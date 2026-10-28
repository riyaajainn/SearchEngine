
import json
import os
import time
import uuid
from pathlib import Path

from opensearchpy import OpenSearch, helpers
from dotenv import load_dotenv
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()

# Configuration
INPUT_FILE = BASE_DIR / "products.jsonl"
CHECKPOINT_FILE = BASE_DIR / "index_checkpoint.json"

INDEX_NAME = "products-v1"
BATCH_SIZE = 500
PROGRESS_INTERVAL = 10_000

client = OpenSearch(
    hosts=[{"host": "localhost", "port": 9200}],
    use_ssl=False,
    verify_certs=False,
    timeout=60,
    max_retries=3,
    retry_on_timeout=True,
)

# Load taxonomy definitions dynamically from MongoDB
TAXONOMY = {}
try:
    mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
    if mongo_uri:
        _mclient = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        _coll = _mclient[os.getenv("MONGO_DB", "fs_graph")]["store_deals_category"]
        for c in _coll.find():
            sid = str(c.get("sub_cat_id"))
            if sid:
                TAXONOMY[sid] = {
                    "sub_cat_name": str(c.get("sub_cat_name", "")).strip(),
                    "cat_name": str(c.get("cat_name", "")).strip(),
                }
        _mclient.close()
        print(f"Loaded {len(TAXONOMY)} taxonomy definitions from MongoDB.")
except Exception as e:
    print(f"Taxonomy loading skipped: {e}")


def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return 0, 0

    with CHECKPOINT_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    offset = data.get("offset", 0)
    line_number = data.get("line_number", 0)

    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid checkpoint offset")

    if not isinstance(line_number, int) or line_number < 0:
        raise ValueError("Invalid checkpoint line number")

    return offset, line_number


def save_checkpoint(offset, line_number):
    """Atomically save checkpoint, retrying Windows file-lock errors."""
    temp_file = CHECKPOINT_FILE.with_name(
        f"index_checkpoint_{uuid.uuid4().hex}.tmp"
    )

    try:
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "offset": offset,
                    "line_number": line_number,
                },
                f,
            )
            f.flush()
            os.fsync(f.fileno())

        for attempt in range(10):
            try:
                os.replace(temp_file, CHECKPOINT_FILE)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(1)

    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def make_action(doc, line_number):
    required_fields = (
        "product_id",
        "title",
        "category",
        "sub_cat_id",
    )

    for field in required_fields:
        value = doc.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Invalid '{field}' on JSONL line {line_number}"
            )

    product_id = doc["product_id"].strip()
    sub_cat_id = doc["sub_cat_id"].strip()

    source = {
        "product_id": product_id,
        "title": doc["title"].strip(),
        "category": doc["category"].strip(),
        "sub_cat_id": sub_cat_id,
    }

    if sub_cat_id in TAXONOMY:
        source["subcategory_name"] = TAXONOMY[sub_cat_id]["sub_cat_name"]
        source["category_name"] = TAXONOMY[sub_cat_id]["cat_name"]

    price = doc.get("price")
    if price is not None:
        try:
            source["price"] = float(price)
        except (ValueError, TypeError):
            pass

    brand = doc.get("brand")
    if brand is not None and isinstance(brand, str) and brand.strip():
        source["brand"] = brand.strip()
    else:
        source["brand"] = None

    return {
        "_op_type": "index",
        "_index": INDEX_NAME,
        "_id": product_id,
        "_source": source,
    }


def index_batch(batch):
    """Index a batch and save its checkpoint only after success."""
    actions = [item["action"] for item in batch]
    errors = []
    successes = 0

    for ok, result in helpers.streaming_bulk(
        client,
        actions,
        chunk_size=BATCH_SIZE,
        max_retries=5,
        initial_backoff=2,
        max_backoff=30,
        raise_on_error=False,
        raise_on_exception=True,
    ):
        if ok:
            successes += 1
        else:
            errors.append(result)

            if len(errors) <= 5:
                print("Indexing error:", result)

    if errors or successes != len(batch):
        raise RuntimeError(
            f"Batch failed: {successes}/{len(batch)} indexed. "
            "Checkpoint was not advanced."
        )

    last_item = batch[-1]

    # All documents in this batch succeeded before checkpointing.
    save_checkpoint(
        last_item["offset"],
        last_item["line_number"],
    )

    return successes


def main():
    if not INPUT_FILE.is_file():
        raise FileNotFoundError(INPUT_FILE)

    if not client.indices.exists(index=INDEX_NAME):
        raise RuntimeError(
            f"Index '{INDEX_NAME}' does not exist. "
            "Run create_index.py first."
        )

    offset, line_number = load_checkpoint()

    file_size = INPUT_FILE.stat().st_size

    if offset > file_size:
        raise ValueError(
            "Checkpoint offset exceeds file size. "
            "Check whether products.jsonl was replaced."
        )

    processed = 0
    succeeded = 0
    batch = []

    print(f"Starting/resuming at line: {line_number:,}")
    print(f"File size: {file_size:,} bytes")

    try:
        with INPUT_FILE.open("rb") as f:
            f.seek(offset)

            while True:
                raw_line = f.readline()

                if not raw_line:
                    break

                current_offset = f.tell()
                current_line = line_number + 1
                line_number = current_line

                if not raw_line.strip():
                    # Preserve progress over blank lines.
                    save_checkpoint(current_offset, current_line)
                    continue

                try:
                    doc = json.loads(raw_line.decode("utf-8"))
                    action = make_action(doc, current_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Invalid JSON/encoding at line {current_line}"
                    ) from exc

                batch.append(
                    {
                        "action": action,
                        "offset": current_offset,
                        "line_number": current_line,
                    }
                )

                if len(batch) >= BATCH_SIZE:
                    succeeded += index_batch(batch)
                    processed += len(batch)
                    batch.clear()

                    if processed % PROGRESS_INTERVAL < BATCH_SIZE:
                        print(
                            f"Indexed this run: {processed:,} | "
                            f"Last line: {line_number:,} | "
                            f"Last ID: checkpoint saved"
                        )

            if batch:
                succeeded += index_batch(batch)
                processed += len(batch)
                batch.clear()

        client.indices.refresh(index=INDEX_NAME)
        client.indices.put_settings(
            index=INDEX_NAME,
            body={"index": {"refresh_interval": "1s"}},
        )

        count = client.count(index=INDEX_NAME)["count"]

        print("\nImport completed successfully.")
        print(f"Indexed this run: {succeeded:,}")
        print(f"Documents currently in index: {count:,}")
        print(f"Last processed line: {line_number:,}")

    except Exception:
        print("\nImport interrupted or failed.")
        print("The last successful batch checkpoint is retained.")
        print("Fix the reported issue and rerun this script.")
        raise

    finally:
        client.close()


if __name__ == "__main__":
    main()

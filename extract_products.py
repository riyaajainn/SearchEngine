
import json
import math
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from config import TARGET_STORE_IDS

load_dotenv()

# MongoDB configuration
MONGODB_URI = os.environ["MONGODB_URI"]
DB_NAME = "fs_graph"
COLLECTION_NAME = "price_graph"

# Output configuration
OUTPUT_FILE = "products.jsonl"
BATCH_SIZE = 5000


def clean_value(val):
    if val is None:
        return None
    if isinstance(val, float):
        if math.isnan(val):
            return None
        if val.is_integer():
            return str(int(val))
        return str(val)
    s = str(val).strip()
    if not s or s.lower() in ("nan", "null", "none"):
        return None
    return s


def main():
    client = MongoClient(
        MONGODB_URI,
        appname="ecommerce-search-extractor",
        serverSelectionTimeoutMS=10000,
    )

    total = 0
    skipped = 0

    try:
        collection = client[DB_NAME][COLLECTION_NAME]

        query = {
            "title": {"$exists": True, "$ne": None},
            "category": {"$exists": True, "$ne": None},
            "sub_cat_id": {"$exists": True, "$ne": None},
            "sid": {"$in": TARGET_STORE_IDS},
        }

        projection = {
            "_id": 1,
            "title": 1,
            "category": 1,
            "sub_cat_id": 1,
            "brand": 1,
            "price": 1,
            "sid": 1,
        }

        cursor = collection.find(
            query,
            projection=projection,
            batch_size=BATCH_SIZE,
        )

        with open(OUTPUT_FILE, "w", encoding="utf-8") as output:
            for doc in cursor:
                title = clean_value(doc.get("title"))
                category = clean_value(doc.get("category"))
                sub_cat_id = clean_value(doc.get("sub_cat_id"))

                if not title or not category or not sub_cat_id:
                    skipped += 1
                    continue

                raw_brand = clean_value(doc.get("brand"))
                brand = str(raw_brand).strip() if (raw_brand and str(raw_brand).strip()) else None

                record = {
                    "product_id": str(doc["_id"]),
                    "title": title,
                    "category": category,
                    "sub_cat_id": sub_cat_id,
                    "brand": brand,
                    "price": clean_value(doc.get("price")),
                    "sid": clean_value(doc.get("sid")),
                }

                output.write(
                    json.dumps(record, ensure_ascii=False) + "\n"
                )

                total += 1

                if total % 10000 == 0:
                    print(f"Exported {total:,} documents (skipped {skipped:,} invalid)")

        print("\nExtraction completed successfully.")
        print(f"Database: {DB_NAME}")
        print(f"Collection: {COLLECTION_NAME}")
        print(f"Total exported: {total:,}")
        print(f"Total skipped: {skipped:,}")
        print(f"Output file: {OUTPUT_FILE}")

    except Exception as exc:
        print(f"Extraction failed: {exc}")
        raise

    finally:
        client.close()


if __name__ == "__main__":
    main()

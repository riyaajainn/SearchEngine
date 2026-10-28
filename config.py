import os
from dotenv import load_dotenv
from opensearchpy import OpenSearch

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv()

# OpenSearch Cluster Configuration
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "").strip() or None
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost").strip()
OPENSEARCH_PORT = int(str(os.getenv("OPENSEARCH_PORT", "9200")).strip())
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "").strip() or None
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "").strip() or None
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").strip().lower() in ("true", "1", "yes")
OPENSEARCH_VERIFY_CERTS = os.getenv("OPENSEARCH_VERIFY_CERTS", "true").strip().lower() in ("true", "1", "yes")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "products-v1").strip()

# Autocomplete Configuration
AUTOCOMPLETE_HOST = os.getenv("AUTOCOMPLETE_HOST", "127.0.0.1").strip()
AUTOCOMPLETE_PORT = int(str(os.getenv("AUTOCOMPLETE_PORT", "8503")).strip())
AUTOCOMPLETE_ENDPOINT_URL = os.getenv("AUTOCOMPLETE_ENDPOINT_URL", f"http://127.0.0.1:{AUTOCOMPLETE_PORT}/autocomplete").strip()

# MongoDB Configuration
_raw_mongo = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "mongodb://read_only:v%3F8lT%21sw%26pu4ec2zaPra@143.110.184.59:27017/?authMechanism=DEFAULT"
MONGO_URI = _raw_mongo.strip()
MONGO_DB = os.getenv("MONGO_DB", "fs_graph").strip()
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "price_graph").strip()
IMAGE_URL_FIELD = os.getenv("IMAGE_URL_FIELD", "imgurl").strip()

# Store Slugs and Display Names
STORE_SLUGS = {
    "1": "flipkart",
    "2": "amazon",
    "22": "amazon",
    "5": "jiomart",
    "6": "tatacliq",
    "7": "myntra",
    "9": "ajio",
    "13": "croma",
    "14": "reliancedigital",
}

STORE_DISPLAY_NAMES = {
    "1": "Flipkart",
    "2": "Amazon",
    "22": "Amazon",
    "5": "JioMart",
    "6": "Tata CLiQ",
    "7": "Myntra",
    "9": "Ajio",
    "13": "Croma",
    "14": "Reliance Digital",
}


def get_opensearch_client(timeout: int = 10, max_retries: int = 2) -> OpenSearch:
    """
    Create and return an OpenSearch client instance using central configuration.
    Supports local instances and Cloud providers (AWS, Aiven, Bonsai).
    """
    if OPENSEARCH_URL:
        return OpenSearch(
            hosts=[OPENSEARCH_URL],
            timeout=timeout,
            max_retries=max_retries,
            retry_on_timeout=True
        )

    client_kwargs = {
        "hosts": [{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        "timeout": timeout,
        "max_retries": max_retries,
        "retry_on_timeout": True
    }

    if OPENSEARCH_USER and OPENSEARCH_PASSWORD:
        client_kwargs["http_auth"] = (OPENSEARCH_USER, OPENSEARCH_PASSWORD)
        client_kwargs["use_ssl"] = True
        client_kwargs["verify_certs"] = OPENSEARCH_VERIFY_CERTS
    elif OPENSEARCH_USE_SSL:
        client_kwargs["use_ssl"] = True
        client_kwargs["verify_certs"] = OPENSEARCH_VERIFY_CERTS

    return OpenSearch(**client_kwargs)

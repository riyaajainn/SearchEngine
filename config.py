import os
from dotenv import load_dotenv
from opensearchpy import OpenSearch

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv()

# OpenSearch Cluster Configuration
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL")
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() in ("true", "1", "yes")
OPENSEARCH_VERIFY_CERTS = os.getenv("OPENSEARCH_VERIFY_CERTS", "true").lower() in ("true", "1", "yes")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "products-v1")

# Autocomplete Configuration
AUTOCOMPLETE_HOST = os.getenv("AUTOCOMPLETE_HOST", "127.0.0.1")
AUTOCOMPLETE_PORT = int(os.getenv("AUTOCOMPLETE_PORT", "8503"))
AUTOCOMPLETE_ENDPOINT_URL = os.getenv("AUTOCOMPLETE_ENDPOINT_URL", f"http://127.0.0.1:{AUTOCOMPLETE_PORT}/autocomplete")


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

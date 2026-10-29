
import sys
from opensearchpy import OpenSearch

INDEX_NAME = "products-v1"

client = OpenSearch(
    hosts=[{"host": "localhost", "port": 9200}],
    use_ssl=False,
    verify_certs=False,
    timeout=30,
)

mapping = {
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 0,
        "refresh_interval": "-1",  # Faster bulk indexing
        "index": {
            "similarity": {
                "default": {
                    "type": "BM25",
                    "b": 0.85,  # Penalizes long, spammy keyword-stuffed titles
                    "k1": 1.2,
                }
            }
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "product_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "fields": {
                    "raw": {
                        "type": "keyword",
                        "ignore_above": 512,
                    }
                },
            },
            "category": {
                "type": "text",
                "fields": {
                    "raw": {"type": "keyword"}
                },
            },
            "sub_cat_id": {"type": "keyword"},
            "subcategory_name": {
                "type": "text",
                "fields": {
                    "raw": {"type": "keyword"}
                },
            },
            "category_name": {
                "type": "text",
                "fields": {
                    "raw": {"type": "keyword"}
                },
            },
            "brand": {
                "type": "text",
                "fields": {
                    "raw": {"type": "keyword"}
                },
            },
            "price": {"type": "float"},
            "sid": {"type": "keyword"},
        },
    },
}

try:
    if client.indices.exists(index=INDEX_NAME):
        print(f"Deleting existing index '{INDEX_NAME}' for fresh setup...")
        client.indices.delete(index=INDEX_NAME)
        print(f"Deleted old index '{INDEX_NAME}'.")

    client.indices.create(index=INDEX_NAME, body=mapping)
    print(f"Created fresh index '{INDEX_NAME}' successfully with updated schema.")

    print("\nIndex health:")
    print(client.cluster.health(index=INDEX_NAME))

finally:
    client.close()

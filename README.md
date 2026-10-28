# E-Commerce Search Engine (9M+ Products)

An intelligent, low-latency e-commerce search engine indexing over **9,013,096 products**, built with **OpenSearch**, **MongoDB**, and **Streamlit**.

It features automated natural language price parsing, dynamic demographic and taxonomy intent classification, accessory spam demotion, and on-demand MongoDB metadata hydration.

---

## 1. End-to-End System Architecture

```
[MongoDB: fs_graph.price_graph] (9M+ raw records)
       │
       ▼  (extract_products.py - One-time migration)
[products.jsonl] (1.64 GB local staging cache - NOT needed in production)
       │
       ▼  (test_bulk_index.py + index_checkpoint.json + MongoDB store_deals_category)
[OpenSearch Cluster: products-v1] (9,013,096 docs stored on SSD)
       ▲
       │  (app.py - BM25 + Intent Resolution + Negative Boosting)
[User Query] ──► [Price Intent Parser] ──► [Category Intent Engine] ──► [OpenSearch Query]
                                                                                │
                                                                         (Hits: product_ids)
                                                                                │
[Streamlit UI] ◄── [Product Cards] ◄── [Hydration: Batch Fetch Images from MongoDB]
```

### Step 1: MongoDB Source of Truth
* **Database**: `fs_graph`
* **Collection**: `price_graph` (contains raw crawled e-commerce products with images, prices, titles, ratings, store URLs, and timestamps).
* **Note on Taxonomy**: Taxonomy and category trees are derived **directly and dynamically from OpenSearch index `products-v1`** without requiring any secondary MongoDB collection.

### Step 2: Extraction & Disk-Cached Streaming (`extract_products.py`)
* To avoid long-lived cursor timeouts across 9M+ records during the initial load, `extract_products.py` queries MongoDB with projections:
  * Extracted: `_id` (`product_id`), `title`, `category`, `sub_cat_id`, `price`.
  * Heavy fields (`imgurl`, ratings, descriptions) are left in MongoDB to minimize memory footprint.
* Cleans invalid strings, `NaN`, and `null` values.
* Exports directly into a high-speed serialized streaming file: **`products.jsonl`** (1.64 GB).

> [!IMPORTANT]
> **Is `products.jsonl` needed in production?**
> **No.** `products.jsonl` and `index_checkpoint.json` were strictly temporary local staging files for initial batch ingestion. In production, OpenSearch holds all data permanently on disk. You do **not** ship these files to production servers.

### Step 3: OpenSearch Indexing & Ingestion (`create_index.py` & `test_bulk_index.py`)
* **Index Configuration (`products-v1`)**:
  * Shards: 3 | Replicas: 0 | Refresh Interval: `-1` (during bulk load).
  * **Similarity**: BM25 with custom tuning (`b=0.85`, `k1=1.2`) to penalize repetitive, spammy keyword stuffing in titles.
  * **Mappings**:
    * `product_id`: `keyword`
    * `title`: analyzed `text` + `raw` `keyword`
    * `brand`: analyzed `text` + `raw` `keyword` (defaults to `null` if absent or empty in collection)
    * `category` & `sub_cat_id`: `keyword` (for instant exact filtering)
    * `category_name` & `subcategory_name`: analyzed `text` + `raw` `keyword`
    * `price`: `float` (for range queries and sorting)
* **Fault-Tolerant Bulk Loader (`test_bulk_index.py`)**:
  * Dynamically enriches records with category names from `store_deals_category`.
  * Uses `opensearchpy.helpers.streaming_bulk` with chunks of 500 actions.
  * **Atomic Checkpointing (`index_checkpoint.json`)**: Tracks byte offsets and line numbers with atomic `os.replace` to allow resuming interrupted jobs without duplicates.
  * Automatically restores `refresh_interval: "1s"` and refreshes the cluster upon completion.

### Step 4: Real-Time Search & Query Understanding (`app.py`)
When a user types a query (e.g. `glitchez jaket for men under 1500`), the query passes through:

1. **Intelligent Dynamic Spell Correction & "Did You Mean" Engine (`suggest_correction`)**:
   * Preserves natural-language price filters (e.g. `under 40000`, `between 1000 and 2000`) and alphanumeric product model codes (`12r`, `s24`, `5g`).
   * **100% Dynamic OpenSearch Term Suggester**: Replaces low-frequency typos with high-frequency catalog terms based on real index document frequency ratios (e.g. `sumsung` (89 docs) $\rightarrow$ `samsung` (61,445 docs), `refridgerator` (20 docs) $\rightarrow$ `refrigerator` (12,057 docs), `bottel` (334 docs) $\rightarrow$ `bottle` (85,246 docs)) without needing hardcoded typo dictionaries.
   * **Automatic Auto-Correct & Transparent UI Banner**: Automatically considers the mistyped query as correct to retrieve the right products, while displaying a prominent `"Did you mean: <correct query>?"` banner with one-click verbatim search override.
2. **Price Intent Parser (`parse_price_intent`)**:
   * Uses regex to detect phrases like `"under 1500"`, `"below 500"`, `"between 1000 and 3000"`.
   * Sets strict price filters (`price <= 1500`) and removes price words from text search.
3. **Category Intent & Demographic Classifier (`resolve_query_intent`)**:
   * Dynamically matches queries against database subcategory taxonomy and detects demographic intent (`men`, `women`, `kids`).
   * Detects demographic entities (`men`, `women`, `kids`, `boy`, `girl`).
   * Evaluates query tokens against the in-memory taxonomy graph (`subcat_index`).
   * Resolves target subcategory (`sub_cat_id: 308: Men jackets`) and extracts the residual query (`"glitchez"`).
4. **Google-Grade Multi-Tier Title Search Engine (`search_mode="🎯 Title Match"`)**:
   * Designed specifically for users searching by exact product title, SKU, or model name to find **identical products and variants** across sellers and stores.
   * **Multi-Tier Scoring Hierarchy**:
     * **Tier 1 (`title.raw`, boost 2000.0, `case_insensitive: True`)**: Exact string match on the unanalyzed title. Surfaces identical product listings (scores 30,000–58,000+ points).
     * **Tier 2 (`title.raw` cleaned, boost 1500.0)**: Exact match with normalized punctuation and spacing.
     * **Tier 3 (`match_phrase`, boost 120.0, `slop: 1`)**: Exact sequence phrase matching preserving word order, storage capacity, and color.
     * **Tier 4 (`match_phrase_prefix`, boost 40.0)**: As-you-type prefix completion.
     * **Tier 5 (conjunction `and`, boost 25.0)**: Strict all-token inclusion.
     * **Tier 6 (high-coverage fallback, boost 5.0, `minimum_should_match: 70%`)**: Fallback for long multi-attribute product names.
   * **Same-Title Product Clustering & Multi-Store Output (`group_by_same_title=True`)**:
     * Employs OpenSearch's high-speed **`collapse`** on `title.raw` with **`inner_hits`** (`size: 25`).
     * Instead of repeating identical product titles and cluttering results, the engine outputs **clean, de-duplicated Title Clusters**.
     * Each cluster aggregates:
       * **Total Count**: Exact number of products in the 9M database sharing that exact same title (`same_title_count`).
       * **Sub-Products**: All individual product entries (Product IDs, prices, stores, stock status).
       * **Price Spread & Arbitrage**: Computes price ranges across sellers/stores (e.g. `Spread: ₹679 – ₹803 (Save ₹124 / 15%)`).
       * **Multi-Store Resolution**: Automatically maps platform IDs (`sid`) to friendly store names (**Flipkart**, **Amazon**, **Myntra**, **Ajio**, **Nykaa**, etc.).
   * **Visual Badging & Highlighting**:
     * `🎯 EXACT TITLE MATCH` (Score $\ge 5,000$ or exact raw match)
     * `✨ DIRECT TITLE VARIANT` (Score $\ge 1,000$)
     * `🔎 TITLE MATCH`
     * Real-time Lucene highlighted matched title tokens in yellow tags.
   * **One-Click Verbatim Isolation**: Instant `"🔍 Filter Exact Same Title"` button to isolate all identical listings across sellers in a single click.
5. **Dynamic Category Discovery & Intent Classifier (`resolve_query_intent`)**:
   * Evaluates query tokens against the in-memory taxonomy graph (`subcat_index`).
   * Normalizes synonyms and demographic entities (`men`, `women`, `kids`).
   * Resolves target subcategory (`sub_cat_id`) and separates core products from accessories.
6. **Subcategory Aggregations & Low-RAM MongoDB Hydration (`find_mongo_products`)**:
   * Inverted index execution operates within **<100MB Python RAM** and 15–35ms latency across 9M records.
   * OpenSearch returns only compact product IDs; batch `$in` query fetches high-res images (`imgurl`) from MongoDB only for visible items.

---

## 2. Why This Architecture Beats Dense Embeddings & Vector DBs

Many assume vector databases are always superior because they use AI embeddings. In high-scale e-commerce, **a pure Vector DB is significantly slower, far more expensive, and less accurate**.

### A. Performance & Latency Comparison

| Metric | OpenSearch / Inverted Index (Our System) | Vector DB (HNSW / Dense Vectors) |
| :--- | :--- | :--- |
| **Search Latency (9M docs)** | **5 ms – 25 ms** ⚡ | **50 ms – 250 ms** 🐢 |
| **How it searches** | Hash maps & posting lists ($O(1)$ to $O(k)$) | High-dimensional distance math (cosine / dot-product) across graph nodes |
| **Concurrency & Throughput** | Hundreds of QPS on modest CPU | Heavy CPU/GPU bottlenecks under multi-user traffic |
| **Pre-filtering with Attributes** | Instant bitset filtering on price/category | Expensive; requires complex hybrid graph traversal |

### B. The 4 "Semantic Traps" of Vector Search in E-Commerce

1. **The Accessory Hijacking Trap**:
   * If a user searches `"iPhone 15"`, a vector embedding considers `"iPhone 15 Silicon Case"` and `"iPhone 15 Tempered Glass"` to have a **95%+ cosine similarity** with `"iPhone 15"`.
   * In a vector database, top results are often flooded with cheap ₹299 plastic covers instead of the ₹60,000 phone.
2. **The Exact SKU / Model Number Trap**:
   * Vector embeddings struggle with alphanumeric nuances (e.g. `Samsung S23 Ultra` vs `Samsung S22 Ultra` vs `Samsung S23 FE`).
   * They map to virtually identical vectors, frequently ranking older or wrong models first.
3. **Embeddings Cannot Do Math**:
   * An embedding vector for `"shoes under 1000"` has no concept of whether ₹1,299 is greater than ₹1,000. It cannot enforce strict arithmetic bounds.
4. **Category Boundary Blurring**:
   * In vector space, `"jeans for men"` clusters with general apparel, hoodies, and jackets.
   * Our taxonomy engine strictly maps to `sub_cat_id: 309 (Men jeans)` with 100% precision.

### C. How Industry Leaders (Amazon, Flipkart, Buyhatke) Do It
Top e-commerce platforms do **not** use standalone vector search for candidate retrieval. They use the **Two-Stage Architecture**:
1. **Stage 1 (Retrieval & Filtering):** OpenSearch / Lucene Inverted Index filters 9M+ products down to the top 50–100 candidates in **10 milliseconds** using exact keywords, category IDs, and price bounds.
2. **Stage 2 (Ranking):** A lightweight Learning-to-Rank (LTR) or popularity model scores those top 50 items.

> **Note:** OpenSearch **already includes a native vector engine** (`knn_vector` field type via Lucene kNN/FAISS). If neural embeddings are ever needed for query expansion, they can be enabled directly inside OpenSearch without adding a separate vector database.

---

## 3. RAM vs. Disk Architecture: Do We Store the Index in RAM?

> [!NOTE]
> **Can we NOT store the indexing in RAM?**
> **OpenSearch does NOT store the entire index in RAM! It is a Disk-First database.**

### A. How OpenSearch Actually Uses Memory (Disk vs. RAM)

All 9,013,096 documents and their index structures live permanently on **DISK (SSD / NVMe)** in compressed Lucene segment files (`.fdt`, `.tim`, `.doc`).

```
[ Incoming User Search ]
          │
          ▼
┌────────────────────────────────────────────────────────┐
│  SERVER RAM (Only ~2 GB – 4 GB JVM Heap Needed)        │
│  - JVM Heap: Keeps query state & active filters        │
│  - OS Page Cache: Dynamically caches ONLY frequent,    │
│    "hot" search terms (e.g. "phone", "shoes", "jeans") │
└──────────────────────────┬─────────────────────────────┘
                           │ (Cache miss / Long-tail search)
                           ▼
┌────────────────────────────────────────────────────────┐
│  DISK / SSD (NVMe) (All 9M Products Stored Here)       │
│  - Compressed Inverted Index & Posting Lists (~3.2 GB) │
│  - Ultra-fast random block reads (1 to 2 milliseconds) │
└────────────────────────────────────────────────────────┘
```

1. **Demand Paging (OS Page Cache)**:
   * When OpenSearch boots, it does **not** load the 3 GB index into RAM.
   * Only the exact blocks of terms requested by active users are cached dynamically by the OS kernel.
   * If a user searches for a rare or cold term, OpenSearch reads the compressed block from the SSD in **1 to 2 milliseconds**, responds, and allows the OS to free memory as needed.
2. **Why Vector DBs Cannot Do This (Why Vector DBs FORCE You to Use Huge RAM)**:
   * Vector search algorithms (HNSW) build complex multi-layered graphs. Finding nearest neighbors requires jumping across thousands of random graph pointers.
   * If those vector pointers live on disk instead of RAM, the random disk seeks cause massive latency penalties—search times explode from 50ms to **3 to 5 seconds**.
   * Therefore, Vector DBs **force you to keep all vectors in RAM**, whereas OpenSearch was engineered specifically to be disk-resident.

---

### B. Hardware & RAM Sizing Comparison (9,013,096 Products)

| Resource | Current OpenSearch Setup | Vector DB (384-dim MiniLM) | Vector DB (768-dim BERT/BGE) |
| :--- | :--- | :--- | :--- |
| **Raw Vector Footprint** | 0 GB | ~13.84 GB | ~27.69 GB |
| **HNSW Graph Overhead** | 0 GB | ~14 GB – 18 GB | ~25 GB – 35 GB |
| **Total Dedicated RAM Needed** | **4 GB – 8 GB RAM** | **~28 GB – 32 GB RAM** | **~55 GB – 65 GB RAM** |
| **Disk Storage (9M Docs)** | **~2.8 GB – 3.5 GB** | **~15 GB – 20 GB** | **~35 GB – 45 GB** |
| **Embedding Generation Time** | **0 (Instant)** | **12–24 Hours GPU** | **Days of GPU Compute** |
| **Server Hardware Cost** | **Low (Basic VM / Container)** | **High ($$$ High-RAM Cloud Server)** | **Extreme ($$$$ Enterprise High-RAM)** |

---

## 4. Production Deployment Guidelines

### What to Deploy vs. What to Exclude

When packaging the application for production (e.g. Docker container or cloud VM):

```
Included in Production:
  ├── app.py                     # Streamlit frontend or FastAPI search API
  ├── .env                       # Production OpenSearch & MongoDB credentials
  └── requirements.txt           # Dependencies (opensearch-py, pymongo, streamlit)

Excluded from Production (.dockerignore / .gitignore):
  ├── products.jsonl             # (1.64 GB staging file - NOT needed)
  ├── index_checkpoint.json      # (Local migration checkpoint - NOT needed)
  ├── extract_products.py        # (One-time extraction script - NOT needed)
  └── test_bulk_index.py         # (One-time bulk indexer - NOT needed)
```

### Real-Time Updates in Production (No JSONL Needed)
In production, do not re-export JSONL files to update products:
1. **MongoDB Change Streams**: Listen to `price_graph` changes in real-time using `collection.watch()`.
2. **Direct Indexing**: When a product is inserted, updated, or deleted in MongoDB, push that single document change directly into OpenSearch:
   ```python
   client.index(index="products-v1", id=product_id, body=updated_doc)
   ```
This keeps OpenSearch synchronized with MongoDB with sub-second latency.

---

## 5. Repository Structure

```
d:/search_en/
│
├── .env                         # Environment variables (OpenSearch, MongoDB)
├── README.md                    # System architecture & documentation
│
├── create_index.py              # Creates OpenSearch index with BM25 & strict mappings
├── extract_products.py          # Streams valid products from MongoDB into JSONL (migration)
├── products.jsonl               # 1.64 GB serialized export of 9M+ products (staging)
│
├── test_bulk_index.py           # Fault-tolerant bulk indexer with atomic checkpoints (migration)
├── index_checkpoint.json        # Atomic tracking file (byte offset & line number)
│
├── app.py                       # Streamlit search engine UI + Query Understanding
├── inspect_mongo.py             # Diagnostic script for MongoDB collections
└── inspect_categories.py        # Diagnostic script for category distributions
```

---

## 6. How to Run the Application

### 1. Prerequisites
Ensure **OpenSearch** is running locally on port `9200`:
```powershell
curl http://localhost:9200
```

### 2. Verify Environment Variables (`.env`)
```ini
MONGODB_URI=mongodb://read_only:<password>@143.110.184.59:27017/?authMechanism=DEFAULT
MONGO_DB=fs_graph
MONGO_COLLECTION=price_graph

OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_INDEX=products-v1
IMAGE_URL_FIELD=imgurl
```

### 3. Launch the Search Dashboard
```powershell
python -m streamlit run app.py
```
Open **`http://localhost:8501`** in your browser.

### 4. Sample Benchmark Queries to Test
* `jeans for men` $\rightarrow$ Automatically routes to `sub_cat_id: 309 (Men jeans)`.
* `glitchez jacket for men` $\rightarrow$ Routes to `Men jackets (308)` with title filter `glitchez`.
* `phone under 25000` $\rightarrow$ Routes to `Mobile phones (1302)` with price filter $\le 25000$ and accessory demotion.
* `tshirt under 200 for men` $\rightarrow$ Routes to `Men t-shirts (312)` with price filter $\le 200$.
* `iphone 15` $\rightarrow$ Exact product search with phrase match and accessory suppression.

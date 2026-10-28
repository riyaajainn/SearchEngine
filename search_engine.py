import re
from bson import ObjectId

from config import OPENSEARCH_INDEX, get_opensearch_client
from nlp_utils import (
    STOPWORDS,
    RE_TOKENS,
    RE_COMPATIBLE,
    clean_and_normalize_text,
    clean_title,
    get_word_variants,
    extract_demographic,
    parse_price_intent,
    query_has_variant_or_color,
    is_compatibility_stuffed,
)
from taxonomy_learner import (
    mine_core_device_vocabulary,
    mine_accessory_vocabulary,
    build_taxonomy as learner_build_taxonomy,
    TaxonomyContext,
)
from query_builder import build_search_query_dsl

# Dynamic active runtime vocabularies mined directly from OpenSearch cluster
DYNAMIC_CORE_DEVICE_WORDS = set()
DYNAMIC_ACCESSORY_KEYWORDS = set()

# Backward-compatible references
CORE_DEVICE_AND_SPEC_WORDS = DYNAMIC_CORE_DEVICE_WORDS
TRUE_ACCESSORY_TOKENS = DYNAMIC_ACCESSORY_KEYWORDS


def build_taxonomy(source, index_name: str = OPENSEARCH_INDEX) -> tuple:
    """
    Dynamically loads master category and subcategory definitions directly from OpenSearch.
    Updates runtime vocabulary sets for high-performance query execution.
    """
    global DYNAMIC_ACCESSORY_KEYWORDS, DYNAMIC_CORE_DEVICE_WORDS
    global CORE_DEVICE_AND_SPEC_WORDS, TRUE_ACCESSORY_TOKENS

    taxonomy, subcat_index = learner_build_taxonomy(source, index_name=index_name)

    if hasattr(source, "search"):
        DYNAMIC_CORE_DEVICE_WORDS = mine_core_device_vocabulary(source, index_name=index_name)
        CORE_DEVICE_AND_SPEC_WORDS = DYNAMIC_CORE_DEVICE_WORDS
        DYNAMIC_ACCESSORY_KEYWORDS, discovered_hosts = mine_accessory_vocabulary(
            source, index_name=index_name, core_device_words=DYNAMIC_CORE_DEVICE_WORDS
        )
        DYNAMIC_CORE_DEVICE_WORDS.update(discovered_hosts)
        TRUE_ACCESSORY_TOKENS = DYNAMIC_ACCESSORY_KEYWORDS

    return taxonomy, subcat_index


def classify_query_intent(query_str: str, accessory_vocab: set = None) -> str:
    """Classify user query intent into 'ACCESSORY' vs 'CORE_PRODUCT'."""
    q_low = query_str.lower()
    if RE_COMPATIBLE.search(q_low):
        return "ACCESSORY"
    vocab = accessory_vocab if accessory_vocab is not None else DYNAMIC_ACCESSORY_KEYWORDS
    tokens = set(RE_TOKENS.findall(q_low))
    if tokens.intersection(vocab):
        return "ACCESSORY"
    return "CORE_PRODUCT"


def resolve_query_intent(query_str: str, taxonomy_index: list) -> dict:
    """Map natural language user query to target category & residual query dynamically."""
    cleaned = clean_and_normalize_text(query_str)
    raw_tokens = RE_TOKENS.findall(cleaned)

    intent_type = classify_query_intent(query_str)

    detected_demo, matched_demo_t = extract_demographic(query_str)
    demo_tokens = {matched_demo_t} if matched_demo_t else set()

    content_tokens = set([t for t in raw_tokens if t not in STOPWORDS])
    expanded_content_tokens = set()
    for t in content_tokens:
        expanded_content_tokens.update(get_word_variants(t))

    best_match = None
    best_score = 0

    for item in taxonomy_index:
        if intent_type == "ACCESSORY" and item.get("is_core_hardware", False):
            continue
        if intent_type == "CORE_PRODUCT" and item.get("is_accessory", False):
            continue

        if detected_demo and item["demographic"] and item["demographic"] != detected_demo:
            continue
        if not detected_demo and item["demographic"] and not any(t in item["all_tokens"] for t in expanded_content_tokens):
            continue

        core_overlap = item["core_tokens"].intersection(expanded_content_tokens)
        if not core_overlap:
            continue

        query_coverage = len(core_overlap) / max(len(content_tokens), 1)
        subcat_coverage = len(core_overlap) / max(len(item["core_tokens"]), 1)
        score = (query_coverage * 7.0 + subcat_coverage * 3.0)
        if detected_demo and item["demographic"] == detected_demo:
            score += 15

        if intent_type == "ACCESSORY" and item["is_accessory"]:
            score += 25
        elif intent_type == "CORE_PRODUCT" and item["is_accessory"]:
            score -= 50

        if score > best_score:
            best_score = score
            best_match = item

    target_subcat = None
    target_subcat_name = None
    matched_q_tokens = set()

    if best_match and best_score >= 6:
        target_subcat = best_match["sub_cat_id"]
        target_subcat_name = best_match["sub_cat_name"]
        for i, nt in enumerate(raw_tokens):
            nt_variants = get_word_variants(nt)
            if nt_variants.intersection(best_match["core_tokens"]) or raw_tokens[i] in demo_tokens:
                matched_q_tokens.add(raw_tokens[i])

    residual_tokens = [t for t in raw_tokens if t not in matched_q_tokens and t not in STOPWORDS]
    residual_query = " ".join(residual_tokens).strip()

    return {
        "target_subcat": target_subcat,
        "target_subcat_name": target_subcat_name,
        "residual_query": residual_query,
        "detected_demo": detected_demo,
        "best_score": best_score,
        "intent_type": intent_type,
    }


def check_words_frequency(os_client, index_name: str, words: list) -> dict:
    """Query OpenSearch aggregations to find document frequency for given tokens."""
    if not words:
        return {}
    aggs = {
        f"cnt_{i}": {
            "filter": {
                "bool": {
                    "should": [
                        {"term": {"title": w.lower()}},
                        {"term": {"brand.raw": {"value": w, "case_insensitive": True}}}
                    ]
                }
            }
        }
        for i, w in enumerate(words)
    }
    try:
        res = os_client.search(index=index_name, body={"size": 0, "aggs": aggs})
        counts = {}
        for i, w in enumerate(words):
            counts[w.lower()] = res.get("aggregations", {}).get(f"cnt_{i}", {}).get("doc_count", 0)
        return counts
    except Exception:
        return {}


def suggest_correction(os_client, query_str: str, index_name: str = OPENSEARCH_INDEX) -> str:
    """Spell-check and term completion suggestion based on live OpenSearch lexicon."""
    if not query_str or len(query_str.strip()) < 3:
        return None

    decomp_query = clean_and_normalize_text(query_str.strip())
    if decomp_query.lower() != query_str.lower().strip():
        return decomp_query

    price_phrases = []
    def price_mask_repl(m):
        price_phrases.append(m.group(0))
        return f"__PRICE_TOKEN_{len(price_phrases)-1}__"

    masked_query = query_str.strip()
    from nlp_utils import PRICE_PATTERNS
    for cp in PRICE_PATTERNS:
        masked_query = cp.sub(price_mask_repl, masked_query)

    tokens = masked_query.split()
    if not tokens:
        return None

    non_price_words = []
    for token in tokens:
        if not (token.startswith("__PRICE_TOKEN_") and token.endswith("__")):
            clean_token = re.sub(r'^[^\w]+|[^\w]+$', '', token).lower()
            if clean_token and not clean_token.isdigit():
                non_price_words.append(clean_token)

    word_counts = check_words_frequency(os_client, index_name, non_price_words)

    corrected_tokens = []
    has_change = False

    for token in tokens:
        if token.startswith("__PRICE_TOKEN_") and token.endswith("__"):
            idx = int(token.replace("__PRICE_TOKEN_", "").replace("__", ""))
            corrected_tokens.append(price_phrases[idx])
            continue

        clean_token = re.sub(r'^[^\w]+|[^\w]+$', '', token).lower()
        if not clean_token or clean_token.isdigit():
            corrected_tokens.append(token)
            continue

        if word_counts.get(clean_token, 0) >= 3:
            corrected_tokens.append(token)
            continue

        suggest_body = {
            "suggest": {
                "spell_check": {
                    "text": clean_token,
                    "term": {
                        "field": "title",
                        "size": 3,
                        "sort": "frequency",
                        "suggest_mode": "popular",
                        "min_word_length": 3,
                        "prefix_length": 1,
                        "max_edits": 2
                    }
                }
            }
        }
        try:
            res = os_client.search(index=index_name, body=suggest_body)
            suggestions = res.get("suggest", {}).get("spell_check", [])
            options = suggestions[0].get("options", []) if suggestions else []
            if options and options[0].get("score", 0) >= 0.7:
                best_suggest = options[0]["text"]
                corrected_tokens.append(best_suggest)
                has_change = True
            else:
                corrected_tokens.append(token)
        except Exception:
            corrected_tokens.append(token)

    if has_change:
        cand = " ".join(corrected_tokens).strip()
        cand = re.sub(r'\s+', ' ', cand)
        return cand
    return None


def assign_hit_metadata(hit_obj: dict, query: str, clean_q: str):
    """Assign match badges, highlight formatting, and same-title collapsed product metadata."""
    src_map = hit_obj.get("_source", {})
    hl_map = hit_obj.get("highlight", {}).get("title", [])
    src_map["highlighted_title"] = hl_map[0] if hl_map else src_map.get("title", "")
    raw_t = (src_map.get("title") or "").strip().lower()
    clean_target = clean_q.strip().lower()
    query_target = query.strip().lower()
    hit_sc = hit_obj.get("_score", 0)

    if raw_t == query_target or raw_t == clean_target or hit_sc >= 5000:
        src_map["match_badge"] = "🎯 EXACT TITLE MATCH"
        src_map["match_badge_color"] = "#15803d"
        src_map["match_badge_bg"] = "#dcfce7"
    elif hit_sc >= 1000 or clean_target in raw_t:
        src_map["match_badge"] = "✨ DIRECT TITLE VARIANT"
        src_map["match_badge_color"] = "#0369a1"
        src_map["match_badge_bg"] = "#e0f2fe"
    else:
        src_map["match_badge"] = "🔎 TITLE MATCH"
        src_map["match_badge_color"] = "#475569"
        src_map["match_badge_bg"] = "#f1f5f9"

    inner_info = hit_obj.get("inner_hits", {}).get("same_title_listings", {}).get("hits", {})
    inner_docs = inner_info.get("hits", [])
    total_cnt = inner_info.get("total", {}).get("value", len(inner_docs))
    src_map["same_title_count"] = max(total_cnt, 1)
    src_map["same_title_products"] = [
        {
            "product_id": str(ih.get("_source", {}).get("product_id", "")),
            "price": ih.get("_source", {}).get("price"),
            "sub_cat_id": str(ih.get("_source", {}).get("sub_cat_id", "")),
            "title": ih.get("_source", {}).get("title", ""),
            "score": ih.get("_score", 0),
        }
        for ih in inner_docs
    ]


def post_process_search_hits(
    raw_hits: list,
    query: str,
    clean_q: str,
    q_tokens: list,
    is_accessory_intent: bool,
    taxonomy: dict,
    core_device_subcats: set,
    unwanted_acc: list,
    effective_subcat: str,
    detected_demo: str,
    limit: int
) -> list:
    """Filter, disambiguate, and attach metadata to raw search hits."""
    filtered_hits = []

    if is_accessory_intent:
        acc_title_tokens = DYNAMIC_ACCESSORY_KEYWORDS
        q_acc_tokens = {t for t in q_tokens if t in DYNAMIC_ACCESSORY_KEYWORDS}

        for hit in raw_hits:
            src = hit.get("_source", {})
            sub_id = str(src.get("sub_cat_id", ""))
            t_lower = (src.get("title") or "").lower()
            t_words = set(RE_TOKENS.findall(t_lower))

            if not t_words.intersection(acc_title_tokens):
                if sub_id in core_device_subcats or not re.search(r'\b(?:for|compatible with|suitable for)\b', t_lower):
                    continue

            if q_acc_tokens and not t_words.intersection(q_acc_tokens):
                continue

            if q_acc_tokens and re.search(r'\b(?:for|with)\b', t_lower):
                prefix_words = set(re.split(r'\b(?:for|with)\b', t_lower)[0].split())
                if prefix_words.intersection(DYNAMIC_ACCESSORY_KEYWORDS - q_acc_tokens):
                    continue

            if re.search(r'\b(?:for|compatible with|suitable for)\b', t_lower) or t_words.intersection(acc_title_tokens):
                assign_hit_metadata(hit, query, clean_q)
                filtered_hits.append(hit)
                if len(filtered_hits) >= limit:
                    break
    else:
        unwanted_acc_set = set(unwanted_acc)
        for hit in raw_hits:
            src = hit.get("_source", {})
            sub_id = str(src.get("sub_cat_id", ""))
            subcat_info = taxonomy.get(sub_id, {})
            sub_name = (subcat_info.get("sub_cat_name") or src.get("subcategory_name") or "").lower()
            t_lower = (src.get("title") or "").lower()

            if subcat_info.get("is_accessory", False) or "accessories" in sub_name or "accessories" in t_lower or "accessory" in t_lower:
                continue

            if effective_subcat and sub_id != str(effective_subcat):
                continue

            t_tokens = set(RE_TOKENS.findall(t_lower))
            if t_tokens.intersection(unwanted_acc_set):
                continue
            if RE_COMPATIBLE.search(t_lower):
                continue

            # Multi-model compatibility stuffing check
            if is_compatibility_stuffed(t_lower, q_tokens):
                continue

            sub_demo = subcat_info.get("demographic")
            if detected_demo and sub_demo and sub_demo != detected_demo:
                continue

            assign_hit_metadata(hit, query, clean_q)
            filtered_hits.append(hit)
            if len(filtered_hits) >= limit:
                break

    return filtered_hits


def normalize_scores_and_badges(filtered_hits: list, clean_q: str):
    """Normalize raw OpenSearch scores to standard 0-100% scale."""
    max_score = max([h.get("_score") or 0 for h in filtered_hits], default=0)
    has_variant_spec = query_has_variant_or_color(clean_q)

    if max_score > 0:
        for h in filtered_hits:
            raw_s = h.get("_score") or 0
            norm_s = round((raw_s / max_score) * 100, 1)
            badge = h.get("_source", {}).get("match_badge")
            if not has_variant_spec and badge == "🎯 EXACT TITLE MATCH" and norm_s >= 92.0:
                norm_s = 100.0
            h["raw_score"] = raw_s
            h["_score"] = norm_s
            for item in h.get("_source", {}).get("same_title_products", []):
                item_raw = item.get("score") or 0
                item_norm = round((item_raw / max_score) * 100, 1)
                if not has_variant_spec and badge == "🎯 EXACT TITLE MATCH" and item_norm >= 92.0:
                    item_norm = 100.0
                item["score"] = item_norm


def search_products(
    os_client,
    query: str,
    limit: int = 50,
    page: int = 1,
    min_price: float = None,
    max_price: float = None,
    taxonomy: dict = None,
    sort_by: str = "relevance",
    client_id: str = None,
    group_by_same_title: bool = True,
    index_name: str = OPENSEARCH_INDEX,
    taxonomy_index: list = None,
    **kwargs
) -> tuple:
    """
    Production product search engine executing multi-phase semantic & lexical matching over OpenSearch.
    Zero hardcoded models, brands, or categories.
    """
    if taxonomy is None:
        taxonomy = {}

    clean_q, parsed_min_p, parsed_max_p = parse_price_intent(query)
    effective_min_p = min_price if min_price is not None else parsed_min_p
    effective_max_p = max_price if max_price is not None else parsed_max_p

    if taxonomy_index is None:
        taxonomy_index = list(taxonomy.values())
    intent = resolve_query_intent(clean_q, taxonomy_index)
    core_query = intent.get("residual_query", "")
    target_subcat = intent.get("target_subcat")
    detected_demo = intent.get("detected_demo")
    intent_type = intent.get("intent_type", "CORE_PRODUCT")

    q_tokens = [t.lower() for t in RE_TOKENS.findall(clean_q) if t not in STOPWORDS]

    accessory_subcats = [k for k, v in taxonomy.items() if v.get("is_accessory", False)]
    core_device_subcats = set([k for k, v in taxonomy.items() if v.get("is_core_hardware", False)])

    is_accessory_intent = (intent_type == "ACCESSORY")

    target_subcat_info = taxonomy.get(target_subcat, {})
    if target_subcat_info.get("is_accessory", False):
        is_accessory_intent = True

    effective_subcat = target_subcat if (target_subcat_info.get("is_accessory", False) or intent.get("best_score", 0) >= 18) else None

    unwanted_acc = []
    if not is_accessory_intent:
        for acc in DYNAMIC_ACCESSORY_KEYWORDS:
            if acc not in q_tokens and len(acc) >= 3:
                unwanted_acc.append(acc)

    is_core_hardware = bool(taxonomy.get(effective_subcat, {}).get("is_core_hardware", False) or not effective_subcat)

    final_query = build_search_query_dsl(
        clean_q=clean_q,
        q_tokens=q_tokens,
        core_query=core_query,
        target_subcat=target_subcat,
        detected_demo=detected_demo,
        is_accessory_intent=is_accessory_intent,
        accessory_subcats=accessory_subcats,
        core_device_subcats=list(core_device_subcats),
        effective_min_p=effective_min_p,
        effective_max_p=effective_max_p,
        unwanted_acc=unwanted_acc,
        is_core_hardware=is_core_hardware,
    )

    sort_criteria = ["_score"]
    if sort_by == "Price: Low to High":
        sort_criteria = [{"price": {"order": "asc"}}, "_score"]
    elif sort_by == "Price: High to Low":
        sort_criteria = [{"price": {"order": "desc"}}, "_score"]

    fetch_multiplier = 4 if group_by_same_title else 3
    body = {
        "size": max(limit * fetch_multiplier, 30),
        "query": final_query,
        "sort": sort_criteria,
        "highlight": {
            "pre_tags": ["<mark style='background:#fef08a;padding:1px 3px;border-radius:3px;'>"],
            "post_tags": ["</mark>"],
            "fields": {
                "title": {"number_of_fragments": 0}
            }
        },
        "_source": [
            "id", "product_id", "title", "price", "mrp", "discount",
            "category", "category_name", "sub_cat_id", "subcategory_name",
            "brand", "sid", "url", "rating"
        ]
    }

    if group_by_same_title:
        body["collapse"] = {
            "field": "title.raw",
            "inner_hits": {
                "name": "same_title_listings",
                "size": 5,
                "sort": [{"price": "asc"}],
                "_source": ["id", "product_id", "price", "sub_cat_id", "sid", "url", "title"]
            }
        }

    raw_res = os_client.search(index=index_name, body=body)

    raw_hits = raw_res.get("hits", {}).get("hits", [])
    filtered_hits = post_process_search_hits(
        raw_hits=raw_hits,
        query=query,
        clean_q=clean_q,
        q_tokens=q_tokens,
        is_accessory_intent=is_accessory_intent,
        taxonomy=taxonomy,
        core_device_subcats=core_device_subcats,
        unwanted_acc=unwanted_acc,
        effective_subcat=effective_subcat,
        detected_demo=detected_demo,
        limit=limit,
    )

    normalize_scores_and_badges(filtered_hits, clean_q)

    raw_res["hits"]["hits"] = filtered_hits
    return raw_res, clean_q, effective_min_p, effective_max_p, intent


# ---------------- MongoDB Batch Fetcher & Helpers ----------------

def find_mongo_products(collection, product_ids: list, image_url_field: str = "imgurl") -> dict:
    """Fetch product enrichment metadata directly from MongoDB in batches."""
    object_ids = []
    string_ids = []

    for product_id in product_ids:
        if not isinstance(product_id, str):
            continue
        product_id = product_id.strip()
        if not product_id:
            continue
        string_ids.append(product_id)
        if re.fullmatch(r"[0-9a-fA-F]{24}", product_id):
            object_ids.append(ObjectId(product_id))

    candidates = list(dict.fromkeys(object_ids + string_ids))
    if not candidates:
        return {}

    projection = {image_url_field: 1, "title": 1, "price": 1, "sid": 1, "pid": 1, "stock": 1, "brand": 1, "canonical_url": 1}
    documents = collection.find({"_id": {"$in": candidates}}, projection)
    return {str(doc["_id"]): doc for doc in documents}


def get_nested_value(document, dotted_path: str):
    """Safely traverse and extract nested dictionary keys."""
    value = document
    for part in dotted_path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None

    if isinstance(value, list):
        if not value:
            return None
        first = value[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            for key in ("url", "imgurl", "image_url", "src"):
                if first.get(key):
                    return first[key]
        return None

    if isinstance(value, dict):
        for key in ("url", "imgurl", "image_url", "src"):
            if value.get(key):
                return value[key]
        return None

    return value if isinstance(value, str) else None

import re
from collections import Counter
from nlp_utils import (
    STOPWORDS,
    RE_TOKENS,
    get_word_variants,
    extract_demographic,
)


def mine_core_device_vocabulary(os_client, index_name: str = "products-v1") -> set:
    """
    Dynamically mines core product nouns from non-accessory subcategories across OpenSearch.
    Zero hardcoded dictionaries.
    """
    learned = set()
    if not hasattr(os_client, "search"):
        return learned
    try:
        res = os_client.search(
            index=index_name,
            body={
                "size": 0,
                "aggs": {"subcats": {"terms": {"field": "subcategory_name.raw", "size": 200}}}
            }
        )
        for b in res.get("aggregations", {}).get("subcats", {}).get("buckets", []):
            sc = b["key"].lower()
            if "accessor" not in sc:
                for w in re.findall(r'[a-z]{2,}', sc):
                    if w not in ("and", "the", "for", "men", "women", "kids", "boys", "girls", "with"):
                        learned.add(w)
    except Exception:
        pass
    return learned


def mine_accessory_vocabulary(os_client, index_name: str = "products-v1", core_device_words: set = None) -> tuple:
    """
    Data-driven Category & Positional Grammar Learner:
    Learns accessory nouns versus target host devices dynamically using directional preposition ratios
    and category odds ratios. Zero hardcoding.
    Returns: (learned_accessories: set, discovered_host_devices: set)
    """
    learned = set()
    host_devices = set()
    if not hasattr(os_client, "search"):
        return learned, host_devices
    try:
        # 1. Sample from accessory subcategories
        res_acc = os_client.search(
            index=index_name,
            body={
                "size": 800,
                "_source": ["title"],
                "query": {"wildcard": {"subcategory_name.raw": "*accessori*"}}
            }
        )
        acc_titles = [h["_source"]["title"].lower() for h in res_acc.get("hits", {}).get("hits", []) if "title" in h.get("_source", {})]

        # 2. Sample from non-accessory core subcategories
        res_core = os_client.search(
            index=index_name,
            body={
                "size": 800,
                "_source": ["title"],
                "query": {"bool": {"must_not": [{"wildcard": {"subcategory_name.raw": "*accessori*"}}]}}
            }
        )
        core_titles = [h["_source"]["title"].lower() for h in res_core.get("hits", {}).get("hits", []) if "title" in h.get("_source", {})]

        before_for = Counter()
        after_for = Counter()
        for t in acc_titles:
            parts = re.split(r'\b(?:for|compatible with|suitable for|fit for|designed for)\b', t, maxsplit=1)
            if len(parts) == 2:
                for w in set(re.findall(r'[a-z]{3,}', parts[0])):
                    if w not in STOPWORDS:
                        before_for[w] += 1
                for w in set(re.findall(r'[a-z]{3,}', parts[1])):
                    if w not in STOPWORDS:
                        after_for[w] += 1
            else:
                for w in set(re.findall(r'[a-z]{3,}', t)):
                    if w not in STOPWORDS:
                        before_for[w] += 1

        core_counts = Counter()
        for t in core_titles:
            for w in set(re.findall(r'[a-z]{3,}', t)):
                if w not in STOPWORDS:
                    core_counts[w] += 1

        n_acc = max(len(acc_titles), 1)
        n_core = max(len(core_titles), 1)

        # Host devices predominantly follow 'for' with high frequency (e.g. for iphone, for samsung, for realme)
        host_devices = {w for w, caft in after_for.items() if caft >= 4 and (caft / (before_for.get(w, 0) + 1)) >= 3.0}

        known_core = set(core_device_words or set()).union(host_devices)

        for w, cbef in before_for.items():
            if cbef >= 3:
                cc = core_counts.get(w, 0)
                odds = (cbef / n_acc) / ((cc + 0.5) / n_core)
                prep_ratio = cbef / (after_for.get(w, 0) + 1)
                if (odds >= 1.5 or prep_ratio >= 2.0 or cbef >= 20) and w not in host_devices and w not in known_core and len(w) >= 3:
                    learned.add(w)

        learned.difference_update(host_devices)
        learned.difference_update(known_core)
        return learned, host_devices
    except Exception:
        return learned, host_devices


class TaxonomyContext:
    """Thread-safe catalog taxonomy context holding precomputed lookup indices."""
    def __init__(self, taxonomy: dict, subcat_index: list, core_words: set, accessory_keywords: set):
        self.taxonomy = taxonomy
        self.subcat_index = subcat_index
        self.core_words = core_words
        self.accessory_keywords = accessory_keywords


def build_taxonomy(source, index_name: str = "products-v1") -> tuple:
    """
    Dynamically load master category and subcategory definitions directly from OpenSearch.
    Pre-computes lookup sets for high-performance query execution.
    """
    core_words = set()
    acc_keywords = set()

    if hasattr(source, "search"):
        core_words = mine_core_device_vocabulary(source, index_name=index_name)
        acc_keywords, discovered_hosts = mine_accessory_vocabulary(source, index_name=index_name, core_device_words=core_words)
        core_words.update(discovered_hosts)

    taxonomy = {}
    subcat_index = []

    items = []
    if hasattr(source, "search"):
        body = {
            "size": 0,
            "aggs": {
                "categories": {
                    "terms": {"field": "category.keyword", "size": 100},
                    "aggs": {
                        "cat_names": {
                            "terms": {"field": "category_name.raw", "size": 1},
                            "aggs": {
                                "subcategories": {
                                    "terms": {"field": "sub_cat_id.keyword", "size": 200},
                                    "aggs": {
                                        "sub_names": {
                                            "terms": {"field": "subcategory_name.raw", "size": 1}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        try:
            res = source.search(index=index_name, body=body)
            buckets = res.get("aggregations", {}).get("categories", {}).get("buckets", [])
            for c_b in buckets:
                cat_id = c_b.get("key")
                cn_buckets = c_b.get("cat_names", {}).get("buckets", [])
                cat_name = cn_buckets[0].get("key") if cn_buckets else str(cat_id)
                sc_buckets = cn_buckets[0].get("subcategories", {}).get("buckets", []) if cn_buckets else []
                for s_b in sc_buckets:
                    sub_id = s_b.get("key")
                    sn_buckets = s_b.get("sub_names", {}).get("buckets", [])
                    sub_name = sn_buckets[0].get("key") if sn_buckets else str(sub_id)
                    items.append({
                        "category": cat_id,
                        "category_name": cat_name,
                        "sub_cat_id": sub_id,
                        "subcategory_name": sub_name
                    })
        except Exception:
            pass

    for item in items:
        sub_id = str(item.get("sub_cat_id", ""))
        sub_name = str(item.get("subcategory_name", "")).strip()
        cat_id = str(item.get("category", ""))
        cat_name = str(item.get("category_name", "")).strip()

        if not sub_id or not sub_name:
            continue

        raw_sub = sub_name.lower()
        sub_tokens = set(RE_TOKENS.findall(raw_sub))
        core_tokens = set([t for t in sub_tokens if t not in STOPWORDS])
        all_tokens = set(sub_tokens)

        for t in list(sub_tokens):
            all_tokens.update(get_word_variants(t))

        for t in list(core_tokens):
            core_tokens.update(get_word_variants(t))

        detected_demo, _ = extract_demographic(sub_name)

        is_acc = (
            "accessori" in raw_sub
            or "accessory" in raw_sub
            or bool(core_tokens.intersection(acc_keywords))
        )
        is_core_hardware = (
            not is_acc
            and (
                "mobile" in raw_sub
                or "phone" in raw_sub
                or "laptop" in raw_sub
                or "television" in raw_sub
                or "tv" in raw_sub
                or "refrigerator" in raw_sub
                or "washing machine" in raw_sub
                or "air conditioner" in raw_sub
                or "tablet" in raw_sub
                or bool(core_tokens.intersection(core_words))
            )
        )

        entry = {
            "cat_id": cat_id,
            "cat_name": cat_name,
            "sub_cat_id": sub_id,
            "sub_cat_name": sub_name,
            "core_tokens": core_tokens,
            "all_tokens": all_tokens,
            "demographic": detected_demo,
            "is_accessory": is_acc,
            "is_core_hardware": is_core_hardware,
        }

        taxonomy[sub_id] = entry
        subcat_index.append(entry)

    return taxonomy, subcat_index

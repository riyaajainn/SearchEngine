def build_search_query_dsl(
    clean_q: str,
    q_tokens: list,
    core_query: str,
    target_subcat: str,
    detected_demo: str,
    is_accessory_intent: bool,
    accessory_subcats: list,
    core_device_subcats: list,
    effective_min_p: float,
    effective_max_p: float,
    unwanted_acc: list,
    is_core_hardware: bool = False
) -> dict:
    """
    Constructs the OpenSearch bool boosting query DSL for product search.
    Separates positive core matching clauses from relational accessory demotion clauses.
    """
    must_clauses = []
    filter_clauses = []
    should_clauses = []
    negative_clauses = []

    # 1. Must Clause: query_string with AND operator
    must_clauses.append({
        "query_string": {
            "query": clean_q,
            "default_operator": "AND",
            "fields": ["title^5", "title.raw^10"]
        }
    })

    # 2. Filters (Price boundaries)
    if effective_min_p is not None:
        filter_clauses.append({"range": {"price": {"gte": effective_min_p}}})
    if effective_max_p is not None:
        filter_clauses.append({"range": {"price": {"lte": effective_max_p}}})

    # 3. Should Clauses (Relevance boosts)
    should_clauses.append({"match_phrase": {"title": {"query": clean_q, "boost": 20.0}}})
    should_clauses.append({"match_phrase_prefix": {"title": {"query": clean_q, "boost": 10.0}}})

    if q_tokens:
        should_clauses.append({
            "span_first": {
                "match": {"span_term": {"title": q_tokens[0]}},
                "end": 3,
                "boost": 15.0
            }
        })
        if len(q_tokens) >= 2:
            should_clauses.append({
                "span_near": {
                    "clauses": [{"span_term": {"title": t}} for t in q_tokens[:3]],
                    "slop": 1,
                    "in_order": True,
                    "boost": 25.0
                }
            })

    if core_query and core_query != clean_q:
        should_clauses.append({"match_phrase": {"title": {"query": core_query, "boost": 8.0}}})

    if target_subcat:
        should_clauses.append({"term": {"sub_cat_id": {"value": target_subcat, "boost": 4.0}}})

    if detected_demo:
        should_clauses.append({"match": {"title": {"query": detected_demo, "boost": 3.0}}})

    # 4. Intent-Driven Negative Boosting
    if is_accessory_intent:
        if core_device_subcats:
            negative_clauses.append({"terms": {"sub_cat_id": core_device_subcats}})
    else:
        if accessory_subcats:
            negative_clauses.append({"terms": {"sub_cat_id": accessory_subcats}})

        target_term = core_query or clean_q
        negative_clauses.append({"match_phrase": {"title": f"for {target_term}"}})
        negative_clauses.append({"match_phrase": {"title": f"compatible with {target_term}"}})
        negative_clauses.append({"match_phrase": {"title": f"suitable for {target_term}"}})
        negative_clauses.append({"match_phrase": {"title": f"designed for {target_term}"}})

        if q_tokens:
            negative_clauses.append({
                "span_near": {
                    "clauses": [
                        {"span_term": {"title": "for"}},
                        {"span_term": {"title": q_tokens[0]}}
                    ],
                    "slop": 3,
                    "in_order": True
                }
            })

        if unwanted_acc:
            negative_clauses.append({
                "match": {
                    "title": {
                        "query": " ".join(unwanted_acc),
                        "boost": 4.0,
                    }
                }
            })

    search_query = {
        "boosting": {
            "positive": {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses,
                    "should": should_clauses,
                }
            },
            "negative": {
                "bool": {
                    "should": negative_clauses
                }
            },
            "negative_boost": 0.01,
        }
    }

    # Price Gaussian Decay for high-end core products
    if effective_max_p is not None and effective_max_p >= 2000 and is_core_hardware:
        final_query = {
            "function_score": {
                "query": search_query,
                "functions": [
                    {
                        "gauss": {
                            "price": {
                                "origin": effective_max_p * 0.85,
                                "scale": effective_max_p * 0.5,
                                "decay": 0.5
                            }
                        },
                        "weight": 2.0
                    }
                ],
                "score_mode": "sum",
                "boost_mode": "multiply"
            }
        }
    else:
        final_query = search_query

    return final_query


def build_autocomplete_dsl(raw_q: str, tokens: list, user_wants_accessory: bool) -> dict:
    """Constructs the OpenSearch bool query DSL for live keystroke autocomplete."""
    if len(tokens) == 1:
        qs = f"{tokens[0]}*"
    else:
        qs = " ".join(tokens[:-1]) + f" {tokens[-1]}*"

    should_clauses = [
        {"span_first": {"match": {"span_term": {"title": tokens[0].lower()}}, "end": 3, "boost": 500.0}},
        {"match_phrase_prefix": {"title": {"query": raw_q, "boost": 200.0}}}
    ]

    bool_query = {
        "must": {
            "query_string": {
                "default_field": "title",
                "query": qs,
                "default_operator": "AND"
            }
        },
        "should": should_clauses
    }

    if not user_wants_accessory:
        bool_query["must_not"] = [{"wildcard": {"subcategory_name.raw": "*accessori*"}}]
    else:
        bool_query["filter"] = [{"wildcard": {"subcategory_name.raw": "*accessori*"}}]

    return {
        "size": 60,
        "_source": ["title", "subcategory_name", "category_name"],
        "query": {
            "bool": bool_query
        }
    }

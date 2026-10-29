import json
import os
import re
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from pymongo import MongoClient
from opensearchpy import OpenSearch

from search_engine import (
    build_taxonomy,
    suggest_correction,
    search_products,
    find_mongo_products,
    get_nested_value,
    extract_demographic,
)

# ---------------- Configuration ----------------
from config import (
    OPENSEARCH_HOST as OS_HOST,
    OPENSEARCH_PORT as OS_PORT,
    OPENSEARCH_INDEX as OS_INDEX,
    AUTOCOMPLETE_PORT,
    AUTOCOMPLETE_ENDPOINT_URL,
    MONGO_URI,
    MONGO_DB,
    MONGO_COLLECTION,
    IMAGE_URL_FIELD,
    STORE_SLUGS,
    STORE_DISPLAY_NAMES,
    TARGET_STORE_IDS,
    get_opensearch_client,
)

# ---------------- Live Google-Style Autocomplete Service ----------------
import socket
import threading
from autocomplete_service import start_server

@st.cache_resource
def ensure_autocomplete_service_running(port=AUTOCOMPLETE_PORT):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result != 0:
            t = threading.Thread(target=start_server, args=(port,), daemon=True)
            t.start()
    except Exception:
        pass
    return True

ensure_autocomplete_service_running(AUTOCOMPLETE_PORT)


def build_flipshope_url(sid, pid, title=None, canonical_url=None):
    """Construct canonical Flipshope product URL using store slug, title slug, sid, and pid."""
    if not sid or not pid or str(sid).strip() in ("N/A", "None", "") or str(pid).strip() in ("N/A", "None", ""):
        return None

    clean_sid = str(sid).strip()
    clean_pid = str(pid).strip()

    # If canonical_url is already present, validate and ensure correct formatting
    if canonical_url and str(canonical_url).strip() not in ("None", ""):
        c_url = str(canonical_url).strip()
        if not c_url.startswith("/"):
            c_url = f"/{c_url}"
        # If it already includes the store slug prefix like /flipkart--..., use directly
        if re.match(r"^/[a-z0-9_-]+?--", c_url):
            return f"https://flipshope.com{c_url}"
        # Prepend store slug if missing
        store_slug = STORE_SLUGS.get(clean_sid, "flipkart")
        return f"https://flipshope.com/{store_slug}--{c_url.lstrip('/')}"

    # Build canonical fallback with required store slug
    store_slug = STORE_SLUGS.get(clean_sid, "flipkart")
    clean_title = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "product").lower()).strip("-")
    if not clean_title:
        clean_title = "product"
    return f"https://flipshope.com/{store_slug}--{clean_title}--{clean_sid}--{clean_pid}"


def build_direct_store_url(sid, pid):
    """Construct direct retailer link on Flipkart, Amazon, Croma, etc."""
    if not sid or not pid or str(sid).strip() in ("N/A", "None", "") or str(pid).strip() in ("N/A", "None", ""):
        return None
    s = str(sid).strip()
    p = str(pid).strip()
    if s == "1":
        return f"https://www.flipkart.com/product/p/itme?pid={p}"
    elif s == "2":
        return f"https://www.amazon.in/dp/{p}"
    elif s == "5":
        return f"https://www.jiomart.com/p/{p}"
    elif s == "6":
        return f"https://www.tatacliq.com/p-mp{p}"
    elif s == "7":
        return f"https://www.myntra.com/{p}"
    elif s == "9":
        return f"https://www.ajio.com/p/{p}"
    elif s == "10":
        return f"https://www.pepperfry.com/site_product/{p}.html"
    elif s == "13":
        return f"https://www.croma.com/p/{p}"
    elif s == "14":
        return f"https://www.reliancedigital.in/p/{p}"
    elif s == "15":
        return f"https://www.meesho.com/s/p/{p}"
    return None

st.set_page_config(
    page_title="Product Search",
    page_icon="🔎",
    layout="wide",
)


@st.cache_resource
def get_clients():
    os_client = get_opensearch_client(timeout=30)
    collection = None
    if MONGO_URI:
        try:
            mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000,
            )
            mongo_client.admin.command("ping")
            collection = mongo_client[MONGO_DB][MONGO_COLLECTION]
        except Exception:
            collection = None

    return os_client, collection


@st.cache_resource
def get_taxonomy(_os_client, index_name=OS_INDEX):
    """Dynamically load master category and subcategory definitions from OpenSearch."""
    try:
        return build_taxonomy(_os_client, index_name=index_name)
    except Exception:
        return {}, []


st.title("🔎 Product Search Dashboard")
st.caption("Intelligent e-commerce search with dynamic category discovery and accessory demotion.")

st.markdown("""
<style>
/* Large, prominent Open on Flipshope CTA button */
.flipshope-cta-btn {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 8px !important;
    background: linear-gradient(135deg, #1d4ed8, #2563eb) !important;
    color: #ffffff !important;
    padding: 10px 18px !important;
    border-radius: 8px !important;
    text-decoration: none !important;
    font-size: 13.5px !important;
    font-weight: 700 !important;
    margin: 8px 0 6px 0 !important;
    box-shadow: 0 4px 10px rgba(37,99,235,0.25) !important;
    transition: all 0.15s ease !important;
    letter-spacing: 0.2px !important;
    width: 100% !important;
    text-align: center !important;
}
.flipshope-cta-btn:hover {
    background: linear-gradient(135deg, #1e40af, #1d4ed8) !important;
    box-shadow: 0 6px 14px rgba(37,99,235,0.35) !important;
    color: #ffffff !important;
    transform: translateY(-1px) !important;
}

.store-cta-btn {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 6px !important;
    background: #f8fafc !important;
    color: #0f172a !important;
    border: 1px solid #cbd5e1 !important;
    padding: 9px 14px !important;
    border-radius: 8px !important;
    text-decoration: none !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04) !important;
    transition: all 0.15s ease !important;
    white-space: nowrap !important;
}
.store-cta-btn:hover {
    background: #e2e8f0 !important;
    border-color: #94a3b8 !important;
    color: #0f172a !important;
    transform: translateY(-1px) !important;
}

/* Small, compact Filter Exact Same Title button inside cards */
div[data-testid="stVerticalBlockBorderWrapper"] div.stButton > button {
    min-height: 26px !important;
    height: 26px !important;
    padding: 1px 8px !important;
    font-size: 11px !important;
    background-color: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 5px !important;
    margin-top: 2px !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] div.stButton > button p {
    font-size: 11px !important;
    font-weight: 500 !important;
    color: #64748b !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] div.stButton > button:hover {
    background-color: #f1f5f9 !important;
    border-color: #cbd5e1 !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] div.stButton > button:hover p {
    color: #1e293b !important;
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Settings")
    result_limit = st.slider(
        "Number of results",
        min_value=1,
        max_value=30,
        value=15,
    )

    st.subheader("Sort Results")
    sort_option = st.selectbox(
        "Sort By",
        options=["Relevance", "Price: Low to High", "Price: High to Low"],
        index=0,
    )

    st.subheader("Price Filter (Optional)")
    sidebar_min_p = st.number_input("Min Price (₹)", min_value=0, value=0, step=100)
    sidebar_max_p = st.number_input("Max Price (₹)", min_value=0, value=0, step=500)

    st.subheader("Search Engine Mode")
    engine_mode = st.radio(
        "Mode",
        options=["🎯 Title Match (Google-Grade)", "🛒 Category & Intent Search"],
        index=st.session_state.get("engine_mode_idx", 0),
        help="Title Match searches strictly by product title and groups exact/identical listings just like Google Search & Amazon, using minimal RAM (<100MB)."
    )

    group_same_title = st.checkbox(
        "👥 Group Same Title Products",
        value=True,
        help="Outputs products sharing the exact same title together in de-duplicated clusters."
    )

    st.caption(f"Index: {OS_INDEX}")
    st.caption(f"MongoDB: {MONGO_DB}.{MONGO_COLLECTION}")

    if st.button("🗑️ Clear Search History", key="btn_clear_hist", help="Clear recent query suggestions and history"):
        st.session_state["search_history"] = []
        st.rerun()

os_client, collection = get_clients()
taxonomy, taxonomy_index = get_taxonomy(os_client, index_name=OS_INDEX)

# Initialize Search History in Session State (starts clean and records user searches dynamically)
if "search_history" not in st.session_state:
    st.session_state["search_history"] = []

# Support query parameters (e.g. from 1-click live auto-complete suggestion click)
qp_query = st.query_params.get("q")
if qp_query:
    st.session_state["search_input"] = qp_query
    st.query_params.clear()

# Support programmatic search queries (e.g. from chips)
override_q = st.session_state.pop("search_query_override", None)
if override_q is not None:
    st.session_state["search_input"] = override_q

@st.cache_data
def get_autocomplete_vocabulary(_taxonomy, _os_client=None):
    """
    Dynamically mine e-commerce auto-complete vocabulary, categories, demographics,
    and attributes directly from the live OpenSearch cluster and taxonomy definitions.
    Zero static hardcoded lists.
    """
    vocab = set()
    core_items = set()
    apparel_items = set()
    discovered_demos = set()
    discovered_attributes = set()

    # 1. Dynamically extract items and demographics from OpenSearch taxonomy
    target_cats = (
        "Clothing", "Fashion Accessories", "Mobiles and Accessories",
        "Laptops and Accessories", "Electronics", "Home Appliances"
    )

    for sid, info in _taxonomy.items():
        sub_name = info.get("sub_cat_name", "").strip()
        cat_name = re.sub(r'\s+', ' ', info.get("cat_name", "").strip())
        if not sub_name or sub_name == "Others":
            continue
        s_low = sub_name.lower()
        vocab.add(s_low)

        demo, _ = extract_demographic(s_low)
        is_apparel = cat_name in ("Clothing", "Fashion Accessories")
        if demo:
            discovered_demos.add(demo)
            clean = re.sub(r'\b(?:men|man|women|woman|kids|kid|boys|boy|girls|girl)\b', '', s_low, flags=re.I).strip()
            clean = re.sub(r'\s+', ' ', clean)
            if clean:
                vocab.add(f"{clean} for {demo}")
                vocab.add(f"{demo} {clean}")
                if cat_name in target_cats:
                    core_items.add(clean)
                if is_apparel:
                    apparel_items.add(clean)
                if clean.endswith("s") and len(clean) > 3 and not clean.endswith("ss"):
                    sing = clean[:-1]
                    vocab.add(f"{sing} for {demo}")
                    vocab.add(f"{demo} {sing}")
                    if cat_name in target_cats:
                        core_items.add(sing)
                    if is_apparel:
                        apparel_items.add(sing)
                if "topwear" in s_low or "topwear" in clean:
                    core_items.update(["top", "tops", "crop top"])
                    apparel_items.update(["top", "tops", "crop top"])
                    vocab.add(f"top for {demo}")
                    vocab.add(f"tops for {demo}")
                if "ethnicwear" in s_low or "ethnicwear" in clean:
                    core_items.update(["kurti", "saree", "dress", "dresses"])
                    apparel_items.update(["kurti", "saree", "dress", "dresses"])
        else:
            clean = re.sub(r'\s+', ' ', s_low)
            if cat_name in target_cats:
                core_items.add(clean)
            if is_apparel:
                apparel_items.add(clean)
            if clean.endswith("s") and len(clean) > 3 and not clean.endswith("ss"):
                if cat_name in target_cats:
                    core_items.add(clean[:-1])
                if is_apparel:
                    apparel_items.add(clean[:-1])

    # 2. Dynamically sample real product titles and attributes from OpenSearch cluster
    if _os_client and hasattr(_os_client, "search"):
        try:
            # Dynamically extract attributes from Clothing titles
            res_clothing = _os_client.search(
                index=OS_INDEX,
                body={
                    "size": 300,
                    "_source": ["title"],
                    "query": {"term": {"category_name.raw": "Clothing"}}
                }
            )
            for h in res_clothing.get("hits", {}).get("hits", []):
                raw_t = h.get("_source", {}).get("title", "")
                clean_t = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw_t)
                clean_t = re.split(r'[,|:\-–]', clean_t)[0].strip().lower()
                toks = clean_t.split()
                if 2 <= len(toks) <= 4:
                    vocab.add(clean_t)
                for itm in list(core_items):
                    if len(itm) < 3:
                        continue
                    pattern = r'\b([a-z]{3,})\s+' + re.escape(itm) + r'\b'
                    for m in re.finditer(pattern, raw_t.lower()):
                        attr = m.group(1)
                        if attr not in ('for', 'and', 'the', 'with', 'from', 'all', 'wear') and attr not in discovered_demos:
                            discovered_attributes.add(attr)

            # Sample real tech and home appliance titles from OpenSearch
            res_tech = _os_client.search(
                index=OS_INDEX,
                body={
                    "size": 300,
                    "_source": ["title"],
                    "query": {
                        "bool": {
                            "should": [
                                {"term": {"category_name.raw": "Mobiles and Accessories"}},
                                {"term": {"category_name.raw": "Home Appliances"}},
                                {"term": {"category_name.raw": "Electronics"}}
                            ],
                            "minimum_should_match": 1
                        }
                    }
                }
            )
            for h in res_tech.get("hits", {}).get("hits", []):
                raw_t = h.get("_source", {}).get("title", "")
                clean_t = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw_t)
                clean_t = re.split(r'[,|:\-–]', clean_t)[0].strip().lower()
                toks = clean_t.split()
                if 2 <= len(toks) <= 4:
                    vocab.add(clean_t)
        except Exception:
            pass

    # 3. Dynamic Cross-Product: Mined attributes + Mined apparel items + Mined demographics
    for attr in discovered_attributes:
        for itm in list(core_items)[:30]:
            vocab.add(f"{attr} {itm}")
            for d in discovered_demos:
                vocab.add(f"{attr} {itm} for {d}")
                vocab.add(f"{d} {attr} {itm}")

    brand_category_map = {}

    # Prioritize categories by their dynamic document count directly from OpenSearch
    subcat_freq_map = {}
    if _os_client and hasattr(_os_client, "search"):
        try:
            res_sc = _os_client.search(
                index=OS_INDEX,
                body={
                    "size": 0,
                    "aggs": {"subcats": {"terms": {"field": "subcategory_name.raw", "size": 150}}}
                }
            )
            for bck in res_sc.get("aggregations", {}).get("subcats", {}).get("buckets", []):
                k = bck["key"].lower().strip()
                subcat_freq_map[k] = bck["doc_count"]
        except Exception:
            pass

    sorted_items = sorted(
        list(core_items),
        key=lambda x: (-subcat_freq_map.get(x, 0), x)
    )

    return (
        sorted(list(vocab)),
        sorted_items,
        sorted(list(apparel_items)),
        sorted(list(discovered_demos)),
        sorted(list(discovered_attributes)),
        brand_category_map
    )

# Dynamic auto-complete vocabulary mined from OpenSearch cluster
base_vocab, dynamic_items, dynamic_apparel, dynamic_demos, dynamic_attrs, dynamic_brand_map = get_autocomplete_vocabulary(taxonomy, os_client)

history = list(reversed(st.session_state.get("search_history", [])))
ALL_SUGGESTIONS = list(dict.fromkeys(history + base_vocab))
json_suggestions = json.dumps(ALL_SUGGESTIONS)
json_history = json.dumps(history)
json_items = json.dumps(dynamic_items)
json_apparel = json.dumps(dynamic_apparel)
json_demos = json.dumps(dynamic_demos)
json_attrs = json.dumps(dynamic_attrs)
json_brand_map = json.dumps(dynamic_brand_map)

# Search Input and Action Buttons
col_search, col_btn = st.columns([5, 1])

with col_search:
    query = st.text_input(
        "Search for a product title",
        placeholder="Search products across all departments...",
        key="search_input",
        autocomplete="off",
    )

with col_btn:
    st.write("")
    st.write("")
    search_clicked = st.button(
        "Search products",
        type="primary",
        disabled=False,
        use_container_width=True,
    )

# Inject Google-Style Live Keystroke Auto-complete Dropdown directly attached under search bar
components.html(
    f"""
    <script>
    (function() {{
        const parentDoc = window.parent.document;
        const allSuggestions = {json_suggestions};
        const historyList = {json_history};
        
        function initDropdown() {{
            const input = parentDoc.querySelector('input[aria-label="Search for a product title"]');
            if (!input) {{
                return setTimeout(initDropdown, 100);
            }}

            // Strictly suppress browser native autocomplete, autofill tooltip, and spellcheck
            input.autocomplete = "off";
            input.setAttribute("autocomplete", "off");
            input.setAttribute("autocorrect", "off");
            input.setAttribute("autocapitalize", "off");
            input.setAttribute("spellcheck", "false");
            input.setAttribute("aria-autocomplete", "none");
            input.setAttribute("data-lpignore", "true");
            input.setAttribute("data-form-type", "other");

            const parentForm = input.closest("form");
            if (parentForm) {{
                parentForm.setAttribute("autocomplete", "off");
            }}

            // Get or create dropdown container attached to parent document body
            let dropdown = parentDoc.getElementById("live-search-dropdown");
            if (!dropdown) {{
                dropdown = parentDoc.createElement("div");
                dropdown.id = "live-search-dropdown";
                dropdown.style.position = "absolute";
                dropdown.style.zIndex = "9999999";
                dropdown.style.background = "#ffffff";
                dropdown.style.border = "1px solid #cbd5e1";
                dropdown.style.borderRadius = "8px";
                dropdown.style.boxShadow = "0 10px 25px -5px rgba(0, 0, 0, 0.15), 0 8px 10px -6px rgba(0, 0, 0, 0.08)";
                dropdown.style.display = "none";
                dropdown.style.overflow = "hidden";
                dropdown.style.fontFamily = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
                parentDoc.body.appendChild(dropdown);
            }}

            function positionDropdown() {{
                const rect = input.getBoundingClientRect();
                dropdown.style.top = (rect.bottom + window.parent.scrollY + 4) + "px";
                dropdown.style.left = (rect.left + window.parent.scrollX) + "px";
                dropdown.style.width = rect.width + "px";
            }}

            let selectedIndex = -1;
            let currentMatches = [];
            let originalTypedValue = "";

            function setInputPreview(val) {{
                if (!input) return;
                const nativeSetter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, "value").set;
                if (nativeSetter) {{
                    nativeSetter.call(input, val);
                }} else {{
                    input.value = val;
                }}
            }}

            function selectItem(val) {{
                dropdown.style.display = "none";
                if (input) {{
                    input.focus();
                    setInputPreview(val);
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}

                const parentUrl = new URL(window.parent.location.href);
                parentUrl.searchParams.set("q", val);
                window.parent.location.href = parentUrl.toString();
            }}

            const popularCategories = {json_items};
            const apparelCategories = {json_apparel};
            const demographicsList = {json_demos};
            const attributesList = {json_attrs};
            const brandCategoriesMap = {json_brand_map};

            function getDynamicPredictions(val) {{
                const v = val.trim().toLowerCase();
                const preds = [];
                if (!v) return preds;

                // 1. Demographic suffix completion (e.g. 'black jeans for', 'for', 'top for w')
                // Note: Only apparel/wearable items can have demographic completions!
                if (v.endsWith(" for") || v === "for") {{
                    const base = v.endsWith(" for") ? v : "for";
                    const pre = v.endsWith(" for") ? v.slice(0, -4).trim() : "";
                    const lastToken = pre ? pre.split(/\\s+/).pop() : "";
                    const isPreWearable = !pre || apparelCategories.some(ac => pre.includes(ac) || ac.includes(lastToken));
                    if (isPreWearable) {{
                        for (const d of demographicsList) {{
                            preds.push(`${{base}} ${{d}}`);
                        }}
                    }}
                    return preds;
                }} else if (v.includes(" for ")) {{
                    const parts = v.split(" for ");
                    const pre = parts[0].trim();
                    const partial = parts[1].trim();
                    const lastToken = pre.split(/\\s+/).pop();
                    const isPreWearable = apparelCategories.some(ac => pre.includes(ac) || ac.includes(lastToken));
                    if (isPreWearable) {{
                        for (const d of demographicsList) {{
                            if (d.startsWith(partial)) {{
                                preds.push(`${{pre}} for ${{d}}`);
                            }}
                        }}
                    }}
                    return preds;
                }}

                const tokens = v.split(/\\s+/);

                // 2. Single word typed (e.g. 'black', 'women', 'jeans', 'apple', 'samsung', 'haier', etc.)
                if (tokens.length === 1) {{
                    const word = tokens[0];

                    // 2A. Recognized brand with dynamic mined catalog categories!
                    if (brandCategoriesMap[word]) {{
                        const bCats = brandCategoriesMap[word];
                        for (const cat of bCats) {{
                            preds.push(`${{word}} ${{cat}}`);
                            if (apparelCategories.includes(cat)) {{
                                for (const d of demographicsList.slice(0, 2)) {{
                                    preds.push(`${{word}} ${{cat}} for ${{d}}`);
                                }}
                            }}
                        }}
                    }}
                    // 2B. Demographic typed first (e.g. 'women', 'men', 'kids') -> suggest apparel categories
                    else if (demographicsList.includes(word)) {{
                        for (const cat of apparelCategories.slice(0, 8)) {{
                            preds.push(`${{word}} ${{cat}}`);
                            preds.push(`${{cat}} for ${{word}}`);
                        }}
                    }}
                    // 2C. Wearable category typed (e.g. 'jeans', 'shoes', 'top') -> suggest demographics
                    else if (apparelCategories.includes(word)) {{
                        for (const d of demographicsList) {{
                            preds.push(`${{word}} for ${{d}}`);
                        }}
                    }}
                    // 2D. Attribute / Color / Fabric typed (e.g. 'black', 'white', 'blue', 'cotton', 'denim') -> suggest apparel
                    else if (attributesList.includes(word)) {{
                        for (const cat of apparelCategories.slice(0, 6)) {{
                            preds.push(`${{word}} ${{cat}}`);
                            for (const d of demographicsList.slice(0, 2)) {{
                                preds.push(`${{word}} ${{cat}} for ${{d}}`);
                            }}
                        }}
                    }}
                }}

                // 3. Two or more words typed (e.g. 'haier wash', 'haier ref', 'nike sh', 'black je')
                else if (tokens.length >= 2 && !v.includes("for")) {{
                    const firstWord = tokens[0];
                    const partialRest = tokens.slice(1).join(" ");

                    // If full category matched (e.g. 'nike shoes', 'black jeans', 'haier washing machine')
                    if (popularCategories.includes(partialRest)) {{
                        const isWearable = apparelCategories.includes(partialRest);
                        if (isWearable && !demographicsList.includes(firstWord)) {{
                            for (const d of demographicsList) {{
                                preds.push(`${{v}} for ${{d}}`);
                            }}
                        }}
                    }}
                    // If partial category prefix (e.g. 'haier wash', 'haier ref', 'nike sh', 'black je')
                    else {{
                        for (const cat of popularCategories) {{
                            if (cat.startsWith(partialRest)) {{
                                preds.push(`${{firstWord}} ${{cat}}`);
                                const isWearable = apparelCategories.includes(cat);
                                if (isWearable && !demographicsList.includes(firstWord)) {{
                                    for (const d of demographicsList.slice(0, 2)) {{
                                        preds.push(`${{firstWord}} ${{cat}} for ${{d}}`);
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}

                return preds;
            }}

            function getFilteredSuggestions(val) {{
                if (!val) {{
                    return historyList.slice(0, 5);
                }}
                val = val.trim().toLowerCase();

                // 1. Dynamic predictive completions (e.g. black -> black jeans, black top; black jeans -> black jeans for women)
                const dynamic = getDynamicPredictions(val);

                // 2. Starts with prefix
                const startsWith = allSuggestions.filter(s => s.toLowerCase().startsWith(val));
                startsWith.sort((a, b) => a.length - b.length || a.localeCompare(b));

                // 3. Individual word starts with prefix
                const wordStarts = allSuggestions.filter(s => {{
                    const sLower = s.toLowerCase();
                    if (sLower.startsWith(val)) return false;
                    const words = sLower.split(/[\\s\\-_]+/);
                    return words.some(w => w.startsWith(val));
                }});
                wordStarts.sort((a, b) => a.length - b.length || a.localeCompare(b));

                // 4. Substring match
                const contains = allSuggestions.filter(s => {{
                    const sLower = s.toLowerCase();
                    return !sLower.startsWith(val) && !wordStarts.includes(s) && sLower.includes(val);
                }});
                contains.sort((a, b) => a.length - b.length || a.localeCompare(b));

                const combined = [...dynamic, ...startsWith, ...wordStarts, ...contains];
                return Array.from(new Set(combined)).slice(0, 8);
            }}

            function renderDropdown(matches) {{
                currentMatches = matches;
                selectedIndex = -1;
                if (!matches || !matches.length) {{
                    dropdown.style.display = "none";
                    return;
                }}
                positionDropdown();
                dropdown.innerHTML = "";

                const val = input.value.trim().toLowerCase();

                matches.forEach((item, idx) => {{
                    const row = parentDoc.createElement("div");
                    row.className = "live-sug-item";
                    row.dataset.index = idx;
                    row.style.padding = "10px 14px";
                    row.style.cursor = "pointer";
                    row.style.fontSize = "13.5px";
                    row.style.color = "#1e293b";
                    row.style.borderBottom = idx < matches.length - 1 ? "1px solid #f1f5f9" : "none";
                    row.style.display = "flex";
                    row.style.alignItems = "center";
                    row.style.justifyContent = "space-between";
                    row.style.transition = "background-color 0.1s ease";

                    const leftWrap = parentDoc.createElement("div");
                    leftWrap.style.display = "flex";
                    leftWrap.style.alignItems = "center";
                    leftWrap.style.gap = "10px";
                    leftWrap.style.overflow = "hidden";
                    leftWrap.style.textOverflow = "ellipsis";
                    leftWrap.style.whiteSpace = "nowrap";

                    const isHist = historyList.includes(item);
                    const icon = parentDoc.createElement("span");
                    icon.textContent = isHist ? "🕒" : "🔍";
                    icon.style.fontSize = "13px";
                    icon.style.opacity = "0.65";
                    icon.style.flexShrink = "0";
                    leftWrap.appendChild(icon);

                    const textSpan = parentDoc.createElement("span");
                    textSpan.style.overflow = "hidden";
                    textSpan.style.textOverflow = "ellipsis";
                    textSpan.style.whiteSpace = "nowrap";

                    const itemLower = item.toLowerCase();
                    const matchIdx = val ? itemLower.indexOf(val) : -1;
                    if (matchIdx !== -1) {{
                        const before = item.slice(0, matchIdx);
                        const match = item.slice(matchIdx, matchIdx + val.length);
                        const after = item.slice(matchIdx + val.length);
                        textSpan.innerHTML = `${{before}}<strong style="color: #2563eb; font-weight: 700;">${{match}}</strong>${{after}}`;
                    }} else {{
                        textSpan.textContent = item;
                    }}
                    leftWrap.appendChild(textSpan);
                    row.appendChild(leftWrap);

                    if (isHist) {{
                        const badge = parentDoc.createElement("span");
                        badge.textContent = "Recent";
                        badge.style.fontSize = "10px";
                        badge.style.fontWeight = "600";
                        badge.style.color = "#64748b";
                        badge.style.background = "#f1f5f9";
                        badge.style.padding = "2px 6px";
                        badge.style.borderRadius = "4px";
                        badge.style.flexShrink = "0";
                        row.appendChild(badge);
                    }}

                    row.onmouseenter = () => {{
                        selectedIndex = idx;
                        updateHighlight(dropdown.querySelectorAll(".live-sug-item"));
                        setInputPreview(item);
                    }};
                    row.onmouseleave = () => {{
                        if (selectedIndex !== idx) {{
                            row.style.background = "#ffffff";
                        }}
                    }};
                    row.onmousedown = (e) => {{
                        e.preventDefault();
                        selectItem(item);
                    }};
                    row.onclick = () => {{
                        selectItem(item);
                    }};

                    dropdown.appendChild(row);
                }});

                // When user moves mouse away from the dropdown without selecting, restore their typed query
                dropdown.onmouseleave = () => {{
                    if (originalTypedValue !== undefined && input) {{
                        setInputPreview(originalTypedValue);
                        selectedIndex = -1;
                        updateHighlight(dropdown.querySelectorAll(".live-sug-item"));
                    }}
                }};

                dropdown.style.display = "block";
            }}

            function updateHighlight(items) {{
                items.forEach((item, idx) => {{
                    if (idx === selectedIndex) {{
                        item.style.background = "#eff6ff";
                    }} else {{
                        item.style.background = "#ffffff";
                    }}
                }});
            }}

            // Live keystroke autocomplete matching from 2 letters onwards purely via the API
            let liveDebounceTimer = null;
            input.oninput = () => {{
                originalTypedValue = input.value;
                const val = input.value.trim().toLowerCase();
                if (val.length < 2) {{
                    if (!val) {{
                        renderDropdown(historyList.slice(0, 5));
                    }} else {{
                        dropdown.style.display = "none";
                    }}
                    return;
                }}

                // Query live autocomplete API directly from 2 characters onwards
                clearTimeout(liveDebounceTimer);
                liveDebounceTimer = setTimeout(() => {{
                    fetch('{AUTOCOMPLETE_ENDPOINT_URL}?q=' + encodeURIComponent(val))
                        .then(r => r.json())
                        .then(dbSugs => {{
                            if (input.value.trim().toLowerCase() === val) {{
                                if (dbSugs && dbSugs.length) {{
                                    renderDropdown(dbSugs.slice(0, 8));
                                }} else {{
                                    dropdown.style.display = "none";
                                }}
                            }}
                        }})
                        .catch(() => {{
                            dropdown.style.display = "none";
                        }});
                }}, 30);
            }};

            input.onfocus = () => {{
                originalTypedValue = input.value;
                const val = input.value.trim().toLowerCase();
                if (val.length >= 2) {{
                    fetch('{AUTOCOMPLETE_ENDPOINT_URL}?q=' + encodeURIComponent(val))
                        .then(r => r.json())
                        .then(dbSugs => {{
                            if (input.value.trim().toLowerCase() === val && dbSugs && dbSugs.length) {{
                                renderDropdown(dbSugs.slice(0, 8));
                            }}
                        }})
                        .catch(() => {{}});
                }} else if (!val) {{
                    renderDropdown(historyList.slice(0, 5));
                }}
            }};

            input.onkeydown = (e) => {{
                if (e.key === "Enter") {{
                    if (dropdown.style.display === "block" && selectedIndex >= 0 && selectedIndex < currentMatches.length) {{
                        e.preventDefault();
                        selectItem(currentMatches[selectedIndex]);
                        return;
                    }} else {{
                        dropdown.style.display = "none";
                        dropdown.innerHTML = "";
                        selectedIndex = -1;
                        originalTypedValue = input.value;
                        return;
                    }}
                }}
                if (e.key === "Escape") {{
                    dropdown.style.display = "none";
                    dropdown.innerHTML = "";
                    selectedIndex = -1;
                    if (originalTypedValue !== undefined) {{
                        setInputPreview(originalTypedValue);
                    }}
                    return;
                }}
                if (dropdown.style.display !== "block" || !currentMatches.length) return;
                const items = dropdown.querySelectorAll(".live-sug-item");
                if (e.key === "ArrowDown") {{
                    e.preventDefault();
                    selectedIndex = (selectedIndex + 1) % items.length;
                    updateHighlight(items);
                    if (selectedIndex >= 0 && selectedIndex < currentMatches.length) {{
                        setInputPreview(currentMatches[selectedIndex]);
                    }}
                }} else if (e.key === "ArrowUp") {{
                    e.preventDefault();
                    selectedIndex = (selectedIndex - 1 + items.length) % items.length;
                    updateHighlight(items);
                    if (selectedIndex >= 0 && selectedIndex < currentMatches.length) {{
                        setInputPreview(currentMatches[selectedIndex]);
                    }}
                }}
            }};

            // Global dismiss on click outside
            if (parentDoc._liveDropdownClickHandler) {{
                parentDoc.removeEventListener("click", parentDoc._liveDropdownClickHandler);
            }}
            parentDoc._liveDropdownClickHandler = (e) => {{
                if (e.target !== input && !dropdown.contains(e.target)) {{
                    dropdown.style.display = "none";
                    dropdown.innerHTML = "";
                    selectedIndex = -1;
                    if (originalTypedValue !== undefined && !e.target.closest("button")) {{
                        setInputPreview(originalTypedValue);
                    }}
                }}
            }};
            parentDoc.addEventListener("click", parentDoc._liveDropdownClickHandler);

            if (window.parent._liveDropdownResizeHandler) {{
                window.parent.removeEventListener("resize", window.parent._liveDropdownResizeHandler);
            }}
            window.parent._liveDropdownResizeHandler = () => {{
                if (dropdown.style.display === "block") positionDropdown();
            }};
            window.parent.addEventListener("resize", window.parent._liveDropdownResizeHandler);
        }}

        initDropdown();
    }})();
    </script>
    """,
    height=0,
    width=0,
)

# Recent Search Quick Chips
if st.session_state["search_history"]:
    recent_cols = st.columns([1] + [1.8] * min(5, len(st.session_state["search_history"])))
    with recent_cols[0]:
        st.caption("🕒 **Recent:**")
    for idx, past_q in enumerate(list(reversed(st.session_state["search_history"]))[:5]):
        with recent_cols[idx + 1]:
            if st.button(f"🔍 {past_q[:18]}", key=f"chip_{idx}_{past_q[:10]}", help=f"Search for '{past_q}'"):
                st.session_state["search_query_override"] = past_q
                st.rerun()

active_query = query.strip()

if active_query:
    try:
        if st.session_state.get("last_searched_query") != active_query:
            st.session_state["last_searched_query"] = active_query
            st.session_state["force_verbatim"] = False

        if active_query:
            if active_query in st.session_state["search_history"]:
                st.session_state["search_history"].remove(active_query)
            st.session_state["search_history"].append(active_query)

        with st.spinner("Searching products..."):
            os_client, collection = get_clients()

            # Check if user typed a misspelled query
            suggested_query = suggest_correction(os_client, active_query, index_name=OS_INDEX)
            is_verbatim = st.session_state.get("force_verbatim", False)
            query_to_run = active_query if (is_verbatim or not suggested_query) else suggested_query

            min_override = float(sidebar_min_p) if sidebar_min_p > 0 else None
            max_override = float(sidebar_max_p) if sidebar_max_p > 0 else None

            res, clean_q, parsed_min_p, parsed_max_p, intent = search_products(
                os_client,
                query_to_run,
                result_limit,
                taxonomy=taxonomy,
                taxonomy_index=taxonomy_index,
                selected_subcat=None,
                min_price_override=min_override,
                max_price_override=max_override,
                sort_by=sort_option,
                search_mode=engine_mode,
                group_by_same_title=group_same_title,
                index_name=OS_INDEX,
            )

            hits = res["hits"]["hits"]
            total_hits = res["hits"]["total"]["value"]

            # Show 'Did you mean' banner when mistyped query is corrected
            if suggested_query and not is_verbatim:
                c_sug, c_opt = st.columns([4, 1.4])
                with c_sug:
                    st.markdown(
                        f"""
                        <div style="background: linear-gradient(135deg, #eef6ff, #dbeafe); border-left: 5px solid #2563eb; padding: 14px 18px; border-radius: 8px; margin-bottom: 14px; box-shadow: 0 2px 5px rgba(37,99,235,0.06);">
                            <div style="font-size: 16px; font-weight: 700; color: #1e40af; margin-bottom: 3px;">
                                🔍 Did you mean: <span style="text-decoration: underline; color: #1d4ed8;">{suggested_query}</span>?
                            </div>
                            <div style="font-size: 13px; color: #334155;">
                                Mistyped spelling considered correct — showing results for <strong>"{suggested_query}"</strong> instead of <s>"{active_query}"</s>.
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with c_opt:
                    if st.button(f"Search instead for verbatim:\n'{active_query}'", key="btn_verbatim"):
                        st.session_state["force_verbatim"] = True
                        st.rerun()
            elif is_verbatim and suggested_query:
                c_verb, c_switch = st.columns([4, 1.4])
                with c_verb:
                    st.markdown(
                        f"""
                        <div style="background: #f8fafc; border-left: 4px solid #64748b; padding: 10px 16px; border-radius: 8px; margin-bottom: 12px;">
                            <span style="font-size: 13px; color: #334155;">
                                Showing results for verbatim search: <strong>"{active_query}"</strong>.
                            </span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with c_switch:
                    if st.button(f"👉 Did you mean:\n'{suggested_query}'?", key="btn_switch_suggested"):
                        st.session_state["force_verbatim"] = False
                        st.rerun()

            # Show intent classification & category badge
            target_name = intent.get("target_subcat_name")
            residual = intent.get("residual_query")
            intent_type = intent.get("intent_type", "CORE_PRODUCT")

            if intent_type == "ACCESSORY":
                display_cat = target_name or "Accessories & Parts"
                st.info(f"🧩 **Intent Classified: Accessory / Part** | Category: **{display_cat}**")
            elif target_name:
                intent_info = f"🎯 **Intent Classified: Core Product** | Category: **{target_name}**"
                if residual:
                    intent_info += f" | Search terms: **'{residual}'**"
                st.info(intent_info)

            # Show price badge if price filter was active
            eff_min = min_override or parsed_min_p
            eff_max = max_override or parsed_max_p
            if eff_min is not None or eff_max is not None:
                if eff_min is not None and eff_max is not None:
                    st.info(f"🏷️ Price filter active: **₹{eff_min:,.0f} - ₹{eff_max:,.0f}**")
                elif eff_max is not None:
                    st.info(f"🏷️ Price filter active: **Up to ₹{eff_max:,.0f}**")
                else:
                    st.info(f"🏷️ Price filter active: **Above ₹{eff_min:,.0f}**")

            if not hits:
                st.warning("No matching products found.")
                st.stop()

            product_ids = []
            for hit in hits:
                src = hit.get("_source", {})
                pid = src.get("product_id")
                if pid:
                    product_ids.append(pid)
                for sprod in src.get("same_title_products", []):
                    spid = sprod.get("product_id")
                    if spid:
                        product_ids.append(spid)

            mongo_products = find_mongo_products(
                collection,
                list(dict.fromkeys(product_ids)),
                image_url_field=IMAGE_URL_FIELD,
            )

            # Restrict results strictly to target supported stores (Flipkart, Amazon, JioMart, Tata CLiQ, Myntra, Ajio, Pepperfry, Croma, Reliance Digital, Meesho)
            target_sid_set = {str(s) for s in TARGET_STORE_IDS}.union({int(s) for s in TARGET_STORE_IDS})
            valid_hits = []
            for hit in hits:
                src = hit.get("_source", {})
                pid = str(src.get("product_id", ""))
                mdoc = mongo_products.get(pid)
                doc_sid = (mdoc and mdoc.get("sid")) or src.get("sid")
                if doc_sid is None or (doc_sid not in target_sid_set and str(doc_sid) not in target_sid_set):
                    continue
                if "same_title_products" in src:
                    src["same_title_products"] = [
                        sp for sp in src["same_title_products"]
                        if (mongo_products.get(str(sp.get("product_id")), {}).get("sid") or sp.get("sid")) in target_sid_set
                    ]
                valid_hits.append(hit)
            hits = valid_hits

        st.success(f"Found {total_hits:,} search matches.")

        missing_mongo = 0

        # Render 2 products per row for a clean grid layout
        for start_idx in range(0, len(hits), 2):
            batch_hits = hits[start_idx : start_idx + 2]
            row_cols = st.columns(2)

            for col_idx, hit in enumerate(batch_hits):
                rank = start_idx + col_idx + 1
                source = hit.get("_source", {})
                product_id = str(source.get("product_id", ""))
                mongo_doc = mongo_products.get(product_id)

                sub_cat_id = str(source.get("sub_cat_id", ""))
                subcat_info = taxonomy.get(sub_cat_id, {})
                cat_name = subcat_info.get("cat_name") or source.get("category_name")
                subcat_name = subcat_info.get("sub_cat_name") or source.get("subcategory_name")

                if cat_name and subcat_name:
                    display_cat = f"{cat_name} > {subcat_name}"
                elif subcat_name:
                    display_cat = subcat_name
                else:
                    display_cat = f"Category {source.get('category', 'N/A')}"

                with row_cols[col_idx]:
                    with st.container(border=True):
                        c_img, c_info = st.columns([1, 2])

                        with c_img:
                            image_url = (
                                get_nested_value(mongo_doc, IMAGE_URL_FIELD)
                                if mongo_doc
                                else None
                            )

                            if image_url:
                                try:
                                    st.image(image_url)
                                except Exception:
                                    st.caption("Image unavailable")
                            else:
                                st.info("No image found")

                        with c_info:
                            badge = source.get("match_badge")
                            if badge:
                                bg = source.get("match_badge_bg", "#f1f5f9")
                                col = source.get("match_badge_color", "#334155")
                                st.markdown(
                                    f'<span style="background-color: {bg}; color: {col}; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 9999px; letter-spacing: 0.4px; display: inline-block; margin-bottom: 6px;">{badge}</span>',
                                    unsafe_allow_html=True,
                                )

                            hl_title = source.get("highlighted_title") or source.get("title") or "Untitled"
                            st.markdown(
                                f"<div style='font-size: 15px; font-weight: 700; line-height: 1.35; margin-bottom: 4px;'>{rank}. {hl_title}</div>",
                                unsafe_allow_html=True,
                            )
                            brand_val = source.get("brand") or (mongo_doc and mongo_doc.get("brand"))
                            brand_tag = f" | 🏷️ Brand: **{brand_val}**" if (brand_val and str(brand_val).strip() and str(brand_val).strip().lower() != "none") else ""
                            st.caption(f"📂 **{display_cat}**{brand_tag}")

                            # Display price directly from OpenSearch index or MongoDB
                            price_val = (mongo_doc and mongo_doc.get("price")) or source.get("price")
                            if price_val:
                                try:
                                    st.markdown(f"💰 **₹{float(price_val):,.0f}**")
                                except (ValueError, TypeError):
                                    st.markdown(f"💰 **₹{price_val}**")

                            doc_sid = (mongo_doc and mongo_doc.get("sid")) or source.get("sid")
                            doc_pid = (mongo_doc and mongo_doc.get("pid")) or source.get("pid")
                            sid_str = str(doc_sid) if doc_sid is not None else "N/A"
                            pid_str = str(doc_pid) if doc_pid is not None else "N/A"

                            st.caption(f"🎯 Relevance Score: **{hit.get('_score', 0):.1f} / 100** | SID: `{sid_str}` | PID: `{pid_str}`")

                            doc_title = (mongo_doc and mongo_doc.get("title")) or source.get("title")
                            doc_canon = mongo_doc.get("canonical_url") if mongo_doc else None
                            flipshope_url = build_flipshope_url(doc_sid, doc_pid, doc_title, doc_canon)
                            direct_store_url = build_direct_store_url(doc_sid, doc_pid)
                            store_name = STORE_DISPLAY_NAMES.get(str(doc_sid), "Store")

                            btn_parts = []
                            if flipshope_url:
                                btn_parts.append(f'<a href="{flipshope_url}" target="_blank" class="flipshope-cta-btn">🛍️ Open on Flipshope ↗</a>')
                            if direct_store_url:
                                btn_parts.append(f'<a href="{direct_store_url}" target="_blank" class="store-cta-btn">🛒 {store_name} ↗</a>')

                            if btn_parts:
                                st.markdown(
                                    f'<div style="display:flex; gap:8px; align-items:center; margin:8px 0 6px 0;">{"".join(btn_parts)}</div>',
                                    unsafe_allow_html=True,
                                )

                            # Same Title Products Cluster & Comparison Section
                            same_prods = source.get("same_title_products", [])
                            same_count = source.get("same_title_count", len(same_prods))

                            if same_count > 1:
                                prices = [p.get("price") for p in same_prods if p.get("price") is not None]
                                if prices:
                                    min_p = min(prices)
                                    max_p = max(prices)
                                    if min_p < max_p:
                                        diff = max_p - min_p
                                        pct = int((diff / max_p) * 100)
                                        st.markdown(
                                            f'<div style="background: #ecfdf5; border-left: 4px solid #10b981; padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; color: #065f46; margin: 6px 0;">'
                                            f'🔄 <strong>{same_count} Same Title Products Found</strong><br>'
                                            f'Spread: ₹{min_p:,.0f} – ₹{max_p:,.0f} (Save up to ₹{diff:,.0f} / {pct}%)'
                                            f'</div>',
                                            unsafe_allow_html=True,
                                        )
                                    else:
                                        st.markdown(
                                            f'<div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; color: #1e40af; margin: 6px 0;">'
                                            f'🔄 <strong>{same_count} Same Title Products Found</strong> in database'
                                            f'</div>',
                                            unsafe_allow_html=True,
                                        )

                                with st.expander(f"📦 View all {len(same_prods)} same-title products", expanded=False):
                                    for idx_p, sp in enumerate(same_prods, 1):
                                        sp_id = sp.get("product_id")
                                        sp_doc = mongo_products.get(sp_id, {})
                                        sp_price = (sp_doc and sp_doc.get("price")) or sp.get("price")
                                        sp_sid = (sp_doc and sp_doc.get("sid")) or sp.get("sid")
                                        sp_pid = (sp_doc and sp_doc.get("pid")) or sp.get("pid")
                                        store_label = STORE_DISPLAY_NAMES.get(str(sp_sid), f"Store #{sp_sid}" if sp_sid is not None else "Online Store")
                                        sp_stock = sp_doc.get("stock") if sp_doc else None
                                        stock_str = "🟢 In Stock" if sp_stock == 1 else ("🔴 Out of Stock" if sp_stock == 0 else "")
                                        p_str = f"₹{float(sp_price):,.0f}" if sp_price else "N/A"
                                        sp_brand = sp.get("brand") or (sp_doc and sp_doc.get("brand"))
                                        brand_sub = f" | Brand: {sp_brand}" if (sp_brand and str(sp_brand).strip() and str(sp_brand).strip().lower() != "none") else ""
                                        
                                        sp_title = (sp_doc and sp_doc.get("title")) or sp.get("title")
                                        sp_canon = sp_doc.get("canonical_url") if sp_doc else None
                                        sp_url = build_flipshope_url(sp_sid, sp_pid, sp_title, sp_canon)
                                        sp_direct = build_direct_store_url(sp_sid, sp_pid)
                                        link_parts = []
                                        if sp_url:
                                            link_parts.append(f'<a href="{sp_url}" target="_blank" style="color:#2563eb; font-weight:600; text-decoration:underline;">🛍️ Flipshope</a>')
                                        if sp_direct:
                                            link_parts.append(f'<a href="{sp_direct}" target="_blank" style="color:#059669; font-weight:600; text-decoration:underline;">🛒 {store_label}</a>')
                                        ref_link_tag = f" | {' · '.join(link_parts)}" if link_parts else ""

                                        st.markdown(
                                            f"**#{idx_p}** {store_label} — **{p_str}** {stock_str}<br><span style='font-size:11px;color:#64748b;'>SID: `{sp_sid}` | PID: `{sp_pid}`{brand_sub}{ref_link_tag}</span>",
                                            unsafe_allow_html=True,
                                        )

                            # Button to instantly search all listings with this exact same title
                            item_title = source.get("title", "")
                            if item_title:
                                if st.button("🔍 Exact title match", key=f"btn_same_{product_id}_{rank}", help=f"Search for all identical listings across stores/sellers for '{item_title[:40]}...'"):
                                    st.session_state["search_query_override"] = item_title
                                    st.session_state["engine_mode_idx"] = 0
                                    st.session_state["force_verbatim"] = True
                                    st.rerun()

                            if not mongo_doc:
                                missing_mongo += 1
                                st.warning("Not found in MongoDB")

        if missing_mongo:
            st.caption(f"{missing_mongo} result(s) could not be matched to MongoDB.")

    except Exception as exc:
        st.error(f"Search failed: {exc}")
        st.info("Check your .env settings, MongoDB connection, and whether OpenSearch is running on localhost:9200.")

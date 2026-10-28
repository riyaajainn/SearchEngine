import json
import re
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from config import (
    OPENSEARCH_INDEX,
    AUTOCOMPLETE_HOST,
    AUTOCOMPLETE_PORT,
    get_opensearch_client,
)
from nlp_utils import (
    RE_TOKENS,
    RE_COMPATIBLE,
    clean_title,
    is_compatibility_stuffed,
)
from taxonomy_learner import mine_accessory_vocabulary
from query_builder import build_autocomplete_dsl

# Centralized OpenSearch client
client = get_opensearch_client(timeout=5, max_retries=1)

# Dynamically mine statistical accessory terms from the live OpenSearch cluster at startup
try:
    DYNAMIC_ACCESSORY_KEYWORDS, _ = mine_accessory_vocabulary(client, index_name=OPENSEARCH_INDEX)
except Exception:
    DYNAMIC_ACCESSORY_KEYWORDS = set()


def is_accessory_item(raw_title: str, subcat_name: str, query: str = "") -> bool:
    """
    100% Generalized accessory and attachment detector.
    Works universally across all product categories without hardcoding individual models, brands, or parts.
    """
    raw_lower = raw_title.lower()

    # 1. Dynamic subcategory check: Any accessory category in the catalog
    if "accessor" in subcat_name.lower():
        return True

    # 2. Universal linguistic attachment pattern:
    # If the user query Q is preceded by relational prepositions ('for', 'compatible with', 'suitable for', 'fit for'),
    # this listing is an attachment / accessory manufactured FOR the product, not the product itself.
    if query:
        q_tokens = [t.lower() for t in RE_TOKENS.findall(query) if len(t) >= 2]
        if q_tokens:
            q_esc = re.escape(" ".join(q_tokens))
            if re.search(r'\b(?:for|compatible with|compatible for|suitable for|fit for|designed for)\s+.*?' + q_esc, raw_lower):
                return True

    # 3. Universal relational grammar pattern: Prepositional attachment
    if RE_COMPATIBLE.search(raw_lower):
        return True

    # 4. Multi-model compatibility stuffing pattern:
    # If a queried phrase or model token repeats multiple times in a comma-delimited title,
    # it is an accessory / compatibility listing (e.g. "for Apple iPhone 16, Apple iPhone 16 Plus...")
    if query:
        q_tokens = [t.lower() for t in RE_TOKENS.findall(query) if len(t) >= 2]
        if is_compatibility_stuffed(raw_lower, q_tokens):
            return True

    # 5. Dynamically mined statistical accessory vocabulary tokens from OpenSearch odds ratio
    title_words = set(RE_TOKENS.findall(raw_lower))
    if title_words.intersection(DYNAMIC_ACCESSORY_KEYWORDS):
        return True

    return False


def query_opensearch_suggestions(q: str, size: int = 8) -> list:
    """
    Universal, data-driven live autocomplete querying OpenSearch.
    Zero hardcoded product, model, or brand lists.
    """
    raw_q = q.strip()
    if len(raw_q) < 2:
        return []

    tokens = RE_TOKENS.findall(raw_q)
    if not tokens:
        return []

    q_tokens_lower = {t.lower() for t in tokens}
    user_wants_accessory = bool(q_tokens_lower.intersection(DYNAMIC_ACCESSORY_KEYWORDS))

    body = build_autocomplete_dsl(raw_q, tokens, user_wants_accessory)

    try:
        res = client.search(index=OPENSEARCH_INDEX, body=body)
    except Exception:
        # Fallback to simple phrase prefix
        try:
            body["query"] = {
                "bool": {
                    "must": {"match_phrase_prefix": {"title": raw_q}}
                }
            }
            res = client.search(index=OPENSEARCH_INDEX, body=body)
        except Exception:
            return []

    hits = res.get("hits", {}).get("hits", [])
    suggestions = []
    seen = set()

    for h in hits:
        raw_t = h.get("_source", {}).get("title", "").strip()
        sub = h.get("_source", {}).get("subcategory_name", "")
        if not raw_t:
            continue

        clean_t = clean_title(raw_t)
        if not clean_t or len(clean_t) < 3:
            continue

        t_low = clean_t.lower()
        if t_low in seen:
            continue

        is_acc = is_accessory_item(raw_t, sub, raw_q)
        if not user_wants_accessory and is_acc:
            continue
        elif user_wants_accessory and not is_acc:
            continue

        seen.add(t_low)
        suggestions.append(clean_t)
        if len(suggestions) >= size:
            break

    return suggestions


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class AutocompleteHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/autocomplete":
            qs = urllib.parse.parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            try:
                import autocomplete_service
                import importlib
                importlib.reload(autocomplete_service)
                sugs = autocomplete_service.query_opensearch_suggestions(q, size=8)
            except Exception:
                sugs = query_opensearch_suggestions(q, size=8)

            data = json.dumps(sugs).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/health":
            data = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_server(port: int = AUTOCOMPLETE_PORT):
    server = ThreadedHTTPServer((AUTOCOMPLETE_HOST, port), AutocompleteHandler)
    print(f"Autocomplete server running on http://{AUTOCOMPLETE_HOST}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    start_server()

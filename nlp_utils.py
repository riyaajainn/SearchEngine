import re
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# Standardized stop words
STOPWORDS = frozenset(ENGLISH_STOP_WORDS).union({"under", "below", "above", "between", "from", "to", "and", "with"})

# Precompiled common structural regexes
RE_TOKENS = re.compile(r'[a-z0-9]+', re.I)
RE_COMPATIBLE = re.compile(r'\b(?:for|compatible with|compatible for|compatible|compitable with|compitable|suitable for|suitable|fit for|fit with|fits|designed for)\b', re.I)
STORAGE_PATTERN = re.compile(r'\b(?:\d+\s*(?:gb|tb|mb)|\d{2,4}\s*gb)\b', re.I)

# Universal product derivative terms (parts, components, protection, peripherals, accessories)
UNIVERSAL_DERIVATIVE_TERMS = frozenset({
    # Power, charging & connectivity
    "battery", "batteries", "charger", "chargers", "adapter", "adapters", "cable", "cables",
    "cord", "cords", "wire", "wires", "connector", "connectors", "plug", "plugs", "jack", "jacks",
    # Protection, cases & skins
    "case", "cases", "cover", "covers", "skin", "skins", "guard", "guards", "glass", "glasses",
    "tempered", "protector", "protectors", "film", "films", "shield", "shields", "sleeve", "sleeves",
    "pouch", "pouches", "bag", "bags", "backpack", "backpacks", "sticker", "stickers", "decal", "decals", "wrap", "wraps",
    # Mounting, stands & holding
    "stand", "stands", "holder", "holders", "mount", "mounts", "bracket", "brackets", "tripod", "tripods",
    "dock", "docks", "cradle", "cradles", "strap", "straps", "band", "bands", "clip", "clips", "hook", "hooks",
    "trolley", "trolleys", "cart", "carts", "tray", "trays",
    # Peripherals, optics & solar
    "lens", "lenses", "panel", "panels", "mouse", "keyboard", "soundbar",
    # Replacement parts, spares & internal components
    "replacement", "replacements", "spare", "spares", "part", "parts", "fan", "fans", "hinge", "hinges",
    "keycap", "keycaps", "motherboard", "motherboards", "bezel", "bezels", "housing", "casing",
    "screw", "screws", "filter", "filters", "cartridge", "cartridges", "toner", "toners", "ink", "inks",
    "thermostat", "thermostats", "valve", "valves", "compressor", "compressors", "condenser", "condensers",
    "coil", "coils", "motor", "motors", "pcb", "pcbs", "gasket", "gaskets", "defrost", "relay", "relays",
    # Automotive / vehicle parts
    "headlight", "headlights", "horn", "horns", "lamp", "lamps", "bulb", "bulbs", "mirror", "mirrors",
    "bumper", "bumpers", "mudguard", "mudguards", "reflector", "reflectors",
    # Manuals / books / tutorials
    "manual", "manuals", "guide", "guides", "tutorial", "tutorials", "handbook", "handbooks", "book", "books",
    "teach", "training", "course", "courses"
})

PRICE_PATTERNS = [
    re.compile(r'\b(?:between|from)\s+(?:rs\.?|inr|₹)?\s*(\d+)\s*(?:and|to|-)\s*(?:rs\.?|inr|₹)?\s*(\d+)\b', re.I),
    re.compile(r'\b(?:rs\.?|inr|₹)?\s*(\d+)\s*(?:to|-)\s*(?:rs\.?|inr|₹)?\s*(\d+)\b', re.I),
    re.compile(r'\b(?:under|below|less\s+than|within|upto|up\s+to|<=?)\s*(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\b', re.I),
    re.compile(r'\b(?:above|over|more\s+than|greater\s+than|>=?)\s*(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\b', re.I)
]


def clean_and_normalize_text(text: str) -> str:
    """Normalize text to lowercase and stripped string."""
    return text.lower().strip() if text else ""


def clean_title(t: str) -> str:
    """
    Generalized title cleaning that preserves core brand/model phrases
    and eliminates bracketed specs, technical details, seller spam, and truncation markers.
    """
    if not t:
        return ""
    # 1. Strip non-ASCII emojis / unicode junk
    clean = re.sub(r'[^\x00-\x7F]+', ' ', t)
    # 2. Remove balanced parentheticals and brackets e.g. (12GB RAM, 256GB, Black) or [MAKSAFE]
    clean = re.sub(r'[\(\[\{].*?[\)\]\}]', '', clean)
    # 3. Remove unclosed trailing bracket or parenthesis
    clean = re.sub(r'[\(\[\{].*$', '', clean)
    # 4. Remove trailing ellipses or ...more
    clean = re.sub(r'\.{2,}.*$', '', clean)
    # 5. Cut off at natural delimiters (commas, pipes, colons, em-dashes, slashes)
    first_part = re.split(r'[,|:\-–/]', clean)[0].strip()
    # 6. Cut off seller marketing phrases
    first_part = re.split(r'\b(?:compatible with|designed for|with inside|pack of)\b', first_part, flags=re.I)[0].strip()
    # 7. Normalize whitespace
    first_part = re.sub(r'\s+', ' ', first_part).strip()
    # 8. Clean up trailing conjunctions or prepositions
    first_part = re.sub(r'\b(?:for|with|and|by|of|in|to)$', '', first_part, flags=re.I).strip()
    
    words = first_part.split()
    if len(words) > 8:
        first_part = " ".join(words[:7])
    return first_part


def get_word_variants(token: str) -> set:
    """Generate singular, plural, and common e-commerce acronym/synonym forms."""
    t = token.lower()
    variants = {t}
    if t in ("tv", "tvs", "television", "televisions"):
        variants.update({"tv", "tvs", "television", "televisions"})
    elif t in ("fridge", "fridges", "refrigerator", "refrigerators"):
        variants.update({"fridge", "fridges", "refrigerator", "refrigerators"})
    elif t in ("ac", "acs"):
        variants.update({"ac", "acs", "air conditioner", "air conditioners"})
    elif t.endswith("ies") and len(t) > 4:
        variants.add(t[:-3] + "y")
    elif t.endswith("es") and len(t) > 4 and (t[-3] in ("s", "x", "z") or t.endswith(("ches", "shes"))):
        variants.add(t[:-2])
    elif t.endswith("s") and len(t) > 3 and not t.endswith("ss"):
        variants.add(t[:-1])
    else:
        if t.endswith("y") and len(t) > 2 and t[-2] not in "aeiou":
            variants.add(t[:-1] + "ies")
        elif t.endswith(("s", "x", "z", "ch", "sh")):
            variants.add(t + "es")
        else:
            variants.add(t + "s")
    return variants


def extract_demographic(text: str) -> tuple:
    """Dynamically determine demographic intent ('men', 'women', 'kids') from tokens."""
    tokens = set(RE_TOKENS.findall(text.lower()))
    for t in tokens:
        variants = get_word_variants(t)
        if any(v in ("men", "man", "male", "gents", "boy", "boys") for v in variants):
            return "men", t
        if any(v in ("women", "woman", "female", "ladies", "girl", "girls") for v in variants):
            return "women", t
        if any(v in ("kids", "kid", "children", "child", "baby", "toddler") for v in variants):
            return "kids", t
    return None, None


def parse_price_intent(query_str: str) -> tuple:
    """Extract natural language price filters from search query."""
    q = query_str
    min_price = None
    max_price = None

    # Between X and Y
    m = PRICE_PATTERNS[0].search(q)
    if m:
        min_price = float(m.group(1))
        max_price = float(m.group(2))
        q = q[:m.start()] + " " + q[m.end():]
    else:
        # X to Y
        m = PRICE_PATTERNS[1].search(q)
        if m:
            min_price = float(m.group(1))
            max_price = float(m.group(2))
            q = q[:m.start()] + " " + q[m.end():]
        else:
            # Under X
            m = PRICE_PATTERNS[2].search(q)
            if m:
                max_price = float(m.group(1))
                q = q[:m.start()] + " " + q[m.end():]
            else:
                # Above X
                m = PRICE_PATTERNS[3].search(q)
                if m:
                    min_price = float(m.group(1))
                    q = q[:m.start()] + " " + q[m.end():]

    if min_price is not None and max_price is not None and min_price > max_price:
        min_price, max_price = max_price, min_price

    clean_q = re.sub(r'\s+', ' ', q).strip()
    return clean_q, min_price, max_price


def query_has_variant_or_color(query_str: str) -> bool:
    """Check if user query explicitly specifies a concrete storage or numerical variant."""
    return bool(STORAGE_PATTERN.search(query_str.lower()))


def is_compatibility_stuffed(title: str, query_tokens: list, derivative_words: set = None) -> bool:
    """
    Data-driven keyword stuffing and multi-model compatibility detector.
    If the user's queried product token repeats multiple times in the title:
    - Repeated 3+ times unconditionally (blatant SEO spam)
    - Repeated 2+ times with any derivative/accessory keyword, relational markers,
      or multi-model conjunction phrases (e.g. 'MacBook Pro & MacBook Air', 'iPhone 15 & iPhone 16')
    it is a third-party accessory, tutorial guide/book, or compatibility listing rather than the genuine product.
    """
    if not query_tokens or not title:
        return False
    t_lower = title.lower()
    derivs = set(derivative_words).union(UNIVERSAL_DERIVATIVE_TERMS) if derivative_words else set(UNIVERSAL_DERIVATIVE_TERMS)

    for tok in query_tokens:
        if len(tok) < 3 and not tok.isdigit():
            continue
        occurrences = len(re.findall(r'\b' + re.escape(tok) + r'\b', t_lower))
        if occurrences >= 2:
            # 1. Unconditional spam: repeated 3+ times
            if occurrences >= 3:
                return True
            # 2. Repeated 2 times AND contains any accessory/derivative word, relational marker, or multi-model joiner
            has_deriv = any(bool(re.search(r'\b' + re.escape(d) + r'\b', t_lower)) for d in derivs)
            has_relational = bool(re.search(r'\b(?:for|suitable for|compatible with|fit for)\b', t_lower))
            has_multi_model = bool(re.search(r'\b' + re.escape(tok) + r'\b.*?[\&\|/,\+]\s*.*?\b' + re.escape(tok) + r'\b', t_lower))
            if has_deriv or has_relational or has_multi_model:
                return True

    if len(query_tokens) >= 2:
        q_phrase = " ".join(query_tokens)
        if len(re.findall(re.escape(q_phrase), t_lower)) >= 2:
            return True

    return False


RE_COMBO_WORDS = re.compile(r'\b(?:combo|bundle|pack\s+of\s+\d+|set\s+of\s+\d+)\b', re.I)
RE_CONJUNCTION = re.compile(r'\bwith\b|\s\+\s|(?:\s|^)&\s|\band\b', re.I)


def is_combo_listing(
    title: str,
    query_tokens: list = None,
    derivative_words: set = None,
    core_device_words: set = None
) -> bool:
    """
    Universally determine if a product title represents a bundled combo package containing
    a primary product along with secondary accessories or items across all categories.
    Zero hardcoded models, brands, or categories.
    """
    if not title:
        return False
    t_lower = title.lower()

    # 1. Explicit combo or bundle keyword across all product categories
    if RE_COMBO_WORDS.search(t_lower):
        return True

    # 2. Conjunction patterns joining multiple distinct products: "with ...", " + ...", " & ..."
    if re.search(r'\b(?:with|\+|&)\b', t_lower):
        parts = re.split(r'\bwith\b|\s\+\s|(?:\s|^)&\s', t_lower, maxsplit=1)
        prefix_tokens = set(RE_TOKENS.findall(parts[0]))
        suffix_tokens = set(RE_TOKENS.findall(parts[1])) if len(parts) > 1 else set()

        derivs = set(derivative_words) if derivative_words else set(UNIVERSAL_DERIVATIVE_TERMS)
        core_voc = set(core_device_words) if core_device_words else set()
        q_toks = set(query_tokens) if query_tokens else set()

        # If suffix contains any derivative / accessory / peripheral / bundle item
        if suffix_tokens.intersection(derivs):
            # And prefix has meaningful product or model tokens
            if len(prefix_tokens - STOPWORDS) >= 1:
                return True

        # If both sides contain distinct device, category, or query nouns (e.g. Inverter + Battery, Camera + Lens)
        if (prefix_tokens.intersection(q_toks) or prefix_tokens.intersection(core_voc)) and \
           (suffix_tokens.intersection(derivs) or suffix_tokens.intersection(core_voc)):
            return True

    return False


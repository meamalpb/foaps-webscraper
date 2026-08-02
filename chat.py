import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

import anthropic
from anthropic import AnthropicBedrockMantle
from dotenv import load_dotenv
from fastapi import APIRouter
from pydantic import BaseModel
from rapidfuzz import fuzz, process

from categories import ALL_SUBCATEGORIES, MAIN_CATEGORIES, format_taxonomy_for_prompt
from db import get_recent_messages, save_message

load_dotenv()

PRODUCTS_JSON = Path("results/products.json")
EXPIRES_BY_FORMAT = "%d-%m-%Y"
MODEL = os.environ.get("CLAUDE_MODEL", "anthropic.claude-opus-4-8")
AWS_REGION = os.environ.get("AWS_REGION")
MAX_RETRIES = 3
REQUEST_TIMEOUT_SECONDS = 60
CLASSIFY_MAX_TOKENS = 1024
REPLY_MAX_TOKENS = 1024
MAX_MATCHES = 10
FUZZY_SCORE_CUTOFF = 70
MAX_HISTORY_EXCHANGES = 5  # how many past user+reply pairs feed back in as conversation context

PLAIN_TEXT_INSTRUCTION = (
    "Reply in plain text only - no markdown asterisks or # headers. Plain "
    "hyphen bullet points (\"- item\") and line breaks are fine when "
    "organizing a list."
)

SOUQ_AI_DESCRIPTION = (
    "Souq AI is an AI tool for getting the product deals available in your "
    "city. Currently it is in beta version and supports english and arabic. "
    "The Pro plan costs 10 USD per month"
)

client = AnthropicBedrockMantle(aws_region=AWS_REGION, timeout=REQUEST_TIMEOUT_SECONDS)

ONBOARDING_TOOL = {
    "name": "classify_onboarding",
    "description": (
        "The user is asking about Souq AI itself - what it is, what it does, "
        "pricing, languages supported, etc. Use for general/meta questions "
        "about the assistant/product, not for product searches."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

SEARCH_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "One product/keyword the user is searching for, e.g. 'rice' or 'milk'.",
        },
        "category": {
            "type": ["string", "null"],
            "enum": MAIN_CATEGORIES + [None],
            "description": (
                "Main product category (from the provided taxonomy) that best "
                "matches this item, if it's category-level (e.g. 'phone', "
                "'dairy'); null if it doesn't clearly map to one."
            ),
        },
        "subcategory": {
            "type": ["string", "null"],
            "enum": ALL_SUBCATEGORIES + [None],
            "description": (
                "Subcategory (from the provided taxonomy, under the chosen "
                "category) that best matches this item, if applicable; null "
                "otherwise."
            ),
        },
    },
    "required": ["query", "category", "subcategory"],
}

PRODUCT_SEARCH_TOOL = {
    "name": "classify_product_search",
    "description": (
        "The user is looking for one or more specific products or product "
        "categories (e.g. 'do you have any deals on rice', 'looking for a "
        "TV', or a list like 'rice, milk, and a phone charger'). Extract one "
        "'items' entry per distinct product/need mentioned, and a city to "
        "filter by if mentioned."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": SEARCH_ITEM_SCHEMA,
                "description": "One entry per distinct product or need mentioned in the message.",
            },
            "city": {
                "type": ["string", "null"],
                "description": "City the user wants results for, if mentioned; null otherwise.",
            },
            "near_me": {
                "type": "boolean",
                "description": (
                    "True if the user is asking for the nearest store or "
                    "deals near their current location (e.g. 'best deals "
                    "near me', 'closest place to buy milk', 'nearest Hyper "
                    "Panda'); false otherwise."
                ),
            },
        },
        "required": ["items", "city", "near_me"],
    },
}

PRICE_COMPARE_TOOL = {
    "name": "classify_price_compare",
    "description": (
        "The user wants to compare prices of one or more products across "
        "retailers and/or cities (e.g. 'where is milk cheapest', 'compare "
        "iPhone prices', or 'compare prices for rice and milk'). Extract one "
        "'items' entry per distinct product mentioned, and city filters if "
        "mentioned."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": SEARCH_ITEM_SCHEMA,
                "description": "One entry per distinct product mentioned in the message.",
            },
            "cities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Cities to restrict the comparison to, if mentioned; empty means no restriction.",
            },
            "near_me": {
                "type": "boolean",
                "description": (
                    "True if the user is asking to compare based on nearest "
                    "store or proximity to their current location; false "
                    "otherwise."
                ),
            },
        },
        "required": ["items", "cities", "near_me"],
    },
}

STORE_INFO_TOOL = {
    "name": "classify_store_info",
    "description": (
        "The user is asking for details (address, location) about a "
        "specific store, including follow-ups like 'give me the address of "
        "that store' referring to a store named earlier in the "
        "conversation. Not for product searches or price comparisons."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "store_name": {
                "type": "string",
                "description": (
                    "The store the user is asking about, resolved from the "
                    "message itself or, for a follow-up like 'that store', "
                    "from the most recently mentioned store in the recent "
                    "conversation transcript. Prior replies often mention "
                    "both a retailer/chain name and a specific branch name "
                    "together (e.g. 'at Hyper Panda (Panda N2 Mall, "
                    "Jeddah)') - always prefer the more specific branch "
                    "name ('Panda N2 Mall'), not the general chain name "
                    "('Hyper Panda'), since that's the actual location being "
                    "discussed. Empty string if nothing can be resolved."
                ),
            },
        },
        "required": ["store_name"],
    },
}

INVALID_TOOL = {
    "name": "classify_invalid",
    "description": (
        "The message is off-topic, not answerable in the context of Souq "
        "AI (a product-deals assistant), or otherwise doesn't fit the "
        "other categories. Use this as the fallback."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

CLASSIFICATION_TOOLS = [
    ONBOARDING_TOOL, PRODUCT_SEARCH_TOOL, PRICE_COMPARE_TOOL, STORE_INFO_TOOL, INVALID_TOOL,
]

TOOL_NAME_TO_CATEGORY = {
    "classify_onboarding": "Onboarding",
    "classify_product_search": "ProductSearch",
    "classify_price_compare": "PriceCompare",
    "classify_store_info": "StoreInfo",
    "classify_invalid": "Invalid",
}

CLASSIFY_PROMPT = (
    "You are classifying messages sent to Souq AI, a chatbot that helps "
    "users find product deals and compare prices in their city. Pick "
    "exactly one of the available tools that best matches the user's "
    "message: classify_onboarding for questions about Souq AI itself, "
    "classify_product_search for someone looking for products, "
    "classify_price_compare for someone wanting to compare prices across "
    "stores/cities, classify_store_info for someone asking for details "
    "(like an address) about a specific store - including a bare follow-up "
    "like 'what's the address of that store', which refers to a store "
    "named earlier in the conversation - or classify_invalid for anything "
    "else.\n\n"
    "For classify_product_search and classify_price_compare, also resolve "
    "the user's request onto this exact product taxonomy when it clearly "
    "maps to a category (e.g. 'smartphone', 'android phone', and 'cell "
    "phone' should all resolve to category 'Electronics', subcategory "
    "'Smartphones'); leave category/subcategory null if nothing fits well:\n\n"
    f"{format_taxonomy_for_prompt()}\n\n"
    "For classify_product_search and classify_price_compare, also set "
    "near_me to true if the user is asking for the nearest store or deals "
    "near their current location (e.g. 'best deals near me', 'closest "
    "place to buy milk', 'what's the nearest Hyper Panda') - this is valid "
    "even with an empty items list, if no specific product was mentioned.\n\n"
    "Recent conversation (may be empty, most recent last):\n{transcript}\n\n"
    "User message: {message}"
)

_products_cache = {"mtime": None, "size": None, "data": [], "stores_index": {}}


def is_expired(expires_by):
    if not expires_by:
        return False
    try:
        expiry_date = datetime.strptime(expires_by, EXPIRES_BY_FORMAT).date()
    except ValueError:
        return False
    return expiry_date < datetime.now().date()


def flatten_products(data):
    """Returns (flattened products, stores index). The stores index is kept
    separate - {(city, merchant_name): [stores]} - rather than denormalized
    onto every product row, so a 10k+ product catalog doesn't carry a
    repeated stores array on each entry just to support distance lookups."""
    products = []
    stores_index = {}
    for city in data.get("cities", []):
        city_name = city.get("city")
        for retailer in city.get("retailers", []):
            merchant_name = retailer.get("name")
            stores_index[(city_name, merchant_name)] = retailer.get("stores", [])
            for flyer in retailer.get("flyers", []):
                expires_by = flyer.get("expires_by")
                if is_expired(expires_by):
                    continue
                for page in flyer.get("pages", []):
                    for product in page.get("products", []):
                        products.append({
                            "name": product.get("name", ""),
                            "description": product.get("description") or "",
                            "original_price": product.get("original_price"),
                            "discounted_price": product.get("discounted_price"),
                            "category": product.get("category") or "Other",
                            "subcategory": product.get("subcategory") or "Unknown",
                            "merchant_name": merchant_name,
                            "city": city_name,
                            "expires_by": expires_by,
                        })
    return products, stores_index


def _load_data():
    try:
        stat = PRODUCTS_JSON.stat()
    except FileNotFoundError:
        return [], {}

    if _products_cache["mtime"] == stat.st_mtime and _products_cache["size"] == stat.st_size:
        return _products_cache["data"], _products_cache["stores_index"]

    try:
        with open(PRODUCTS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], {}

    flattened, stores_index = flatten_products(data)
    _products_cache["mtime"] = stat.st_mtime
    _products_cache["size"] = stat.st_size
    _products_cache["data"] = flattened
    _products_cache["stores_index"] = stores_index
    return flattened, stores_index


def load_products():
    return _load_data()[0]


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_store(city, merchant_name, latitude, longitude):
    """Returns (store_name, distance_km) for the closest of that merchant's
    stores in this city, or None if it has no store locations on file."""
    _, stores_index = _load_data()
    stores = stores_index.get((city, merchant_name)) or []
    if not stores:
        return None
    best = min(
        stores,
        key=lambda s: haversine_km(latitude, longitude, s["latitude"], s["longitude"]),
    )
    return best["name"], haversine_km(latitude, longitude, best["latitude"], best["longitude"])


def nearby_stores(latitude, longitude, limit=MAX_MATCHES):
    """All stores across all retailers/cities, nearest first - used for a
    plain 'what's the nearest store to me' query with no product mentioned."""
    _, stores_index = _load_data()
    entries = []
    for (city, merchant_name), stores in stores_index.items():
        for store in stores:
            distance = haversine_km(latitude, longitude, store["latitude"], store["longitude"])
            entries.append((distance, city, merchant_name, store))
    entries.sort(key=lambda e: e[0])
    return entries[:limit]


def all_stores():
    """Flattens stores_index into one list, each entry carrying its
    merchant/city alongside the store's own fields."""
    _, stores_index = _load_data()
    return [
        {"city": city, "merchant_name": merchant_name, **store}
        for (city, merchant_name), stores in stores_index.items()
        for store in stores
    ]


def find_store(store_name, latitude=None, longitude=None):
    """Resolves a free-text store_name to a specific store. Returns:
    - {"store": <store dict with city/merchant_name>, "distance_km": float|None} on a confident match
    - {"candidates": [...]} when it matches a merchant with multiple branches and can't narrow further
    - None when nothing matches at all."""
    stores = all_stores()
    if not store_name or not stores:
        return None

    name_lower = store_name.lower()

    def distance_to(store):
        if latitude is None or longitude is None:
            return None
        return haversine_km(latitude, longitude, store["latitude"], store["longitude"])

    def resolve(matches):
        if len(matches) == 1:
            return {"store": matches[0], "distance_km": distance_to(matches[0])}
        if latitude is not None and longitude is not None:
            best = min(matches, key=distance_to)
            return {"store": best, "distance_km": distance_to(best)}
        return {"candidates": matches[:5]}

    # Tier 1: match on the merchant/retailer name (e.g. "Hyper Panda" or
    # "Othaim") - checked first, since several branch names also happen to
    # embed the merchant name (e.g. "Hyper Panda King Road"), which would
    # otherwise make a bare merchant query only match that subset of
    # branches and miss closer ones named differently (e.g. "Panda N2 Mall").
    by_merchant = [s for s in stores if name_lower in s["merchant_name"].lower()]
    if by_merchant:
        return resolve(by_merchant)

    # Tier 2: match on the store's own branch name (substring, then fuzzy) -
    # the common case for a follow-up naming one specific branch.
    exact = [s for s in stores if name_lower in s["name"].lower()]
    if not exact:
        haystacks = [s["name"].lower() for s in stores]
        results = process.extract(
            name_lower, haystacks, scorer=fuzz.partial_ratio,
            score_cutoff=FUZZY_SCORE_CUTOFF, limit=None,
        )
        exact = [stores[idx] for _choice, _score, idx in results]

    if exact:
        return resolve(exact)

    return None


def match_by_keywords(products, keywords):
    """Substring match first (cheap, catches the common case); only pays for
    fuzzy scoring when substring matching finds nothing, so typo tolerance
    doesn't cost anything on the normal path."""
    keywords = [k for k in keywords if k]
    if not keywords or not products:
        return []

    haystacks = [f"{p['name']} {p['description']}".lower() for p in products]
    matched_idx = set()
    for keyword in keywords:
        keyword_lower = keyword.lower()
        for idx, haystack in enumerate(haystacks):
            if keyword_lower in haystack:
                matched_idx.add(idx)

    if not matched_idx:
        for keyword in keywords:
            results = process.extract(
                keyword.lower(),
                haystacks,
                scorer=fuzz.partial_ratio,
                score_cutoff=FUZZY_SCORE_CUTOFF,
                limit=None,
            )
            for _choice, _score, idx in results:
                matched_idx.add(idx)

    return [products[idx] for idx in sorted(matched_idx)]


def match_by_category(products, category, subcategory):
    """Exact match against the closed taxonomy vocabulary - cheap, and covers
    category-level phrasing ('smartphone', 'android phone') that shares no
    text with product name/description, which match_by_keywords can't reach."""
    if subcategory:
        subcategory_lower = subcategory.lower()
        return [p for p in products if p["subcategory"].lower() == subcategory_lower]
    if category:
        category_lower = category.lower()
        return [p for p in products if p["category"].lower() == category_lower]
    return []


def search_products(products, keywords, category, subcategory):
    """Union of category-taxonomy matches and keyword/fuzzy matches, so a
    category-level query ('phone') and a brand-specific one ('Xiaomi') both
    work, whether or not the classifier resolved a category."""
    by_category = match_by_category(products, category, subcategory)
    by_keyword = match_by_keywords(products, keywords)

    seen_ids = set()
    combined = []
    for product in by_category + by_keyword:
        product_id = id(product)
        if product_id not in seen_ids:
            seen_ids.add(product_id)
            combined.append(product)
    return combined


def filter_by_cities(products, cities):
    """Filters to the given cities; falls back to the unfiltered list (with a
    flag) if the filter would otherwise wipe out all matches, since a
    misspelled/unknown city shouldn't turn a real match into an empty reply."""
    cities = [c for c in cities if c]
    if not cities or not products:
        return products, False

    wanted = {c.lower() for c in cities}
    filtered = [p for p in products if p["city"] and p["city"].lower() in wanted]
    if filtered:
        return filtered, False
    return products, True


def rank_and_cap(products, latitude, longitude, near_me):
    """Sorts by nearest-store distance when a near-me location is available
    and relevant, otherwise by price (the existing default). Either way,
    caps to MAX_MATCHES and returns the total match count before capping."""
    total = len(products)
    if near_me and latitude is not None and longitude is not None:
        enriched = []
        for p in products:
            info = nearest_store(p["city"], p["merchant_name"], latitude, longitude)
            enriched.append({
                **p,
                "_nearest_store": info[0] if info else None,
                "_distance_km": info[1] if info else None,
            })
        ordered = sorted(
            enriched,
            key=lambda p: p["_distance_km"] if p["_distance_km"] is not None else float("inf"),
        )
    else:
        ordered = sorted(
            products,
            key=lambda p: p["discounted_price"] if p["discounted_price"] is not None else float("inf"),
        )
    return ordered[:MAX_MATCHES], total


def format_products_for_prompt(products, total):
    if not products:
        return "No matching products were found."

    lines = []
    for p in products:
        line = (
            f"- {p['name']} ({p['description']}) at {p['merchant_name']}, {p['city']}: "
            f"original price {p['original_price']}, discounted price {p['discounted_price']}"
        )
        if p.get("_distance_km") is not None:
            line += f" ({p['_nearest_store']}, {p['_distance_km']:.1f} km away)"
        lines.append(line)
    if total > len(products):
        lines.append(f"(showing {len(products)} of {total} matches)")
    return "\n".join(lines)


def format_nearby_stores(entries):
    if not entries:
        return "No store locations were found."
    return "\n".join(
        f"- {merchant_name} ({store['name']}, {city}): {distance:.1f} km away"
        for distance, city, merchant_name, store in entries
    )


def format_transcript(messages):
    if not messages:
        return "(none)"
    speaker = {"user": "User", "assistant": "Souq AI"}
    return "\n".join(f"{speaker.get(m['role'], m['role'])}: {m['content']}" for m in messages)


def format_item_section(query, matched, total, city_fallback, city_label):
    lines = format_products_for_prompt(matched, total)
    note = f" (no results matched {city_label}; showing all cities)" if city_fallback else ""
    return f"## {query}{note}\n{lines}"


def call_with_retry(make_request):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return make_request()
        except (
            anthropic.APIStatusError,
            anthropic.APIConnectionError,
            KeyError,
            StopIteration,
        ) as e:
            print(f"    Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    return None


def classify_message(message, transcript):
    def make_request():
        response = client.messages.create(
            model=MODEL,
            max_tokens=CLASSIFY_MAX_TOKENS,
            tools=CLASSIFICATION_TOOLS,
            tool_choice={"type": "auto"},
            messages=[{
                "role": "user",
                "content": CLASSIFY_PROMPT.format(message=message, transcript=transcript),
            }],
        )
        tool_use = next(b for b in response.content if b.type == "tool_use")
        category = TOOL_NAME_TO_CATEGORY[tool_use.name]
        return category, tool_use.input or {}

    return call_with_retry(make_request)


def generate_grounded_reply(prompt):
    def make_request():
        response = client.messages.create(
            model=MODEL,
            max_tokens=REPLY_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return next(b.text for b in response.content if b.type == "text")

    return call_with_retry(make_request)


def handle_onboarding(message, extracted, transcript, latitude=None, longitude=None):
    prompt = (
        "You are Souq AI's assistant. Answer the user's question about Souq "
        "AI using ONLY the following facts; do not invent features, prices, "
        f"or capabilities beyond what's stated:\n\"{SOUQ_AI_DESCRIPTION}\"\n\n"
        f"Recent conversation (may be empty):\n{transcript}\n\n"
        f"User question: \"{message}\"\n"
        f"Reply conversationally in 1-3 sentences. {PLAIN_TEXT_INSTRUCTION}"
    )
    return generate_grounded_reply(prompt) or SOUQ_AI_DESCRIPTION


def handle_product_search(message, extracted, transcript, latitude=None, longitude=None):
    raw_items = extracted.get("items") or []
    near_me = extracted.get("near_me", False)
    city = extracted.get("city")

    if near_me and not raw_items and latitude is not None and longitude is not None:
        entries = nearby_stores(latitude, longitude)
        context = format_nearby_stores(entries)
        prompt = (
            "You are Souq AI's shopping assistant. The user asked about the "
            f"nearest store(s) to them, without mentioning a specific product: \"{message}\".\n\n"
            f"Recent conversation (may be empty):\n{transcript}\n\n"
            f"Here are the nearest store locations:\n{context}\n\n"
            "Write a short, friendly reply listing the closest few stores "
            f"with their distance. {PLAIN_TEXT_INSTRUCTION}"
        )
        reply = generate_grounded_reply(prompt)
        return reply or "I couldn't find any nearby stores right now. Please try again later."

    items = raw_items or [{"query": message, "category": None, "subcategory": None}]
    products = load_products()
    sections = []
    any_matched = False
    for item in items:
        query = item.get("query") or message
        matched = search_products(products, [query], item.get("category"), item.get("subcategory"))
        matched, city_fallback = filter_by_cities(matched, [city] if city else [])
        matched, total = rank_and_cap(matched, latitude, longitude, near_me)
        any_matched = any_matched or bool(matched)
        sections.append(format_item_section(query, matched, total, city_fallback, f'the city "{city}"'))

    context = "\n\n".join(sections)
    prompt = (
        "You are Souq AI's shopping assistant. The user searched for: "
        f"\"{message}\".\n\n"
        f"Recent conversation (may be empty):\n{transcript}\n\n"
        f"Here are matching deals currently available, grouped by what they asked for:\n{context}\n\n"
        "Write a friendly reply organized by product/need - one short section "
        "per item requested (use the item name as a lead-in, e.g. 'Rice: ...'), "
        "listing standout options with price/store/city as plain hyphen bullet "
        "points. If an item had no matches, say so briefly under that item "
        "rather than skipping it silently. Do not invent products. "
        f"{PLAIN_TEXT_INSTRUCTION}"
    )
    reply = generate_grounded_reply(prompt)
    if reply is not None:
        return reply
    if any_matched:
        return "I found some matching products but I'm having trouble summarizing them right now."
    return "I couldn't find any matching products right now. Try a different search term or check back later."


def handle_price_compare(message, extracted, transcript, latitude=None, longitude=None):
    items = extracted.get("items") or [{"query": message, "category": None, "subcategory": None}]
    cities = extracted.get("cities") or []
    near_me = extracted.get("near_me", False)

    products = load_products()
    sections = []
    any_matched = False
    for item in items:
        query = item.get("query") or message
        matched = search_products(products, [query], item.get("category"), item.get("subcategory"))
        matched, city_fallback = filter_by_cities(matched, cities)
        matched, total = rank_and_cap(matched, latitude, longitude, near_me)
        any_matched = any_matched or bool(matched)
        sections.append(format_item_section(query, matched, total, city_fallback, "the requested cities"))

    context = "\n\n".join(sections)
    prompt = (
        "You are Souq AI's shopping assistant. The user wants to compare "
        f"prices for: \"{message}\".\n\n"
        f"Recent conversation (may be empty):\n{transcript}\n\n"
        f"Here are matching entries across stores/cities, grouped by product:\n{context}\n\n"
        "For each product group, identify the cheapest option, note any "
        "notable price gaps, and mention store/city, as a short section per "
        "product (use the product name as a lead-in). If a product had no "
        f"matches, say so clearly and do not invent prices. {PLAIN_TEXT_INSTRUCTION}"
    )
    reply = generate_grounded_reply(prompt)
    if reply is not None:
        return reply
    if any_matched:
        return "I found some matching products but I'm having trouble comparing them right now."
    return "I couldn't find any matching products to compare right now. Try a different product name or check back later."


def handle_invalid(message, extracted, transcript, latitude=None, longitude=None):
    prompt = (
        "You are Souq AI's assistant. Souq AI only helps with finding "
        "product deals, comparing prices, and answering questions about "
        "itself. The following user message doesn't fit any of that. "
        "Politely and briefly decline, and redirect them to what you can "
        f"help with.\n\nRecent conversation (may be empty):\n{transcript}\n\n"
        f"User message: \"{message}\"\n{PLAIN_TEXT_INSTRUCTION}"
    )
    fallback = (
        "I can help with finding deals, comparing prices, or answering "
        "questions about Souq AI - try asking about a product or city!"
    )
    return generate_grounded_reply(prompt) or fallback


def handle_store_info(message, extracted, transcript, latitude=None, longitude=None):
    store_name = extracted.get("store_name") or ""
    result = find_store(store_name, latitude, longitude)

    if result is None:
        prompt = (
            "You are Souq AI's assistant. The user asked for store details, "
            "but no specific store could be identified from their message or "
            f"the recent conversation.\n\nRecent conversation (may be empty):\n{transcript}\n\n"
            f"User message: \"{message}\"\n"
            "Ask them, briefly and in a friendly way, which store they mean. "
            f"{PLAIN_TEXT_INSTRUCTION}"
        )
        fallback = "Which store did you mean? Let me know the name and I'll look up its address."
        return generate_grounded_reply(prompt) or fallback

    if "candidates" in result:
        lines = "\n".join(
            f"- {s['merchant_name']} ({s['name']}), {s['city']}: {s['address']}"
            for s in result["candidates"]
        )
        prompt = (
            "You are Souq AI's assistant. The user asked for a store's "
            f"address: \"{message}\". Multiple branches matched:\n{lines}\n\n"
            "Ask them to clarify which branch they mean (or offer to narrow "
            f"it down if they share their location). {PLAIN_TEXT_INSTRUCTION}"
        )
        fallback = "There are a few matching branches:\n" + lines + "\nWhich one did you mean?"
        return generate_grounded_reply(prompt) or fallback

    store = result["store"]
    distance_note = f", {result['distance_km']:.1f} km away" if result["distance_km"] is not None else ""
    context = f"{store['merchant_name']} - {store['name']}, {store['city']}: {store['address']}{distance_note}"
    prompt = (
        "You are Souq AI's assistant. Answer the user's question about this "
        f"store using ONLY these facts, do not invent anything: {context}\n\n"
        f"Recent conversation (may be empty):\n{transcript}\n\n"
        f"User message: \"{message}\"\n"
        f"Reply in 1-2 sentences. {PLAIN_TEXT_INSTRUCTION}"
    )
    return generate_grounded_reply(prompt) or context


DISPATCH = {
    "Onboarding": handle_onboarding,
    "ProductSearch": handle_product_search,
    "PriceCompare": handle_price_compare,
    "StoreInfo": handle_store_info,
    "Invalid": handle_invalid,
}


class ChatRequest(BaseModel):
    message: str
    phone_number: str
    latitude: float | None = None
    longitude: float | None = None


class ChatResponse(BaseModel):
    category: str
    reply: str
    needs_location: bool = False


class HistoryMessage(BaseModel):
    role: str
    content: str


router = APIRouter()


LOCATION_REQUEST_REPLY = (
    "To show you the nearest deals, I need your current location - please "
    "share it and I'll take another look."
)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    history = get_recent_messages(request.phone_number, MAX_HISTORY_EXCHANGES * 2)
    transcript = format_transcript(history)

    result = classify_message(request.message, transcript)
    needs_location = False
    if result is None:
        category, reply = "Invalid", (
            "Sorry, I'm having trouble understanding right now - please try again in a moment."
        )
    else:
        category, extracted = result
        wants_nearby = category in ("ProductSearch", "PriceCompare") and extracted.get("near_me")
        has_location = request.latitude is not None and request.longitude is not None
        if wants_nearby and not has_location:
            needs_location = True
            reply = LOCATION_REQUEST_REPLY
        else:
            handler = DISPATCH.get(category, handle_invalid)
            reply = handler(request.message, extracted, transcript, request.latitude, request.longitude)

    save_message(request.phone_number, "user", request.message)
    save_message(request.phone_number, "assistant", reply)
    return ChatResponse(category=category, reply=reply, needs_location=needs_location)


@router.get("/chat/history", response_model=list[HistoryMessage])
def chat_history(phone_number: str):
    return get_recent_messages(phone_number, MAX_HISTORY_EXCHANGES * 2)

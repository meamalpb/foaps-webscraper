import json
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
        },
        "required": ["items", "city"],
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
        },
        "required": ["items", "cities"],
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

CLASSIFICATION_TOOLS = [ONBOARDING_TOOL, PRODUCT_SEARCH_TOOL, PRICE_COMPARE_TOOL, INVALID_TOOL]

TOOL_NAME_TO_CATEGORY = {
    "classify_onboarding": "Onboarding",
    "classify_product_search": "ProductSearch",
    "classify_price_compare": "PriceCompare",
    "classify_invalid": "Invalid",
}

CLASSIFY_PROMPT = (
    "You are classifying messages sent to Souq AI, a chatbot that helps "
    "users find product deals and compare prices in their city. Pick "
    "exactly one of the available tools that best matches the user's "
    "message: classify_onboarding for questions about Souq AI itself, "
    "classify_product_search for someone looking for products, "
    "classify_price_compare for someone wanting to compare prices across "
    "stores/cities, or classify_invalid for anything else.\n\n"
    "For classify_product_search and classify_price_compare, also resolve "
    "the user's request onto this exact product taxonomy when it clearly "
    "maps to a category (e.g. 'smartphone', 'android phone', and 'cell "
    "phone' should all resolve to category 'Electronics', subcategory "
    "'Smartphones'); leave category/subcategory null if nothing fits well:\n\n"
    f"{format_taxonomy_for_prompt()}\n\n"
    "Recent conversation (may be empty, most recent last):\n{transcript}\n\n"
    "User message: {message}"
)

_products_cache = {"mtime": None, "size": None, "data": []}


def is_expired(expires_by):
    if not expires_by:
        return False
    try:
        expiry_date = datetime.strptime(expires_by, EXPIRES_BY_FORMAT).date()
    except ValueError:
        return False
    return expiry_date < datetime.now().date()


def flatten_products(data):
    products = []
    for city in data.get("cities", []):
        city_name = city.get("city")
        for retailer in city.get("retailers", []):
            merchant_name = retailer.get("name")
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
    return products


def load_products():
    try:
        stat = PRODUCTS_JSON.stat()
    except FileNotFoundError:
        return []

    if _products_cache["mtime"] == stat.st_mtime and _products_cache["size"] == stat.st_size:
        return _products_cache["data"]

    try:
        with open(PRODUCTS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    flattened = flatten_products(data)
    _products_cache["mtime"] = stat.st_mtime
    _products_cache["size"] = stat.st_size
    _products_cache["data"] = flattened
    return flattened


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


def sort_and_cap(products):
    total = len(products)
    ordered = sorted(
        products,
        key=lambda p: p["discounted_price"] if p["discounted_price"] is not None else float("inf"),
    )
    return ordered[:MAX_MATCHES], total


def format_products_for_prompt(products, total):
    if not products:
        return "No matching products were found."

    lines = [
        f"- {p['name']} ({p['description']}) at {p['merchant_name']}, {p['city']}: "
        f"original price {p['original_price']}, discounted price {p['discounted_price']}"
        for p in products
    ]
    if total > len(products):
        lines.append(f"(showing {len(products)} of {total} matches)")
    return "\n".join(lines)


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


def handle_onboarding(message, extracted, transcript):
    prompt = (
        "You are Souq AI's assistant. Answer the user's question about Souq "
        "AI using ONLY the following facts; do not invent features, prices, "
        f"or capabilities beyond what's stated:\n\"{SOUQ_AI_DESCRIPTION}\"\n\n"
        f"Recent conversation (may be empty):\n{transcript}\n\n"
        f"User question: \"{message}\"\n"
        f"Reply conversationally in 1-3 sentences. {PLAIN_TEXT_INSTRUCTION}"
    )
    return generate_grounded_reply(prompt) or SOUQ_AI_DESCRIPTION


def handle_product_search(message, extracted, transcript):
    items = extracted.get("items") or [{"query": message, "category": None, "subcategory": None}]
    city = extracted.get("city")

    products = load_products()
    sections = []
    any_matched = False
    for item in items:
        query = item.get("query") or message
        matched = search_products(products, [query], item.get("category"), item.get("subcategory"))
        matched, city_fallback = filter_by_cities(matched, [city] if city else [])
        matched, total = sort_and_cap(matched)
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


def handle_price_compare(message, extracted, transcript):
    items = extracted.get("items") or [{"query": message, "category": None, "subcategory": None}]
    cities = extracted.get("cities") or []

    products = load_products()
    sections = []
    any_matched = False
    for item in items:
        query = item.get("query") or message
        matched = search_products(products, [query], item.get("category"), item.get("subcategory"))
        matched, city_fallback = filter_by_cities(matched, cities)
        matched, total = sort_and_cap(matched)
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


def handle_invalid(message, extracted, transcript):
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


DISPATCH = {
    "Onboarding": handle_onboarding,
    "ProductSearch": handle_product_search,
    "PriceCompare": handle_price_compare,
    "Invalid": handle_invalid,
}


class ChatRequest(BaseModel):
    message: str
    phone_number: str


class ChatResponse(BaseModel):
    category: str
    reply: str


class HistoryMessage(BaseModel):
    role: str
    content: str


router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    history = get_recent_messages(request.phone_number, MAX_HISTORY_EXCHANGES * 2)
    transcript = format_transcript(history)

    result = classify_message(request.message, transcript)
    if result is None:
        category, reply = "Invalid", (
            "Sorry, I'm having trouble understanding right now - please try again in a moment."
        )
    else:
        category, extracted = result
        handler = DISPATCH.get(category, handle_invalid)
        reply = handler(request.message, extracted, transcript)

    save_message(request.phone_number, "user", request.message)
    save_message(request.phone_number, "assistant", reply)
    return ChatResponse(category=category, reply=reply)


@router.get("/chat/history", response_model=list[HistoryMessage])
def chat_history(phone_number: str):
    return get_recent_messages(phone_number, MAX_HISTORY_EXCHANGES * 2)

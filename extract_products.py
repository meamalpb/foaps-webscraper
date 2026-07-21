import base64
import json
import mimetypes
import os
import time
from datetime import datetime

import anthropic
import requests
from anthropic import AnthropicBedrockMantle
from dotenv import load_dotenv

EXPIRES_BY_FORMAT = "%d-%m-%Y"

load_dotenv()

INPUT_JSON = "results/flyer_images.json"
OUTPUT_JSON = "results/products.json"
MODEL = os.environ.get("CLAUDE_MODEL", "anthropic.claude-opus-4-8")
AWS_REGION = os.environ.get("AWS_REGION")
MAX_RETRIES = 3
BATCH_SIZE = 10
REQUEST_TIMEOUT_SECONDS = 300  # 5 minutes
MAX_TOKENS = 16000

PRODUCT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "original_price": {"type": ["number", "null"]},
        "discounted_price": {"type": ["number", "null"]},
    },
    "required": ["name", "description", "original_price", "discounted_price"],
}

BATCH_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_index": {"type": "integer"},
                    "products": {
                        "type": "array",
                        "items": PRODUCT_ITEM_SCHEMA,
                    },
                },
                "required": ["page_index", "products"],
            },
        }
    },
    "required": ["pages"],
}

PRODUCT_TOOL = {
    "name": "record_products",
    "description": "Record the products extracted from each flyer page image.",
    "input_schema": BATCH_PRODUCT_SCHEMA,
}

BATCH_PROMPT = (
    "Each image below is a page from a supermarket flyer/catalogue, labeled "
    "'Page N:' immediately before it. For each page, identify every distinct "
    "product advertised on it. For each product, return its name, a short "
    "description (size/weight/pack/brand if visible), its original (crossed-out "
    "or 'before') price if shown, and its discounted ('after'/offer) price if "
    "shown. Use null for a price field that isn't visible. Prices should be "
    "numbers only (no currency symbols). Some products list multiple variants "
    "(e.g. different storage sizes or colors) as separate price rows under one "
    "shared set of spec icons/details (camera, RAM, battery, etc.) shown only "
    "once. In that case, repeat the shared specs in the description of every "
    "variant row, not just the first one — only the varying attribute (e.g. "
    "storage size) should differ between their descriptions. If a page has no "
    "identifiable products (e.g. a cover page or logo), return an empty "
    "products list for it. Call the record_products tool with one entry per "
    "page, with 'page_index' matching the page's label (1-based), covering "
    "every page provided."
)


def load_flyer_images():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_existing_output():
    if not os.path.exists(OUTPUT_JSON):
        return {"cities": []}
    with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def save_output(data):
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def is_expired(expires_by):
    """expires_by: 'DD-MM-YYYY' string, or falsy if unknown (treated as not expired)."""
    if not expires_by:
        return False
    try:
        expiry_date = datetime.strptime(expires_by, EXPIRES_BY_FORMAT).date()
    except ValueError:
        return False
    return expiry_date < datetime.now().date()


def find_or_create(items, key_field, key_value, factory):
    for item in items:
        if item[key_field] == key_value:
            return item
    item = factory()
    items.append(item)
    return item


def download_image(image_url):
    response = requests.get(image_url, timeout=30)
    response.raise_for_status()
    mime_type = response.headers.get("content-type")
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = mimetypes.guess_type(image_url)[0] or "image/webp"
    b64_data = base64.standard_b64encode(response.content).decode("utf-8")
    return b64_data, mime_type


def download_images(image_urls):
    """Downloads each image, skipping (and logging) any that fail."""
    downloaded = []
    for url in image_urls:
        try:
            b64_data, mime_type = download_image(url)
            downloaded.append((url, b64_data, mime_type))
        except requests.RequestException as e:
            print(f"    Failed to download {url}: {e}")
    return downloaded


def extract_products_from_batch(client, batch):
    """batch: list of (url, base64_data, mime_type). Returns a list of product
    lists (one per item in batch, in order), or None if the call never succeeded."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            content = [{"type": "text", "text": BATCH_PROMPT}]
            for idx, (_url, b64_data, mime_type) in enumerate(batch, start=1):
                content.append({"type": "text", "text": f"Page {idx}:"})
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime_type, "data": b64_data},
                })

            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                tools=[PRODUCT_TOOL],
                tool_choice={"type": "tool", "name": "record_products"},
                messages=[{"role": "user", "content": content}],
            )
            tool_use = next(b for b in response.content if b.type == "tool_use")
            pages = tool_use.input["pages"]
            products_by_index = {page["page_index"]: page["products"] for page in pages}
            return [products_by_index.get(idx, []) for idx in range(1, len(batch) + 1)]
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


def prune_stale_entries(output, flyer_data):
    """Remove cities/retailers/flyers/pages from output that no longer exist in
    flyer_data, plus any flyer whose expires_by date has passed."""
    valid_flyer_images = {}
    expired_flyer_urls = set()
    for city in flyer_data.get("cities", []):
        for retailer in city.get("retailers", []):
            for flyer in retailer.get("flyers", []):
                if is_expired(flyer.get("expires_by")):
                    expired_flyer_urls.add(flyer["flyer_url"])
                    continue
                valid_flyer_images[flyer["flyer_url"]] = {
                    img["url"] for img in flyer.get("images", [])
                }
    valid_retailers = {
        (city["city"], retailer["name"])
        for city in flyer_data.get("cities", [])
        for retailer in city.get("retailers", [])
    }
    valid_cities = {city["city"] for city in flyer_data.get("cities", [])}

    removed_cities = removed_retailers = removed_flyers = removed_expired_flyers = removed_pages = 0

    kept_cities = []
    for city in output.get("cities", []):
        if city["city"] not in valid_cities:
            removed_cities += 1
            continue

        kept_retailers = []
        for retailer in city.get("retailers", []):
            if (city["city"], retailer["name"]) not in valid_retailers:
                removed_retailers += 1
                continue

            kept_flyers = []
            for flyer in retailer.get("flyers", []):
                if flyer["flyer_url"] in expired_flyer_urls:
                    removed_expired_flyers += 1
                    continue

                valid_urls = valid_flyer_images.get(flyer["flyer_url"])
                if valid_urls is None:
                    removed_flyers += 1
                    continue

                pages = flyer["pages"]
                kept_pages = [p for p in pages if p["image_url"] in valid_urls]
                removed_pages += len(pages) - len(kept_pages)
                flyer["pages"] = kept_pages
                kept_flyers.append(flyer)

            retailer["flyers"] = kept_flyers
            kept_retailers.append(retailer)

        city["retailers"] = kept_retailers
        kept_cities.append(city)

    output["cities"] = kept_cities

    if removed_cities or removed_retailers or removed_flyers or removed_expired_flyers or removed_pages:
        print(
            f"Pruned {removed_cities} stale city(ies), {removed_retailers} stale retailer(s), "
            f"{removed_flyers} stale flyer(s), {removed_expired_flyers} expired flyer(s) "
            f"and {removed_pages} stale page(s) no longer in '{INPUT_JSON}'."
        )

    return output


def process_flyer(client, flyer_output, images, label):
    done_urls = {page["image_url"] for page in flyer_output["pages"]}
    pending = [image["url"] for image in images if image["url"] not in done_urls]

    if not pending:
        print(f"  {label}: nothing new ({len(images)} image(s), all processed)")
        return False

    print(f"  {label}: {len(pending)} pending image(s) of {len(images)}")

    changed = False
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch_urls = pending[batch_start:batch_start + BATCH_SIZE]
        print(f"    Batch {batch_start // BATCH_SIZE + 1}: downloading {len(batch_urls)} image(s)")

        batch = download_images(batch_urls)
        if not batch:
            print("    All downloads in this batch failed; skipping.")
            continue

        products_lists = extract_products_from_batch(client, batch)
        if products_lists is None:
            print("    Giving up on this batch after retries; skipping.")
            continue

        for (url, _b64, _mime), products in zip(batch, products_lists):
            print(f"    {url}: found {len(products)} product(s)")
            flyer_output["pages"].append({"image_url": url, "products": products})
            changed = True

    return changed


def main():
    client = AnthropicBedrockMantle(aws_region=AWS_REGION, timeout=REQUEST_TIMEOUT_SECONDS)

    flyer_data = load_flyer_images()
    output = load_existing_output()
    output = prune_stale_entries(output, flyer_data)
    save_output(output)

    for city in flyer_data.get("cities", []):
        city_name = city["city"]
        output_city = find_or_create(
            output["cities"], "city", city_name, lambda: {"city": city_name, "retailers": []}
        )

        for retailer in city.get("retailers", []):
            retailer_name = retailer["name"]
            output_retailer = find_or_create(
                output_city["retailers"], "name", retailer_name,
                lambda: {"name": retailer_name, "flyers": []},
            )

            print(f"\n{city_name} / {retailer_name}")

            for flyer in retailer.get("flyers", []):
                flyer_url = flyer["flyer_url"]
                expires_by = flyer.get("expires_by")
                images = flyer.get("images", [])

                if is_expired(expires_by):
                    print(f"  {flyer_url}: skipping (expired {expires_by})")
                    continue

                output_flyer = find_or_create(
                    output_retailer["flyers"], "flyer_url", flyer_url,
                    lambda: {"flyer_url": flyer_url, "expires_by": expires_by, "pages": []},
                )
                output_flyer["expires_by"] = expires_by

                changed = process_flyer(client, output_flyer, images, flyer_url)
                if changed:
                    save_output(output)

    print(f"\nSaved product extraction results to '{OUTPUT_JSON}'.")


if __name__ == "__main__":
    main()

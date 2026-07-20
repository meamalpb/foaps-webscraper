import json
import mimetypes
import os
import time

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

load_dotenv()

INPUT_JSON = "results/flyer_images.json"
OUTPUT_JSON = "results/products.json"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
MAX_RETRIES = 3
BATCH_SIZE = 10
REQUEST_TIMEOUT_MS = 300000  # 5 minutes

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

BATCH_PROMPT = (
    "Each image below is a page from a supermarket flyer/catalogue, labeled "
    "'Page N:' immediately before it. For each page, identify every distinct "
    "product advertised on it. For each product, return its name, a short "
    "description (size/weight/pack/brand if visible), its original (crossed-out "
    "or 'before') price if shown, and its discounted ('after'/offer) price if "
    "shown. Use null for a price field that isn't visible. Prices should be "
    "numbers only (no currency symbols). If a page has no identifiable products "
    "(e.g. a cover page or logo), return an empty products list for it. "
    "Return one entry per page in the 'pages' array, with 'page_index' matching "
    "the page's label (1-based), covering every page provided."
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


def find_or_create(items, key_field, key_value, factory):
    for item in items:
        if item[key_field] == key_value:
            return item
    item = factory()
    items.append(item)
    return item


def guess_mime_type(image_url):
    return mimetypes.guess_type(image_url)[0] or "image/webp"


def extract_products_from_batch(client, batch_urls):
    """batch_urls: list of image URLs. Returns a list of product lists (one per
    url in batch_urls, in order), or None if the call never succeeded."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            contents = [BATCH_PROMPT]
            for idx, url in enumerate(batch_urls, start=1):
                contents.append(f"Page {idx}:")
                contents.append(types.Part.from_uri(file_uri=url, mime_type=guess_mime_type(url)))

            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=BATCH_PRODUCT_SCHEMA,
                ),
            )
            pages = json.loads(response.text)["pages"]
            products_by_index = {page["page_index"]: page["products"] for page in pages}
            return [products_by_index.get(idx, []) for idx in range(1, len(batch_urls) + 1)]
        except (
            genai_errors.APIError,
            httpx.TimeoutException,
            httpx.ConnectError,
            json.JSONDecodeError,
            KeyError,
        ) as e:
            print(f"    Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    return None


def prune_stale_entries(output, flyer_data):
    """Remove cities/retailers/flyers/pages from output that no longer exist in flyer_data."""
    valid_flyer_images = {}
    for city in flyer_data.get("cities", []):
        for retailer in city.get("retailers", []):
            for flyer in retailer.get("flyers", []):
                valid_flyer_images[flyer["flyer_url"]] = {
                    img["url"] for img in flyer.get("images", [])
                }
    valid_retailers = {
        (city["city"], retailer["name"])
        for city in flyer_data.get("cities", [])
        for retailer in city.get("retailers", [])
    }
    valid_cities = {city["city"] for city in flyer_data.get("cities", [])}

    removed_cities = removed_retailers = removed_flyers = removed_pages = 0

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

    if removed_cities or removed_retailers or removed_flyers or removed_pages:
        print(
            f"Pruned {removed_cities} stale city(ies), {removed_retailers} stale retailer(s), "
            f"{removed_flyers} stale flyer(s) and {removed_pages} stale page(s) "
            f"no longer in '{INPUT_JSON}'."
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
        print(f"    Batch {batch_start // BATCH_SIZE + 1}: processing {len(batch_urls)} image(s)")

        products_lists = extract_products_from_batch(client, batch_urls)
        if products_lists is None:
            print("    Giving up on this batch after retries; skipping.")
            continue

        for url, products in zip(batch_urls, products_lists):
            print(f"    {url}: found {len(products)} product(s)")
            flyer_output["pages"].append({"image_url": url, "products": products})
            changed = True

    return changed


def main():
    client = genai.Client(http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))

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
                images = flyer.get("images", [])

                output_flyer = find_or_create(
                    output_retailer["flyers"], "flyer_url", flyer_url,
                    lambda: {"flyer_url": flyer_url, "pages": []},
                )

                changed = process_flyer(client, output_flyer, images, flyer_url)
                if changed:
                    save_output(output)

    print(f"\nSaved product extraction results to '{OUTPUT_JSON}'.")


if __name__ == "__main__":
    main()

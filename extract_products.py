import json
import mimetypes
import os
import time

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

load_dotenv()

INPUT_JSON = "results/flyer_images.json"
OUTPUT_JSON = "results/products.json"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
MAX_RETRIES = 3

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "original_price": {"type": ["number", "null"]},
                    "discounted_price": {"type": ["number", "null"]},
                },
                "required": [
                    "name",
                    "description",
                    "original_price",
                    "discounted_price",
                ],
            },
        }
    },
    "required": ["products"],
}

PROMPT = (
    "This image is a page from a supermarket flyer/catalogue. "
    "Identify every distinct product advertised on it. For each product, "
    "return its name, a short description (size/weight/pack/brand if visible), "
    "its original (crossed-out or 'before') price if shown, and its discounted "
    "('after'/offer) price if shown. Use null for a price field that isn't visible. "
    "Prices should be numbers only (no currency symbols). If the image has no "
    "identifiable products (e.g. a cover page or logo), return an empty products list."
)


def load_flyer_images():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_existing_output():
    if not os.path.exists(OUTPUT_JSON):
        return {}
    with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def save_output(data):
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def download_image(image_url):
    response = requests.get(image_url, timeout=30)
    response.raise_for_status()
    mime_type = response.headers.get("content-type")
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = mimetypes.guess_type(image_url)[0] or "image/webp"
    return response.content, mime_type


def extract_products_from_image(client, image_url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            image_bytes, mime_type = download_image(image_url)
            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    PROMPT,
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=PRODUCT_SCHEMA,
                ),
            )
            return json.loads(response.text)["products"]
        except (genai_errors.APIError, requests.RequestException, json.JSONDecodeError, KeyError) as e:
            print(f"    Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    return None


def prune_stale_entries(output, flyer_data):
    """Remove retailers/pages from output whose image URL no longer exists in flyer_data."""
    removed_retailers = 0
    removed_pages = 0

    for retailer_name in list(output.keys()):
        if retailer_name not in flyer_data:
            del output[retailer_name]
            removed_retailers += 1
            continue

        current_urls = {img["url"] for img in flyer_data[retailer_name].get("images", [])}
        pages = output[retailer_name]["pages"]
        kept_pages = [page for page in pages if page["image_url"] in current_urls]
        removed_pages += len(pages) - len(kept_pages)
        output[retailer_name]["pages"] = kept_pages

    if removed_retailers or removed_pages:
        print(
            f"Pruned {removed_retailers} stale retailer(s) and "
            f"{removed_pages} stale page(s) no longer in '{INPUT_JSON}'."
        )

    return output


def main():
    client = genai.Client()

    flyer_data = load_flyer_images()
    output = load_existing_output()
    output = prune_stale_entries(output, flyer_data)
    save_output(output)

    for retailer_name, retailer_data in flyer_data.items():
        images = retailer_data.get("images", [])
        print(f"\n{retailer_name}: {len(images)} image(s)")

        retailer_output = output.setdefault(
            retailer_name,
            {"flyer_url": retailer_data.get("flyer_url"), "pages": []},
        )
        done_urls = {page["image_url"] for page in retailer_output["pages"]}

        for i, image in enumerate(images, start=1):
            image_url = image["url"]

            if image_url in done_urls:
                print(f"  [{i}/{len(images)}] Skipping (already processed)")
                continue

            print(f"  [{i}/{len(images)}] Processing {image_url}")
            products = extract_products_from_image(client, image_url)

            if products is None:
                print("    Giving up on this image after retries; skipping.")
                continue

            print(f"    Found {len(products)} product(s)")
            retailer_output["pages"].append(
                {"image_url": image_url, "products": products}
            )
            save_output(output)

    print(f"\nSaved product extraction results to '{OUTPUT_JSON}'.")


if __name__ == "__main__":
    main()

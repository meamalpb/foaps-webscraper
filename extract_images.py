import json
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time

INPUT_JSON = "results/flyers_first.json"
OUTPUT_JSON = "results/flyer_images.json"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}


def get_first_flyer():
    """Read flyers_first.json and return the first flyer URL."""

    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    retailer_name = next(iter(data))
    retailer = data[retailer_name]

    if not retailer["flyers"]:
        raise Exception("No flyers found.")

    flyer = retailer["flyers"][0]

    return retailer_name, flyer["url"]


def fetch_html(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 1200},
            locale="en-US",
            extra_http_headers=HEADERS,
        )

        page = context.new_page()

        print("Opening:", url)

        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000
        )

        print("Response:", response.status if response else None)
        print("Title:", page.title())
        print("Current URL:", page.url)

        page.wait_for_timeout(5000)

        html = page.content()

        print("HTML length:", len(html))

        with open("htmls/debug.html", "w", encoding="utf8") as f:
            f.write(html)
        browser.close()
        return html


def extract_images(html):
    """Extract every image inside the flyer slider."""

    soup = BeautifulSoup(html, "html.parser")

    slider = soup.find("ul", id="slider1")

    if slider is None:
        print("Could not find slider1.")
        return []

    images = []
    seen = set()

    for img in slider.find_all("img"):

        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy")
        )

        if not src:
            continue

        if src in seen:
            continue

        seen.add(src)

        images.append({
            "url": src,
            "alt": img.get("alt", "").strip()
        })

    return images


def main():
    start_time = time.perf_counter()

    retailer_name, flyer_url = get_first_flyer()

    print("Retailer:", retailer_name)
    print("Flyer:", flyer_url)

    html = fetch_html(flyer_url)

    images = extract_images(html)

    print(f"Found {len(images)} images")

    output = {
        "city": "Jeddah",
        "retailers": {
            retailer_name: [
                {
                    "flyer_url": flyer_url,
                    "image_count": len(images),
                    "images": images
                }
            ]}}

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    print(f"Saved image list to {OUTPUT_JSON}")
    end_time = time.perf_counter()
    print(f"\nTotal execution time: {end_time - start_time:.2f} seconds")


if __name__ == "__main__":
    main()
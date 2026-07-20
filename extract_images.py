import json
import os

from dotenv import load_dotenv

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
load_dotenv()


ENV = os.getenv("ENV", "prod").lower()
NUMBER_OF_RETAILERS = int(os.getenv("NUMBER_OF_RETAILERS", "999999"))
NUMBER_OF_FLYERS = int(os.getenv("NUMBER_OF_FLYERS", "999999"))
NUMBER_OF_IMAGES = int(os.getenv("NUMBER_OF_IMAGES", "999999"))
INPUT_JSON = "results/flyers.json"
OUTPUT_JSON = "results/flyer_images.json"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}


def get_retailers():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    retailers = data["retailers"]

    if ENV == "dev":
        retailers = retailers[:NUMBER_OF_RETAILERS]

    return retailers

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

        if not src or src in seen:
            continue

        seen.add(src)

        images.append({
            "url": src,
            "alt": img.get("alt", "").strip()
        })

        if ENV == "dev" and len(images) >= NUMBER_OF_IMAGES:
            break

    return images

def main():
    start_time = time.perf_counter()

    retailers = get_retailers()

    output = {
        "cities": [
            {
                "city": "Jeddah",
                "retailers": []
            }
        ]
    }

    for retailer in retailers:

        print(f"\nRetailer: {retailer['name']}")

        retailer_output = {
            "name": retailer["name"],
            "flyers": []
        }

        flyers = retailer["flyers"]

        if ENV == "dev":
            flyers = flyers[:NUMBER_OF_FLYERS]

        for flyer in flyers:

            flyer_url = flyer["url"]
            expires_by = flyer["valid_till"]

            print(f"  Flyer: {flyer_url}")

            html = fetch_html(flyer_url)

            images = extract_images(html)

            print(f"    Found {len(images)} images")

            retailer_output["flyers"].append({
                "flyer_url": flyer_url,
                "image_count": len(images),
                "expires_by": expires_by,
                "images": images
            })

        output["cities"][0]["retailers"].append(retailer_output)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    print(f"\nSaved image list to {OUTPUT_JSON}")

    end_time = time.perf_counter()
    print(f"\nTotal execution time: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    main()
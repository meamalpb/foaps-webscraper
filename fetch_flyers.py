"""
fetch_first_flyers.py

Standalone script (separate from main.py) that:
  1. Reads supermarkets.json
  2. Takes ONLY the first retailer entry in it
  3. Fetches that retailer's page live using Playwright (JS-rendered)
  4. Extracts flyer links from it
  5. Saves the raw HTML too, so if extraction finds 0 results we can
     actually see what the live page looked like and fix the regex.

Run:
    python3 fetch_first_flyers.py
"""

import os
import re
import json
import time
from datetime import datetime
from bs4 import BeautifulSoup

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
SUPERMARKETS_JSON = "results/supermarkets.json"
OUTPUT_JSON = "results/flyers.json"
DEBUG_HTML_FILE = "htmls/debug_first_retailer_live.html"

# Playwright will handle its own modern User-Agent strings natively, 
# but we maintain a record for custom network setups if ever needed.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}


def get_retailers():
    """
    Reads supermarkets.json.

    If ENV=dev:
        returns the first NUMBER_OF_RETAILERS retailers.

    Otherwise:
        returns all retailers.
    """
    if not os.path.exists(SUPERMARKETS_JSON):
        raise FileNotFoundError(
            f"'{SUPERMARKETS_JSON}' not found. Run main.py first."
        )

    with open(SUPERMARKETS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        raise ValueError(f"'{SUPERMARKETS_JSON}' is empty.")

    retailers = list(data.items())

    env = os.getenv("ENV", "prod").lower()

    if env == "dev":
        limit = int(os.getenv("NUMBER_OF_RETAILERS", "1"))
        retailers = retailers[:limit]

    return retailers


def fetch_live_html(url):
    """Fetches a single URL live using Playwright. Returns (html_text, status_code) or (None, status_code/None)."""
    print(f"Fetching live HTML via Playwright from: {url}")
    try:
        with sync_playwright() as p:
            # Launch Chromium in headless mode
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(extra_http_headers=HEADERS)
            page = context.new_page()
            
            # Visit the retailer URL and wait for network activity to settle down
            response = page.goto(url, wait_until="load")
            
            # FIX: Playwright uses .status instead of .status_code
            status_code = response.status if response else None
            
            # Explicitly wait an additional 3-5 seconds (using 4 seconds as a safe median)
            time.sleep(4)
            
            # Capture the fully rendered DOM content
            html_text = page.content()
            
            print(f"\nHTTP status: {status_code}, content length: {len(html_text)} chars")
            
            browser.close()
            
            if status_code == 200:
                return html_text, status_code
            else:
                print(f"  Warning: non-200 status code ({status_code}).")
                return html_text, status_code
                
    except Exception as e:
        print(f"  Error fetching page with Playwright: {e}")
        return None, None


def save_debug_html(html):
    """Writes the raw fetched HTML to disk so we can inspect it manually if extraction fails."""
    os.makedirs(os.path.dirname(DEBUG_HTML_FILE), exist_ok=True)
    with open(DEBUG_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved raw fetched HTML to '{DEBUG_HTML_FILE}' for inspection.")


from bs4 import BeautifulSoup

def extract_flyers_strict(html):
    flyers = []
    seen = set()

    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a"):
        if a.get("title") != "Click here to view offers":
            continue

        if "product-image" not in a.get("class", []):
            continue

        href = a.get("href")
        if not href or href.startswith("javascript:") or href in seen:
            continue

        seen.add(href)

        # Look for the validity text in the same flyer card
        valid_till = None
        container = a.parent

        while container:
            span = container.find("span", class_="validTill")
            if span:
                text = span.get_text(" ", strip=True)

                # Extract "Jul 21, 2026"
                m = re.search(r'([A-Za-z]{3}\s+\d{1,2},\s+\d{4})', text)
                if m:
                    valid_till = datetime.strptime(
                        m.group(1),
                        "%b %d, %Y"
                    ).strftime("%d-%m-%Y")
                break

            container = container.parent

        flyers.append({
            "id": a.get("id"),
            "url": href,
            "valid_till": valid_till
        })

    return flyers


def run_diagnostics(html):
    """Prints some quick stats to help explain a 0-result extraction."""
    total_a_tags = len(re.findall(r"<a\b[^>]*>", html, re.IGNORECASE))
    tags_with_flyers_href = len(re.findall(r'href\s*=\s*["\'][^"\']*/flyers/[^"\']+["\']', html))
    tags_with_click_title = html.count("Click here to view offers")
    tags_with_product_image_class = html.count('class="product-image"')

    print("\n--- Diagnostics on fetched HTML ---")
    print(f"  Total <a> tags found:                         {total_a_tags}")
    print(f"  <a> hrefs containing '/flyers/':               {tags_with_flyers_href}")
    print(f"  Occurrences of 'Click here to view offers':    {tags_with_click_title}")
    print(f"  Occurrences of class=\"product-image\":          {tags_with_product_image_class}")
    print("------------------------------------\n")


def main():
    start_time = time.perf_counter()

    retailers = get_retailers()

    print(f"Processing {len(retailers)} retailer(s)...")

    output = {
        "retailers": []
    }

    for index, (name, url) in enumerate(retailers, start=1):
        print(f"\n[{index}/{len(retailers)}] Processing {name}")

        html, status_code = fetch_live_html(url)

        if not html:
            print(f"Could not fetch {name} (status={status_code})")
            continue

        save_debug_html(html)

        strict_flyers = extract_flyers_strict(html)

        if strict_flyers:
            flyers = strict_flyers
            method = "strict"
        else:
            flyers = []
            method = "none"

        output["retailers"].append({
            "name": name,
            "retailer_url": url,
            "extraction_method": method,
            "flyers": flyers
        })

        print(f"Found {len(flyers)} flyer(s).")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    end_time = time.perf_counter()

    print(f"\nSaved {len(output['retailers'])} retailer(s) to '{OUTPUT_JSON}'.")
    print(f"Total execution time: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    main()
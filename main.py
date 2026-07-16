import os
import re
import json
import requests

# The live URL to scrape
CLICFLYER_HOME_URL = "https://clicflyer.com/shoppers/en/saudi-arabia/jeddah/home"
OUTPUT_FILE = "results/supermarkets.json"

def fetch_live_html(url):
    """Attempts to fetch the HTML content directly from the internet."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1"
    }
    
    print(f"Attempting to fetch live HTML from {url}...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            print("Successfully fetched live HTML from the internet!")

            return response.text
        else:
            print(f"Warning: Live fetch failed with HTTP status code {response.status_code}.")
            if response.status_code == 503:
                print("Note: HTTP 503 is typical when running from datacenter IP addresses (like cloud agent environments) due to automated bot blocks.")
    except Exception as e:
        print(f"Warning: Failed to connect to the live website. Error: {e}")
        
    return None

from bs4 import BeautifulSoup

def extract_retailers_from_html_content(html):
    retailers = {}

    soup = BeautifulSoup(html, "html.parser")

    menu = soup.find("ul", id="menuRetHeader")
    if not menu:
        return retailers

    for a in menu.find_all("a", href=True):
        href = a["href"]

        span = a.find("span")
        if not span:
            continue

        name = span.get_text(strip=True)

        if href.startswith("/"):
            href = "https://www.clicflyer.com" + href

        if name not in retailers:
            retailers[name] = href

    return retailers


def categorize_retailer(name):
    """Categorizes a retailer based on name keywords."""
    name_lower = name.lower()
    
    supermarket_keywords = [
        "market", "hyper", "panda", "danube", "lulu", "nesto", "farm", 
        "cash and carry", "fresh", "wafa", "al jazera", "othaim", "tamimi", 
        "manuel", "wissam", "centro", "ramez", "city flower", "day n day", 
        "ala kaifak", "a market", "prime", "coop"
    ]    
    if any(kw in name_lower for kw in supermarket_keywords):
        return "Supermarkets"
    else:
        return "Others"

def create_output_directories():
    """Create output directories if they don't already exist."""
    os.makedirs("results", exist_ok=True)
    os.makedirs("htmls", exist_ok=True)

def main():
    # 1. Attempt to fetch HTML from the live internet
    create_output_directories()
    html_content = fetch_live_html(CLICFLYER_HOME_URL)
    
    # 2. Fall back to local reference HTML if live fetch failed
    if not html_content:
        print("Falling back to local reference HTML files to parse the elements...")
        
    if not html_content:
        print("Error: Could not retrieve HTML content from live internet or local fallback files.")
        return

    # 3. Extract retailers
    all_retailers = extract_retailers_from_html_content(html_content)
    print(f"\nTotal unique retailers extracted: {len(all_retailers)}")
    
    if not all_retailers:
        print("Warning: No retailers found. This could indicate a change in the page's HTML structure.")
        return

    # 4. Categorize and organize
    categorized = {
        "Supermarkets": {},
        "Others": {}
    }
    
    for name, href in all_retailers.items():
        category = categorize_retailer(name)
        categorized[category][name] = href

    # 5. Save output files
    supermarkets_data = categorized["Supermarkets"]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(supermarkets_data, f, indent=4, ensure_ascii=False)
    print("Saved supermarkets list to 'supermarkets.json'.")


    # 6. Print results summary
    print("\n==================================================")
    print("             SUPERMARKETS / HYPERMARKETS          ")
    print("==================================================")
    print(f"Found {len(supermarkets_data)} supermarkets:")
    print(f"| {'Supermarket Name':<45} | {'Hyperlink':<70} |")
    print(f"|{'-'*47}|{'-'*72}|")
    for name, href in sorted(supermarkets_data.items()):
        print(f"| {name:<45} | {href:<70} |")
    print("==================================================")

if __name__ == "__main__":
    main()

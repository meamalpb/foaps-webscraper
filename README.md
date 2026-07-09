# ClicFlyer Scraper

A Python scraper for extracting supermarket flyers and flyer page images from ClicFlyer.

The project consists of three stages:

1. Extract all supermarkets.
2. Extract all flyer links for a supermarket.
3. Extract all images for the first flyer.

The scraper uses **Playwright** to render JavaScript-powered pages and **BeautifulSoup** for HTML parsing.

---

## Requirements

- Python 3.14+
- uv

Install `uv` if it is not already installed:

```bash
pip install uv
```

---

## Installation

Clone the repository and run:

```bash
chmod +x setup.sh
./setup.sh
```

This will:

- Create a virtual environment (if one doesn't already exist)
- Install all Python dependencies
- Install the Chromium browser required by Playwright

---

## Running the Scraper

Run the complete scraping pipeline:

```bash
chmod +x run.sh
./run.sh
```

This executes:

1. `main.py`
2. `fetch_flyers.py`
3. `extract_images.py`

in sequence.

---

## Project Structure

```
.
├── htmls/
│   └── Debug HTML files
│
├── results/
│   ├── supermarkets.json
│   ├── flyers_first.json
│   └── flyer_images.json
│
├── main.py
├── fetch_flyers.py
├── extract_images.py
├── setup.sh
├── run.sh
├── pyproject.toml
└── README.md
```

---

## Output Files

| File | Description |
|------|-------------|
| `results/supermarkets.json` | Extracted supermarket names and URLs |
| `results/flyers_first.json` | Flyer URLs for the first supermarket |
| `results/flyer_images.json` | Image URLs for the first flyer |
| `htmls/` | Debug HTML snapshots used during scraping |

---

## Tech Stack

- Python
- Playwright
- BeautifulSoup
- uv

---

## Notes

If Playwright Chromium is missing, install it manually:

```bash
uv run playwright install chromium
```

The scraper creates the required `results/` and `htmls/` directories automatically during execution.
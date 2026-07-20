# ClicFlyer Scraper

A Python scraper for extracting supermarket flyers, flyer page images, and product
data (name, description, prices) from ClicFlyer, served through a small API.

The project consists of four stages:

1. Extract all supermarkets.
2. Extract all flyer links for a supermarket.
3. Extract all images for the first flyer.
4. Extract product details (name, description, original/discounted price) from each
   flyer image using the Gemini vision API.

The scraper uses **Playwright** to render JavaScript-powered pages and **BeautifulSoup**
for HTML parsing. Product data is served through a **FastAPI** endpoint.

---

## Requirements

- Python 3.14+
- uv
- A Gemini API key (for the product extraction step)

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

Then create a `.env` file in the project root with your Gemini API key:

```
GEMINI_API_KEY=...
```

Optionally set `GEMINI_MODEL` in `.env` to override the default vision model
(`gemini-3.5-flash`).

---

## Running the Scraper

Run the complete scraping pipeline:

```bash
chmod +x run.sh
./run.sh
```

This executes, in sequence:

1. `main.py` — extracts supermarkets
2. `fetch_flyers.py` — extracts flyer links for the first supermarket
3. `extract_images.py` — extracts flyer page images
4. `extract_products.py` — extracts product details from each flyer image via Gemini

Each stage reads the previous stage's output file, so you can comment out earlier
steps in `run.sh` to re-run just a later stage against existing `results/` files
(e.g. re-running `extract_products.py` alone after tweaking its prompt).

`extract_products.py` saves progress after every image and skips images it has
already processed, so a rerun resumes instead of starting over. On every run it
also prunes `results/products.json`: retailers or flyer pages that no longer
appear in `results/flyer_images.json` (e.g. an old flyer expired) are removed
from the output automatically, so stale product data doesn't linger.

---

## Serving Product Data

Once `results/products.json` exists, serve it through the API using uvicorn:

```bash
uv run uvicorn api:app --reload
```

By default this starts the server at `http://127.0.0.1:8000` and auto-reloads on
code changes (`--reload`, handy during development — drop it in production).

To bind a specific host/port (e.g. to make it reachable from other machines):

```bash
uv run uvicorn api:app --host 0.0.0.0 --port 8080
```

Once running:

- `GET /products` — flat JSON array of every product across all merchants, each
  shaped as `{name, description, original_price, discounted_price, merchant_name}`
- `GET /docs` — interactive Swagger UI
- `GET /openapi.json` — raw OpenAPI schema

Example:

```bash
curl http://127.0.0.1:8000/products
```

Stop the server with `Ctrl+C`.

---

## Project Structure

```
.
├── htmls/
│   └── Debug HTML files
│
├── results/
│   ├── supermarkets.json
│   ├── flyers.json
│   ├── flyer_images.json
│   └── products.json
│
├── main.py
├── fetch_flyers.py
├── extract_images.py
├── extract_products.py
├── api.py
├── setup.sh
├── run.sh
├── pyproject.toml
├── .env
└── README.md
```

---

## Output Files

| File | Description |
|------|-------------|
| `results/supermarkets.json` | Extracted supermarket names and URLs |
| `results/flyers.json` | Flyer URLs for the first supermarket |
| `results/flyer_images.json` | Image URLs for the first flyer |
| `results/products.json` | Product details (name, description, prices) extracted per flyer image |
| `htmls/` | Debug HTML snapshots used during scraping |

---

## Tech Stack

- Python
- Playwright
- BeautifulSoup
- Gemini API (vision, structured outputs)
- FastAPI / uvicorn
- uv

---

## Notes

If Playwright Chromium is missing, install it manually:

```bash
uv run playwright install chromium
```

The scraper creates the required `results/` and `htmls/` directories automatically
during execution.

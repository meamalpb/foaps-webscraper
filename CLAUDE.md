# Souq AI

Scraper + chatbot project. A 4-stage pipeline (`main.py` → `fetch_flyers.py` →
`extract_images.py` → `extract_products.py`) scrapes ClicFlyer for supermarket flyers and
extracts product deals (via Claude vision) into `results/products.json`. **Souq AI**
(`chat.py`) is a chatbot on top of that data, served alongside a `/products` API (`api.py`).

## Tech stack

- Python 3.14, managed with `uv`
- Playwright + BeautifulSoup — scraping
- Claude via AWS Bedrock (`anthropic[bedrock]`) — vision extraction and the chatbot's
  classification/reply calls
- FastAPI / uvicorn — HTTP layer
- SQLAlchemy — chat history (SQLite locally by default, Postgres via `DATABASE_URL`)
- rapidfuzz — fuzzy text matching (products, stores)
- Docker + Helm — deployment

## Key files

- `chat.py` — Souq AI: classifies each message into Onboarding/ProductSearch/PriceCompare/
  StoreInfo/Invalid, searches `results/products.json`, generates replies
- `categories.py` — fixed product category/subcategory taxonomy used by both the extraction
  pipeline and chat search
- `db.py` — chat message persistence
- `api.py` — FastAPI app; mounts `chat.py`'s router and serves `static/chat.html`
- `extract_products.py` — the pipeline stage that calls Claude to extract products/categories

## Commands

```bash
./setup.sh                          # venv, deps, Playwright, DB init
./run.sh                            # run the full scrape pipeline
uv run uvicorn api:app --reload     # serve API + chat UI at http://127.0.0.1:8000
```

No test suite exists in this repo currently — changes are verified manually (curl against
`/chat`, or the browser UI).

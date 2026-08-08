# Souq AI

A Python scraper that extracts supermarket flyers, flyer page images, product deals, and
store locations from ClicFlyer, plus **Souq AI** — a chatbot that answers questions about
those deals — served through a FastAPI app.

The project has two halves:

1. **The scraper pipeline** (4 stages) — collects supermarkets, flyer links, flyer page
   images, and finally extracts structured product data (name, description, prices,
   category/subcategory) plus store locations, using Claude's vision API.
2. **Souq AI** — a chat API + test UI built on top of that product data, with conversation
   memory, location-aware ("near me") search, and store-scoped browsing.

The scraper uses **Playwright** to render JavaScript-powered pages and **BeautifulSoup** for
HTML parsing. Product extraction and the chatbot both use **Claude** via **AWS Bedrock**.
Everything is served through **FastAPI**.

---

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- AWS Bedrock access (bearer token) for Claude
- A database — SQLite for local dev (default, no setup needed), PostgreSQL for production

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
- Create the database and tables used by Souq AI's chat history (auto-creates the
  Postgres role/database if `DATABASE_URL` points at Postgres and `psql` is available;
  otherwise defaults to a local SQLite file)

Then create a `.env` file in the project root (see `example.env` for the full list):

```
AWS_REGION=us-east-1
AWS_BEARER_TOKEN_BEDROCK=...
CLAUDE_MODEL=anthropic.claude-opus-4-8
DATABASE_URL=postgresql+psycopg://souq_ai:souq_ai@localhost:5432/souq_ai
```

`DATABASE_URL` is optional — omit it to use a local `souq_ai_chat.db` SQLite file instead.

---

## Running the Scraper

Run the complete scraping pipeline:

```bash
chmod +x run.sh
./run.sh
```

This executes, in sequence:

1. `main.py` — extracts the list of supermarkets
2. `fetch_flyers.py` — extracts flyer links and store locations for every supermarket
   (skips flyers already known from a previous run)
3. `extract_images.py` — extracts flyer page images
4. `extract_products.py` — extracts product details (name, description, prices,
   category/subcategory from a fixed taxonomy) from each flyer image via Claude, and
   carries each retailer's store locations through into the output

Each stage reads the previous stage's output file, so you can comment out earlier steps in
`run.sh` to re-run just a later stage against existing `results/` files.

`extract_products.py` saves progress after every image and skips images it has already
processed, so a rerun resumes instead of starting over. On every run it also prunes
`results/products.json`: retailers, flyers, or pages that no longer appear in
`results/flyer_images.json` (e.g. an expired flyer) are removed automatically, and store
locations are refreshed unconditionally.

---

## Running the API + Souq AI Chatbot

```bash
uv run uvicorn api:app --reload
```

By default this starts the server at `http://127.0.0.1:8000` and auto-reloads on code
changes (drop `--reload` in production).

Once running:

- `GET /` — Souq AI test chat UI (`static/chat.html`)
- `POST /chat` — send a message (`{message, phone_number, latitude?, longitude?}`) and get
  back `{category, reply, needs_location}`
- `GET /chat/history?phone_number=...` — recent conversation history for a phone number
- `GET /products` — flat JSON array of every product across all merchants, including
  category/subcategory and each merchant's store locations
- `GET /docs` — interactive Swagger UI
- `GET /openapi.json` — raw OpenAPI schema

Example:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "any deals on rice?", "phone_number": "+15551234567"}'
```

Stop the server with `Ctrl+C`.

### About Souq AI

Every message is classified into one of five categories (`chat.py`), each handled
independently and easy to extend with more:

- **Onboarding** — questions about Souq AI itself
- **ProductSearch** — looking for one or more products, a product category, or everything
  available at a specific store
- **PriceCompare** — comparing prices for one or more products across stores/cities
- **StoreInfo** — asking for a specific store's details (e.g. address), including follow-ups
  like "give me the address of that store"
- **Invalid** — anything off-topic

On top of classification, Souq AI supports:

- **Conversation memory** — recent messages per `phone_number` are persisted (`db.py`) and
  fed back in as context, so follow-ups like "what about a cheaper one?" work
- **Location-aware search** — pass `latitude`/`longitude` (or reply to a `needs_location`
  prompt) for "near me" queries; stores farther than 50 km are excluded
- **Result overflow handling** — when a search matches more than 10 products, Souq AI asks
  whether to show all of them or narrow down first, instead of dumping a long list

---

## Project Structure

```
.
├── htmls/                  # Debug HTML snapshots
│
├── results/
│   ├── supermarkets.json
│   ├── flyers.json         # includes store locations per retailer
│   ├── flyer_images.json
│   └── products.json       # includes category/subcategory + store locations
│
├── static/
│   └── chat.html            # Souq AI test chat UI
│
├── helm/souq-ai/            # Helm chart for k8s deployment
│
├── main.py                  # stage 1: supermarkets
├── fetch_flyers.py          # stage 2: flyer links + store locations
├── extract_images.py        # stage 3: flyer page images
├── extract_products.py      # stage 4: product + category extraction via Claude
├── categories.py            # product category/subcategory taxonomy
├── chat.py                  # Souq AI: classification, search, chat API
├── db.py                    # chat history persistence (SQLite dev / Postgres prod)
├── api.py                   # FastAPI app: /products, mounts chat.py + static UI
├── Dockerfile
├── setup.sh
├── run.sh
├── pyproject.toml
├── example.env
├── .env
└── README.md
```

---

## Output Files

| File | Description |
|------|-------------|
| `results/supermarkets.json` | Extracted supermarket names and URLs |
| `results/flyers.json` | Flyer URLs and store locations per retailer |
| `results/flyer_images.json` | Image URLs per flyer, with store locations carried through |
| `results/products.json` | Product details (name, description, prices, category/subcategory) and store locations per retailer |
| `htmls/` | Debug HTML snapshots used during scraping |

---

## Tech Stack

- Python
- Playwright, BeautifulSoup — scraping
- Claude via AWS Bedrock (`anthropic[bedrock]`) — product extraction (vision) and the Souq AI chatbot
- FastAPI / uvicorn — API + chat UI hosting
- SQLAlchemy — chat history persistence (SQLite dev, PostgreSQL prod)
- rapidfuzz — typo-tolerant product/store matching
- uv — dependency management
- Docker + Helm — containerized deployment

---

## Deployment

A multi-stage `Dockerfile` and a Helm chart (`helm/souq-ai/`) are included for running this
as a Kubernetes deployment, including a `PersistentVolumeClaim` for `results/` so scraped
data survives pod restarts.

---

## Notes

If Playwright Chromium is missing, install it manually:

```bash
uv run playwright install chromium
```

The scraper creates the required `results/` and `htmls/` directories automatically during
execution.

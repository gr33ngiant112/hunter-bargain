# hunter-bargain

Price tracker and discovery bot. Users add items they want tracked; the system scours the internet across multiple search engines to find the lowest price. Checks run daily (scheduled) or on-demand, with email notifications when target prices are met.

## Tech Stack

- **Language:** Python 3.12+
- **Framework:** FastAPI
- **Database:** SQLite (SQLAlchemy ORM)
- **Search:** SerpAPI (Google Shopping, Bing Shopping)
- **Scheduling:** APScheduler (in-process cron)
- **Email:** SMTP via smtplib
- **Infra:** Docker + docker-compose

## Architecture

```
src/hunter_bargain/
  main.py              # FastAPI app with lifespan (scheduler start/stop)
  config.py            # pydantic-settings from .env
  db.py                # SQLAlchemy engine/session
  models.py            # Item, PriceRecord ORM models
  schemas.py           # Pydantic request/response schemas
  api/
    items.py           # CRUD: POST/GET/PATCH/DELETE /api/v1/items
    prices.py          # POST /api/v1/prices/check/{id}, /check-all
  services/
    searcher.py        # Multi-engine orchestrator
    notifier.py        # SMTP email alerts
    scheduler.py       # APScheduler daily job
    engines/
      base.py          # SearchEngine ABC + SearchResult dataclass
      google.py        # Google Shopping via SerpAPI
      bing.py          # Bing Shopping via SerpAPI
```

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env with your SERPAPI_KEY, SMTP credentials, etc.
```

### 2. Run with Docker (recommended)

```bash
docker-compose up --build
```

The API will be available at `http://localhost:8000`.

### 3. Run locally (development)

```bash
pip install -e ".[dev]"
uvicorn hunter_bargain.main:app --reload
```

## CLI Usage

The `hb` command lets you manage tracked items from your terminal (requires the API server to be running).

```bash
# Add an item to track
hb add "iPhone 15 Pro" -e you@example.com -t 899.99 -k "256GB black titanium"

# List all tracked items
hb ls

# Update an item
hb update 1 -t 849.99

# Trigger a price check (single item or all)
hb check 1
hb check

# Remove an item
hb rm 1

# Point at a different server
hb --url http://myserver:9000 ls

# Or via environment variable
export HUNTER_BARGAIN_URL=http://myserver:9000
hb ls
```

## API Usage

### Add an item to track

```bash
curl -X POST http://localhost:8000/api/v1/items/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "iPhone 15 Pro",
    "keywords": "256GB black titanium",
    "target_price": 899.99,
    "notify_email": "you@example.com"
  }'
```

### List tracked items

```bash
curl http://localhost:8000/api/v1/items/
```

### Trigger an on-demand price check

```bash
curl -X POST http://localhost:8000/api/v1/prices/check/1
```

### Check all items at once

```bash
curl -X POST http://localhost:8000/api/v1/prices/check-all
```

### Remove an item

```bash
curl -X DELETE http://localhost:8000/api/v1/items/1
```

## Commands

| Command | Description |
|---------|-------------|
| `hb --help` | Show CLI usage |
| `pip install -e ".[dev]"` | Install with dev dependencies |
| `docker-compose up --build` | Run with Docker |
| `pytest` | Run test suite |
| `ruff check .` | Lint |
| `ruff format .` | Format |

## How It Works

1. **Add items** via the CLI (`hb add`) or REST API with a name, optional keywords, target price, and notification email.
2. **Daily scheduler** (APScheduler) runs a price check across all engines for every tracked item at the configured cron time (default: 9 AM UTC).
3. **On-demand checks** available via `POST /api/v1/prices/check/{id}`.
4. **Search engines** (Google Shopping, Bing Shopping via SerpAPI) return price results sorted by price.
5. **Price records** are persisted to SQLite for historical tracking.
6. **Email alerts** fire when a result meets or beats the target price.

## Rules

This project follows the engineering standards defined in [llmrules](https://github.com/gr33ngiant112/llmrules). See `AGENTS.md` for the OpenCode-translated version.

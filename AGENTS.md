# hunter-bargain

FastAPI + SQLite price tracker: SerpAPI (Google and Bing Shopping) searches, a daily APScheduler job, SMTP alerts, and the `hb` Click CLI.

## Commands

- Setup, inside a venv: `pip install -e ".[dev]"`
- Verify before saying done: `ruff check . && ruff format --check . && pytest -q`
- Run: `uvicorn hunter_bargain.main:app --reload`, or `docker compose up --build`

## Do not

- Do not read, print or edit `.env`.
- Do not call SerpAPI or send real email to check a change. Use tests with fakes and recorded, key-scrubbed fixtures; these are not "dummy data".
- Do not add a dependency by guessing its name. Once `uv.lock` exists, use `uv add`.
- Do not refactor code unrelated to the task.

## Gotchas

- Routers are mounted under `/api/v1` (`main.py`). The CLI and any client must use the full path.
- `config.settings` reads `.env` when the module is imported, so importing the package, including under pytest, loads whatever `.env` is in the working directory.
- A SerpAPI request URL carries the API key, and so can exception text that quotes it. Log the error type, never the URL or the exception text (see `fetch_serpapi` in `services/engines/base.py`).
- The daily job and the run lock for checks of all items live in the app process: run one uvicorn process, not `--workers`.
- The app migrates its database at startup (`db.init_db`), so a model change needs an Alembic migration; see "Schema Changes" in CONTRIBUTING.md.
- The API has no authentication. Docker Compose publishes it on 127.0.0.1 unless `HB_BIND_ADDR` says otherwise.

Workflow, branching, commits and style for everyone: CONTRIBUTING.md.

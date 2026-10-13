# hunter-bargain

FastAPI + SQLite price tracker: SerpAPI (Google and Bing Shopping) searches, a daily APScheduler job, SMTP alerts, and the `hb` Click CLI.

## Commands

- Setup: `uv sync --locked --extra dev`, then `source .venv/bin/activate`
- Verify before saying done: `ruff check . && ruff format --check . && pytest -q`
- Run: `uvicorn hunter_bargain.main:app --reload`, or `docker compose up --build`
- See alerts without real email: `cp .env.example .env`, then `docker compose --profile dev up --build`; Mailpit shows them at http://127.0.0.1:8025

## Do not

- Do not read, print or edit `.env` or the real settings file outside the checkout (`~/.config/hunter-bargain/`). `.claude/settings.json` denies both to Claude Code.
- Do not run `docker compose`, `uvicorn`, `hb check` or `curl` without asking first: they can start or call the app with real settings, and a start can run a missed daily check of all items a minute later. `.claude/settings.json` makes Claude Code ask.
- Do not call SerpAPI or send real email to check a change. Use tests with fakes and recorded, key-scrubbed fixtures; these are not "dummy data".
- Do not add a dependency by guessing its name. Use `uv add`, and commit `uv.lock` with `pyproject.toml`.
- Do not refactor code unrelated to the task.
- Do not drop the assistant trailer from a commit: keep the tool's own (`Co-Authored-By:` in Claude Code). A tool that adds none, such as OpenCode, ends the message with `Assisted-by: <tool and model>`.

## Gotchas

- Routers are mounted under `/api/v1` (`main.py`). The CLI and any client must use the full path.
- `config.settings` reads `.env` and the environment on first use, which for the app is when it starts: an invalid setting stops it there. Importing the package reads neither, and the tests never read `.env` (`conftest.py` gives each test the defaults). Starting the app in the checkout (uvicorn, `docker compose`) still loads its `.env`.
- A SerpAPI request URL carries the API key, and so can exception text that quotes it. Log the error type, never the URL or the exception text (see `fetch_serpapi` in `services/engines/base.py`).
- The daily job and the run lock for checks of all items live in the app process: run one uvicorn process, not `--workers`.
- The app migrates its database at startup (`db.init_db`), so a model change needs an Alembic migration; see "Schema Changes" in CONTRIBUTING.md.
- The API has no authentication. Docker Compose publishes it on 127.0.0.1 unless `HB_BIND_ADDR` says otherwise.

Workflow, branching, commits and style for everyone: CONTRIBUTING.md.

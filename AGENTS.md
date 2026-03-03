# Hunter-Bargain — OpenCode Agent Rules

> Synthesized from [gr33ngiant112/llmrules](https://github.com/gr33ngiant112/llmrules). All agents working on this project MUST follow these rules.

## Overview

**hunter-bargain** is a price tracker and discovery bot. Users add items they want tracked; the system scours the internet across multiple search engines to find the lowest price. Checks run daily (scheduled) or on-demand, with email notifications.

## Tech Stack

- **Language:** Python 3.12+
- **Framework:** FastAPI (REST API)
- **Database:** SQLite (via SQLAlchemy)
- **Scheduling:** APScheduler
- **Scraping:** httpx + BeautifulSoup4, SerpAPI (optional)
- **Email:** smtplib (SMTP)
- **Infra:** Docker + docker-compose
- **Testing:** pytest

## Commands

- **Install:** `pip install -e ".[dev]"`
- **Run:** `docker-compose up`
- **Test:** `pytest`
- **Lint:** `ruff check .`
- **Format:** `ruff format .`

---

## 1. Core Principles

- Operate as a senior/staff software engineer. Prioritize maintainability, readability, and architectural integrity.
- Work autonomously. Do not stop for permission on non-trivial tasks once the plan is approved. If a bug is found or a test fails, fix it immediately.
- Make every change as simple as possible. Impact minimal code. Demand elegance — if a fix feels hacky, re-evaluate.
- Find root causes. No temporary band-aid fixes. No laziness.
- **Zero Dummy Data:** NEVER generate, seed, or hardcode dummy data, placeholder text, or fake API responses to bypass a problem. If blocked (missing API key, etc.), document the blocker in `BLOCKED_FEATURES.md` and move on.

## 2. Workflow & Task Management

- **Plan First:** Enter Plan Mode for any non-trivial task (3+ steps or architectural decisions). Write a detailed plan to `tasks/todo.md` with checkable items.
- **Track Progress:** Mark items complete in `tasks/todo.md` as you go. Document results and add a review section.
- **Verification Before Done:** Never mark a task complete without proving it works. Run tests, check logs, demonstrate correctness empirically. Ask: "Would a staff engineer approve this?"
- **Execution Cycle:** Research → Strategy → Execute (Plan → Act → Validate per sub-task).
- Use subagents liberally to keep main context clean. One task per subagent for focused execution.

## 3. Git Flow & GitHub Standards

- **Strict GitFlow:**
  - `main` — Production-ready code only.
  - `develop` — Main integration branch.
  - `feature/<name>` — Cut from `develop`.
  - `release/<version>` — For stabilization.
  - `hotfix/<name>` — Urgent production fixes.
- Use `gh` CLI for all pull requests and GitHub interactions.
- Descriptive commit messages. Link PRs to relevant issues.
- **Semantic Versioning (SemVer).** Maintain `CHANGELOG.md` at root.

## 4. Development Standards

- Use modern Python features (3.10+ match statements, type hints, dataclasses).
- Follow RESTful API design principles.
- Implement robust input validation and custom error handling.
- Use environment variables (`.env`) for ALL configuration. Never commit secrets.
- **Containerize all services.** Use `docker-compose` for local orchestration. Keep Dockerfiles adjacent to the code they support.
- Code comments generously on complex logic. Keep knowledge close to the code.

## 5. Testing & Validation

- All code must pass the test suite (pytest) before merge.
- Add automated tests for every new feature or bug fix.
- Use mock fixtures to avoid network calls in unit tests.
- Run existing test suites to ensure no regressions.
- "Trust but verify" — execute tests and build commands to prove correctness.

## 6. Self-Improvement

- After ANY correction: update `tasks/lessons.md` with the pattern (mistake, root cause, prevention rule).
- Review `tasks/lessons.md` at the start of every session.
- Iterate on project rules until mistake rate drops.

## 7. Documentation

- Maintain `README.md` at root: Overview, Tech Stack, Architecture, Installation, Usage, Commands.
- Use `tasks/todo.md` for active work tracking.
- Use `tasks/lessons.md` for capturing learning.
- Use `BLOCKED_FEATURES.md` for external dependencies or blockers.
- Prioritize code clarity and comments over external docs.

---

## Architecture

```
hunter-bargain/
  src/hunter_bargain/
    __init__.py
    main.py          # FastAPI app entry point
    config.py         # Environment-based configuration
    models.py         # SQLAlchemy models (Item, PriceRecord)
    schemas.py        # Pydantic request/response schemas
    api/
      __init__.py
      items.py        # CRUD endpoints for tracked items
      prices.py       # Price check endpoints (on-demand)
    services/
      __init__.py
      searcher.py     # Multi-engine price search orchestrator
      engines/
        __init__.py
        base.py       # Abstract search engine interface
        google.py     # Google Shopping search
        bing.py       # Bing Shopping search
        amazon.py     # Amazon search
      notifier.py     # Email notification service
      scheduler.py    # APScheduler daily check service
    db.py             # Database session and engine setup
  tests/
  tasks/
    todo.md
    lessons.md
  Dockerfile
  docker-compose.yml
  pyproject.toml
  .env.example
  CHANGELOG.md
  README.md
  AGENTS.md
```

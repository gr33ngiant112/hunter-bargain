# Contributing to hunter-bargain

First off, thanks for wanting to contribute! Every pull request, bug report, and feature suggestion helps make this project better.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Style Guide](#style-guide)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Features](#suggesting-features)

## Code of Conduct

Be respectful, constructive, and kind. That's it. No 47-page document needed.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/<your-username>/hunter-bargain.git
   cd hunter-bargain
   ```
3. Set up your development environment (see below)

## Development Setup

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for containerized runs)
- No API key for development: the tests use fakes and recorded SerpAPI responses. A
  [SerpAPI](https://serpapi.com/) key is needed only to run real searches.

### Install

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

### Configure

The tests need no `.env`. To run the app, copy the example. Its values are for development: no
credentials, and `docker compose --profile dev up` sends alerts to Mailpit (README "Local
Development"):

```bash
cp .env.example .env
```

Keep real API keys and SMTP credentials out of the checkout, in the file `HB_ENV_FILE` names
(README "Deployment"): anything run in the checkout, tests and coding agents included, loads its
`.env`.

### Run Tests

```bash
pytest -v
```

### Lint & Format

```bash
ruff check .
ruff format .
```

### Pre-commit Hooks

Ruff, mypy (on `src`), a YAML check and a private-key check run before every commit, whichever
editor or agent makes it. Install the hooks once per clone, inside the venv:

```bash
pre-commit install
pre-commit run --all-files   # the whole tree, e.g. after changing the hooks
```

### Run Locally

```bash
uvicorn hunter_bargain.main:app --reload
```

### Run with Docker

```bash
docker compose up --build
```

## Making Changes

### Branch Strategy (GitFlow)

We follow strict GitFlow:

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready releases only |
| `develop` | Integration branch for features |
| `feature/<name>` | New features (branch from `develop`) |
| `fix/<name>`, `chore/<name>`, `ci/<name>` | Bug fixes, maintenance and CI changes (branch from `develop`) |
| `release/<version>` | Release stabilization |
| `hotfix/<name>` | Urgent production fixes |

**Always branch from `develop`:**

```bash
git checkout develop
git pull origin develop
git checkout -b feature/my-awesome-feature
```

A release fast-forwards `main` to `develop`, tags it `vX.Y.Z` and publishes a GitHub release
from that version's `CHANGELOG.md` section.

### Schema Changes

The app migrates its database at startup with Alembic. After changing a model, generate a
migration against a throwaway database, then review the new file in
`src/hunter_bargain/migrations/versions/` (SQLite alters tables in batch mode):

```bash
export DATABASE_URL=sqlite:///scratch.db
alembic upgrade head
alembic revision --autogenerate -m "describe the change"
```

### Commit Messages

Write descriptive commit messages that explain *why*, not just *what*:

```
fix: give each item its own session in the daily job

One shared session meant that an item deleted during the run, or one
database error, ended the run for every item after it.
```

Prefixes: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `ci:`

## Pull Request Process

1. Ensure all tests pass: `pytest -v`
2. Ensure lint is clean: `ruff check . && ruff format --check .`
3. Update `CHANGELOG.md` with your changes under `[Unreleased]`
4. Create a PR targeting `develop` (never `main` directly). CI skips draft PRs, so run steps 1
   and 2 locally before you mark a draft ready for review.
5. Describe what changed and why, link the issue (`Fixes #N`), and list the commands you ran with
   their results
6. Request review from a maintainer

### PR Requirements

- [ ] Tests pass
- [ ] Lint passes
- [ ] New features have tests
- [ ] Bug fixes include regression tests
- [ ] No dummy data, placeholders, or hardcoded secrets
- [ ] CHANGELOG updated

## Style Guide

### Python

- Python 3.12+ features encouraged (type hints, match statements, etc.)
- Line length: 100 characters
- Ruff for linting and formatting (`ruff check .` / `ruff format .`)
- Lint rules: `E, F, I, N, W, UP, B, SIM`

### Code Principles

- **Minimal changes**: Impact as little code as possible
- **Root causes**: Fix the actual problem, not symptoms
- **No type suppression**: Never use `# type: ignore`
- **No empty catch blocks**: Always handle or log errors
- **No fake data shown as real**: No placeholder values or invented responses in the app. Fakes and
  recorded, key-scrubbed SerpAPI responses in tests are not dummy data.

### Testing

- Use `pytest` with fixtures from `conftest.py`
- Use fakes or recorded, key-scrubbed responses for external calls (SerpAPI, SMTP); never make
  real network calls in tests
- Test both happy paths and error cases

## Reporting Bugs

Open an issue with:

1. **What happened** (actual behavior)
2. **What you expected** (expected behavior)
3. **Steps to reproduce**
4. **Environment** (OS, Python version, Docker version)
5. **Logs** (if applicable), with API keys, passwords and other secrets removed: issues are public

## Suggesting Features

Open an issue with:

1. **Problem**: What pain point does this solve?
2. **Proposed solution**: How would you approach it?
3. **Alternatives considered**: What else did you think about?
4. **Scope**: Is this a small tweak or a large feature?

---

Thank you for contributing!

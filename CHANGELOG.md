# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-10-13

Upgrading from 0.5.0: the first start of this version adds the merchant, the rating and the review
count to `price_records`. Records saved before then have none, so with `MERCHANT_ALLOWLIST` set they
are left out of the 90-day and all-time lows, like any listing that names no merchant.

### Added

- `HEARTBEAT_URL`: a push monitor's URL (healthchecks.io, Uptime Kuma) that gets one GET, with a
  10-second timeout, after each price check of all items that succeeds, so the monitor raises the
  alarm when they stop. Empty, the default, is off. The URL is never logged (#24).
- A warning when SerpAPI searches run low. After each check of all items, the app reads the
  account's searches left from SerpAPI's Account API, which costs no search. With fewer left than
  twice what the check made, it logs a warning and emails it to `ALERT_RECIPIENTS` when SMTP is
  set up. The API key and the account's email address in that answer are never logged, stored
  or returned (#24).
- `MERCHANT_DENYLIST` and `MERCHANT_ALLOWLIST`: comma-separated merchant names, compared ignoring
  ASCII case and extra spaces. A denylisted merchant's price never alerts; with an allowlist,
  only its merchants' prices do. Listings from other merchants are still saved, and
  `merchant_excluded` in a price check's result counts them (#29).
- A price more than `VERIFY_MERCHANT_PCT` (40) percent below the median of the same check's
  prices, with at least 3 of them, is flagged `verify_merchant`: the alert email warns to check
  the seller, its button says "Verify merchant" instead of "Buy Now", and `hb check` prints a
  "verify merchant" line (#29).
- The alert email and `hb check` show the listing's rating, its review count and "one of several
  sellers" when SerpAPI gives them. Price records keep each listing's merchant, rating and review
  count, and `GET /api/v1/items/{id}/prices` and a price check's result include them (#29).
- `NOTIFIER=apprise` with `APPRISE_URL`: price alerts and notices go to a push service through
  Apprise (ntfy, Gotify, Discord, Telegram and more) instead of email. `APPRISE_URL` can hold the
  service's token, so it is never logged, and Apprise's own log is off. `NOTIFIER=apprise`
  without `APPRISE_URL` stops the app at startup (#28).

### Changed

- A check of all items that checked nothing is recorded as failed: one whose every search failed
  (a revoked key, no searches left, SerpAPI down), or one with items in which every item check
  raised an error. `/health`, the heartbeat and the catch-up at the next start treat it as such;
  an item deleted during the check is not a failure (#24).
- `/health`'s limit follows `PRICE_CHECK_CRON`: the longest gap between its next 10 run times,
  plus 2 hours (26 hours for the default daily schedule, 74 for weekdays only) (#24).
- A price check searches Google Shopping and Bing Shopping at the same time, so it takes about as
  long as the slower search; the results, engine errors and lowest price are as before (#22).
- One SerpAPI engine on httpx serves both engines. Searches no longer send Google Shopping's
  `num` or Bing Shopping's `count`, which SerpAPI ignores, and an engine error for a failed
  request names httpx's error type, e.g. `request failed (ConnectError)` (#22).
- The 90-day and all-time lows (in alerts, `hb check` and `/stats`) leave out merchants that
  `MERCHANT_DENYLIST` and `MERCHANT_ALLOWLIST` exclude (#29).
- Without SMTP settings, a price alert is written to the log (item id, price, target and engine,
  without the address) instead of being skipped, and again at the next check until SMTP is set
  up (#28).
- New dependency: apprise, which brings requests, urllib3, charset-normalizer, requests-oauthlib,
  oauthlib and markdown into the lock (#28).
- Importing the app no longer reads `.env` or the environment: the settings are read, and the
  database engine made, on first use. The app reads them as it starts, so an invalid setting
  stops it at startup with the same validation error, and the tests never read the checkout's
  `.env` (#33).
- `run_price_check` takes optional `engines` and `notify` parameters, which default to the
  configured engines and the email alert (#33).

### Removed

- The `google-search-results` dependency (#22).

## [0.5.0] - 2026-10-12

Upgrading from 0.4.0: read these notes first.

- Scripts that read the list `POST /prices/check-all` returned, or the prices `hb check` printed
  for all items, must change: check-all now answers `{"status": "started"}`. Use
  `POST /prices/check/{id}` or `hb check ITEM_ID` for one item's prices.
- Back up the database first: stop the app and copy its data directory (in Docker, the
  `app-data` volume). The first start of this version marks an existing database as the schema
  it already has, then migrates it; its rows are kept. There is nothing to run by hand.
- Run one app process, as the image does: migrations run as the app starts, and two processes
  starting together could both migrate the same database.
- The database now uses SQLite's WAL mode, so recent writes can sit in `hunter_bargain.db-wal`
  beside the database file. Back up with the app stopped and copy any `-wal` and `-shm` files
  too, or use `sqlite3 data/hunter_bargain.db ".backup backup.db"`.
- Move the real settings out of the checkout. From the checkout, with the app's settings in
  `.env`: `mkdir -p ~/.config/hunter-bargain && install -m 600 .env ~/.config/hunter-bargain/hb.env`,
  then `cp .env.example .env` and `echo "HB_ENV_FILE=$HOME/.config/hunter-bargain/hb.env" >> .env`.
  Copy any `HB_BIND_ADDR` or `APP_PORT` you had set into the new `.env` as well: Compose reads
  those from the checkout's `.env` or the shell only. `docker compose up -d` then recreates the
  app with the same settings.
- Keywords are now required words in a listing's title. An item whose keywords were search hints
  that titles leave out or spell another way ("noise cancelling" against "Noise Canceling") can
  stop matching: change them with `hb update ITEM_ID --keywords "..."` or
  `PATCH /api/v1/items/{id}`.
- The first start of this version adds the `check_runs` table; existing rows are not changed. A
  missed daily check is caught up only once a check of all items has succeeded on this version:
  the first start after the upgrade has no run to compare with, so it starts no extra check.
- The first start of this version adds each item's alert state (`last_alert_price`,
  `last_alerted_at`) to the `items` table. Existing items start without one, so an item whose
  price is already on target is emailed once more at its next check.
- The app now runs as uid 10001. `docker compose up --build -d` first runs the one-off
  `data-permissions` service, which gives the existing `app-data` volume to that user, so there
  is nothing to do by hand. The container is now named by Compose (e.g. `hunter-bargain-app-1`,
  not `hunter-bargain-app`); `docker compose exec app ...` and `docker compose logs app` work as
  before.

### Added

- Price history (#31): `GET /api/v1/items/{id}/prices` lists an item's recorded prices, newest
  first, and `GET /api/v1/items/{id}/stats` gives its low and median price per day and its
  lowest prices. `hb history ID` shows the lowest price of each day as a sparkline. Alert emails
  and `hb check` also show the item's lowest price in the last 90 days and its all-time low.
- Database migrations (Alembic, in `src/hunter_bargain/migrations`): the app migrates its database
  as it starts, so a release can change the schema of an existing database. `alembic.ini` serves
  the `alembic` command in development, and CI fails when the models need a migration that does
  not exist yet.
- Pre-commit hooks (`.pre-commit-config.yaml`): Ruff lint and format, mypy on `src`, a YAML check
  and a private-key check before each commit; `pre-commit install` once per clone
- `hb --timeout SECONDS`: how long the CLI waits for the server (default 60; it was a fixed 30,
  less than a slow check of both engines can take)
- `HB_ENV_FILE`: Docker Compose gives the app the settings in that file (default `.env`), so the
  real credentials can live outside the checkout, e.g. in `~/.config/hunter-bargain/hb.env`
- `SMTP_SECURITY`: `tls` (the default, as before) or `none`, which sends without TLS and without
  a login, for a local test server only
- A Mailpit service under the `dev` Compose profile: `docker compose --profile dev up` keeps alerts
  in a local inbox at http://127.0.0.1:8025 instead of sending them
- `.claude/settings.json`: Claude Code may not read or edit `.env` or `~/.config/hunter-bargain/`,
  and asks before running `docker compose`, `uvicorn`, `hb check` or `curl`
- A `check_runs` table: each check of all items, by the daily job or
  `POST /api/v1/prices/check-all`, records when it started and finished, whether it succeeded,
  and when it ends, the items it checked, the searches it used, its engine errors (searches that
  failed, such as on a SerpAPI error response) and the alerts it sent
- `GET /api/v1/runs`: the latest checks of all items, newest first (`?limit=`, default 10), and
  `hb status`, which shows the last one
- `ALERT_MIN_DROP_PCT` (default 0) and `ALERT_COOLDOWN_DAYS` (default 7): how far below an item's
  last alert a new low must be, and how many days pass before a price still on target is emailed
  again
- `alert_sent` in the result of `POST /api/v1/prices/check/{item_id}`: whether the check emailed
  an alert
- `searches_used` in the same result: how many of the engines' searches worked
- `SECURITY.md`: report a vulnerability privately, through GitHub's private vulnerability
  reporting, not in a public issue
- `.github/dependabot.yml`: weekly Dependabot pull requests for `uv.lock` (the floors in
  `pyproject.toml` stay as set), the GitHub Actions, the Docker base image (without Python
  version changes) and the Mailpit image
- A commit-message check: a commit-msg hook and a CI job reject a first line that does not start
  with a type from CONTRIBUTING.md (`feat:`, `fix(cli):` and so on). In a clone set up before
  this, run `pre-commit install` again to get the hook

### Changed

- `GET /health` answers 503, with the reason, when the scheduler is not running or no check of
  all items has succeeded in 26 hours (counted from the app's start until one has); it answered
  `{"status": "ok"}` whatever happened. The healthy answer is unchanged.
- `POST /api/v1/prices/check-all` answers 202 at once and checks every item in the background,
  as the daily job does: results go to the price history, the log and alert emails. It answers
  409 while a check of all items, its own or the daily job's, is running.
- `hb check` without an item ID starts that background check and prints no prices;
  `hb check ITEM_ID` still prints the item's prices
- Keywords now filter results as well as refine the search: every word of each comma-separated
  keyword term must appear in a listing's title (`256GB` also matches "256 GB"). Second-hand
  listings are skipped: Google Shopping rows with a `second_hand_condition`, and titles saying
  renewed, refurbished, restored, used, pre-owned, open box or for parts.
- `AGENTS.md` is a short, hand-written guide for coding agents: commands, what not to do, and
  facts the code does not show. Workflow and style rules are in `CONTRIBUTING.md` only.
- CI skips draft pull requests and runs when a pull request is marked ready for review
- `.env.example` holds development values: alerts go to Mailpit, and the SerpAPI key and SMTP
  login are empty
- Dependencies are locked in `uv.lock`. CI installs from it with `uv sync --locked` and fails
  when it is out of date with `pyproject.toml`; a development setup is now
  `uv sync --locked --extra dev`.
- Higher minimum versions: FastAPI 0.133 (it checks the Content-Type of JSON requests and, unlike
  0.132, allows Starlette 1.x), Pydantic 2.7 and APScheduler 3.11 (3.10 fails to import with
  current setuptools). A CI job runs the tests on the lowest versions `pyproject.toml` allows.
- The dependency scan (osv-scanner) reads `uv.lock` and fails CI on a known vulnerability; it
  was report-only
- The tests build Starlette's TestClient on httpx2, a new dev dependency, so they run without a
  deprecation warning
- The tests no longer depend on the developer's setup: each test starts from the default
  settings, whatever `.env` or the environment holds, and a test that connects to another machine
  fails
- A warning fails the test that raised it. CI also checks types with mypy, fails when the tests'
  branch coverage is under 85%, scans the code with Semgrep's Python and FastAPI rules, and lints
  the workflows with zizmor and actionlint. Higher minimum versions: SQLAlchemy 2.0.16 (older
  releases cause a deprecation warning on Python 3.12) and, for development, pytest-cov 7.0 (with
  older releases, `pytest --cov` fails to combine the coverage of the tests' subprocesses).
- The Docker image installs the app and the versions in `uv.lock`, in a multi-stage build with no
  compiler, uv or dev tools in the final image; it starts as before. CI builds it with
  `docker compose build`, runs lint, mypy, the tests and the migration check inside Docker images
  (the tests also on Python 3.13 and 3.14), checks that the image answers `/health`, and fails on
  a fixable HIGH or CRITICAL vulnerability that trivy finds in it.
- The Docker image runs the app as uid 10001 instead of root and has a `HEALTHCHECK`; its base
  images are pinned by digest, which Dependabot updates. Docker Compose runs the app with a
  read-only root filesystem, no Linux capabilities, `no-new-privileges`, CPU, memory and process
  limits and log rotation, and no longer fixes the container's name (#11, #7).

### Removed

- The first build session's notes: `tasks/` (now ignored by git) and `BLOCKED_FEATURES.md`. The
  purchase-links spec moved from `FEATURES.md` to `docs/specs/2026-03-03-purchase-links.md`, and
  the open backlog item is issue #54.
- Unused dependencies: beautifulsoup4, lxml and python-dotenv (pydantic-settings still installs
  python-dotenv), and pytest-asyncio, respx and a second httpx entry in the dev extra

### Fixed

- SQLite enforces foreign keys: deleting an item deletes its price records in the database too
  (ON DELETE CASCADE), and a price record for an item that does not exist is refused. Price
  records left behind by items deleted earlier are kept.
- The daily check is skipped, with a warning in the log, while a check of all items started
  through the API runs, so no item is checked and alerted on twice
- The CLI prints a clear error and exits 1 when the server does not answer in time or the
  request fails, instead of a traceback
- Relevance filter: previous-generation, look-alike and refurbished listings no longer alert (an
  iPhone 13 Pro for an iPhone 15 Pro, a WH-1000XM4 for a WH-1000XM5, a PlayStation 4 for a
  PlayStation 5), and routers, camera kits, vacuums, tablets, laptops and monitors are no longer
  rejected for words such as "Band", "Lens", "Filter", "Screen", "Keyboard" or "Stand" in their
  titles. An item name with no word to match, such as "The", no longer accepts every listing.
- Relevance filter: a product generation counts as its number, so an item named "AirPods Pro 2"
  matches titles that say "(2nd Generation)", "2nd Gen" or "Gen 2", and other generations are
  still rejected
- A daily check missed while the app was down, or one that failed, runs a minute after the app
  next starts, with a warning in the log naming the missed time. A daily run that starts late
  because the host was suspended or busy still runs within 3 hours; it was skipped once it was
  more than 1 second late.
- A price that stays at or below an item's target is emailed once, not at every check: the next
  alert needs a lower price, or 7 days (`ALERT_COOLDOWN_DAYS`). A failed send is tried again at
  the next check, and a new `target_price` starts the item's alerts over.
- A search result without SerpAPI's `extracted_price` is skipped unless its price is a plain
  dollar amount such as "$1,299.00". A comma decimal was misread: "$1.299,00" as 1.299 dollars,
  "$12,99" as 1,299 dollars and "$0,299" as 299 dollars.

## [0.4.0] - 2026-10-05

No earlier version was tagged, and the package reported 0.1.0 until this release. The version
now has one source, `src/hunter_bargain/__init__.py`, which the package metadata and the API
docs read.

### Added

- Industry-standard open source documentation (README, CONTRIBUTING, LICENSE)
- Project banner image (`assets/banner.svg`)
- MIT License
- CI on pull requests and on pushes to `develop`: Ruff lint and format checks, tests, a package
  build, a gitleaks scan of the full history and a report-only dependency scan
- `ALERT_RECIPIENTS`: the addresses that may receive alerts. Item create and update return 422
  for any other `notify_email`, and alerts for older items with other addresses are skipped.
  Unset allows no address.
- `HB_BIND_ADDR`: the address Docker Compose publishes the API on (127.0.0.1 by default)
- `SMTP_TIMEOUT` (seconds, default 30), and implicit TLS when `SMTP_PORT` is 465
- `PRICE_CHECK_TZ` (default `UTC`): the time zone of `PRICE_CHECK_CRON`
- The seller's name in alert emails and `hb check` output, and `lowest_merchant` in price check
  results
- `engine_errors` in price check results, naming each engine that could not search

### Changed

- Docker Compose publishes the API on 127.0.0.1 unless `HB_BIND_ADDR` sets another address
- A missing `SERPAPI_KEY`, a rejected key, used-up searches and SerpAPI server errors are
  reported as engine errors instead of as empty results
- SerpAPI requests time out after 20 seconds
- Repository-wide Ruff lint compliance applied

### Removed

- The `APP_HOST` setting, which the app never read. `APP_PORT` remains the host port Docker
  Compose publishes.

### Fixed

- Google Shopping results link to the product page (`product_link`), and Bing results to the
  seller's page (`external_link`)
- Monthly-installment prices and prices in other currencies are skipped instead of being read as
  US dollars
- One malformed price row no longer drops every result from its engine
- Item validation: an explicit `null` for `name` or `notify_email`, a blank name, and a target
  price that is not a finite number above 0 and at most 1,000,000 return 422 (NaN and Infinity
  no longer cause a 500)
- A fresh clone starts: the SQLite database directory is created, and a `.env` copied from
  `.env.example` is accepted
- The daily check runs in `PRICE_CHECK_TZ` instead of the host's local time zone
- The daily check goes on when an item is deleted during the run, or when one item's check or
  database access fails

### Upgrading

- Rebuild the image on current base-image and dependency fixes:
  `docker compose build --pull --no-cache && docker compose up -d`
- Set `ALERT_RECIPIENTS` in the env file before you upgrade. Unset, no address may receive
  alerts: item create and update return 422, and alerts for existing items are skipped.
- Docker Compose publishes the API on 127.0.0.1 by default. On the host, run the CLI in the container
  (`docker compose exec app hb ls`). To reach the API from other machines, set `HB_BIND_ADDR`;
  the API has no authentication, so use a trusted network or a reverse proxy with authentication.
- SMTP verifies the server's certificate and host name, so a server with a self-signed or
  mismatched certificate is refused. Port 465 uses implicit TLS, any other port STARTTLS.
- Outside Docker, the daily check runs in `PRICE_CHECK_TZ` (default `UTC`) instead of the host's
  local time zone. The Docker image already ran in UTC.
- `APP_HOST` is no longer read; an env file that still sets it keeps working.

### Security

- SMTP verifies the server's certificate and host name, with STARTTLS or implicit TLS, and has a
  connect timeout
- The SerpAPI key is kept out of the logs: request errors are logged without the request URL,
  urllib3's per-request debug line is off, and any `api_key=` value in a log record is redacted
- Alert emails escape item names and listing text and link only http and https URLs; the CLI
  strips control characters, and names or keywords with control characters return 422

## [0.3.0] - 2026-03-03

### Added

- Purchase links in email alerts with styled "Buy Now" CTA button
- Purchase links displayed in `hb check` CLI output
- `FEATURES.md` for tracking feature plans

## [0.2.0] - 2026-03-03

### Added

- Standalone CLI tool (`hb`) with `add`, `rm`, `ls`, `update`, and `check` commands
- Multi-layer relevance filtering for search results:
  - Price floor filter (rejects results below 10% of target price)
  - Extensions metadata analysis (Google Shopping accessory detection)
  - Title word-overlap scoring (≥50% threshold)
  - Accessory term blocklist (50+ terms)

### Fixed

- Query building splits comma-separated keywords into separate search terms
- CLI routes corrected to match API mount point (`/api/v1/`)
- Dockerfile copies `README.md` for hatchling build metadata

### Changed

- Test suite expanded from 24 to 59 tests

## [0.1.0] - 2026-03-03

### Added

- Initial project scaffold
- FastAPI REST API for item management (CRUD)
- Multi-engine price search (Google Shopping and Bing Shopping via SerpAPI)
- Daily scheduled price checks via APScheduler
- On-demand price check endpoint
- Email notifications via SMTP
- SQLite database with SQLAlchemy ORM
- Docker and docker-compose setup
- Comprehensive test suite (24 tests)
- Environment-based configuration via pydantic-settings

[Unreleased]: https://github.com/gr33ngiant112/hunter-bargain/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/gr33ngiant112/hunter-bargain/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/gr33ngiant112/hunter-bargain/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/gr33ngiant112/hunter-bargain/releases/tag/v0.4.0

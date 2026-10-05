# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

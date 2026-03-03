# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-03-03

### Added

- Industry-standard open source documentation (README, CONTRIBUTING, LICENSE)
- Project banner image (`assets/banner.svg`)
- MIT License

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
- Bing Shopping search engine via SerpAPI

### Fixed

- Query building now splits comma-separated item names correctly
- CLI routes corrected to match API mount point (`/api/v1/`)
- Dockerfile copies `README.md` for hatchling build metadata

### Changed

- Email notifications use stdlib `smtplib` instead of `red-mail` (unavailable)
- Test suite expanded from 24 to 59 tests
- Repository-wide Ruff lint compliance applied

## [0.1.0] - 2026-03-03

### Added

- Initial project scaffold
- FastAPI REST API for item management (CRUD)
- Multi-engine price search (Google Shopping via SerpAPI)
- Daily scheduled price checks via APScheduler
- On-demand price check endpoint
- Email notifications via SMTP
- SQLite database with SQLAlchemy ORM
- Docker and docker-compose setup
- Comprehensive test suite (24 tests)
- Environment-based configuration via pydantic-settings

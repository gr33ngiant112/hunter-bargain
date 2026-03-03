# Tasks — Todo

## Current Sprint

- [x] Project scaffold and directory structure
- [x] AGENTS.md (llmrules translated for OpenCode)
- [x] pyproject.toml, .env.example, CHANGELOG.md
- [x] Core config, database, models, schemas
- [x] FastAPI app with item CRUD endpoints
- [x] Search engine abstraction and implementations (Google Shopping, Bing Shopping via SerpAPI)
- [x] Price check orchestrator service
- [x] Email notification service (smtplib)
- [x] APScheduler daily check integration
- [x] Docker + docker-compose setup
- [x] Tests (24/24 passing)
- [x] README.md
- [x] Fix test fixtures (StaticPool for in-memory SQLite, lifespan patching)
- [x] Upgrade to Python 3.12 venv
- [x] Initialize git repo (GitFlow: main + develop)
- [x] Verify Docker build (image builds, container starts, /health responds)
- [x] Add .dockerignore
- [x] Clean up stale files (CLAUDE.md, rules/)
- [x] CLI tool (`hb` command) — add, rm, ls, update, check (18 tests)

## Backlog

- [ ] Run ruff lint pass
- [ ] Set up CI/CD
- [ ] Add more search engines (Amazon, etc.)

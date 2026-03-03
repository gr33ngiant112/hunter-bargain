# Lessons Learned

Track mistakes, corrections, and patterns to prevent recurrence.

| Date | Mistake | Root Cause | Prevention Rule |
|------|---------|------------|-----------------|
| 2026-03-03 | `red-mail` unavailable on Python 3.10 | Assumed library availability without checking PyPI compatibility | Always verify third-party package compatibility with target Python version before adding to deps |
| 2026-03-03 | TestClient integration tests failed with `no such table: items` despite dependency override working | SQLite in-memory databases (`sqlite:///:memory:`) are per-connection; FastAPI TestClient dispatches to a worker thread which gets a separate connection with no tables | Always use `poolclass=StaticPool` with `check_same_thread=False` for in-memory SQLite in FastAPI test fixtures |
| 2026-03-03 | Spent multiple iterations debugging patching when the real issue was SQLite threading | Focused on wrong hypothesis (patching not working) instead of verifying the override was actually being called | Add debug prints early to confirm which layer is broken before iterating on fixes |
| 2026-03-03 | `httpx.Response(json=..., text="")` ignores the json param and uses empty text as body | `text` parameter takes priority over `json` in httpx.Response constructor | Never pass both `json` and `text` to `httpx.Response` — pick one |
| 2026-03-03 | CLI hit `/items/` but API routes are mounted at `/api/v1/items/` — returned 404 | CLI was written without accounting for the `prefix="/api/v1"` on the router include in `main.py` | When adding a client (CLI, SDK, frontend), verify the full URL path against the actual router mount, not just the router's internal prefix |

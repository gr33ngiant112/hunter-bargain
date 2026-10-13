"""FastAPI application entry point.

Wires up routes, database initialization, and the background scheduler.
"""

import logging
import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from hunter_bargain import __version__
from hunter_bargain.api.items import router as items_router
from hunter_bargain.api.prices import router as prices_router
from hunter_bargain.api.runs import router as runs_router
from hunter_bargain.config import settings
from hunter_bargain.db import get_db, init_db
from hunter_bargain.logging_config import configure_logging
from hunter_bargain.services.scheduler import health_problem, start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    # Startup. Logging comes first: it also redacts the SerpAPI key from every log record, before
    # any request or scheduled job runs. LOG_LEVEL is the first setting read, so an invalid
    # setting stops the app here (#33: importing the app reads no settings).
    configure_logging(settings.log_level)
    logger.info("Initializing database...")
    init_db()
    logger.info("Starting scheduler...")
    start_scheduler()
    yield
    # Shutdown
    logger.info("Stopping scheduler...")
    stop_scheduler()


app = FastAPI(
    title="hunter-bargain",
    description=(
        "Price tracker and discovery bot — finds the lowest price across multiple search engines."
    ),
    version=__version__,
    lifespan=lifespan,
)

# Register API routers
app.include_router(items_router, prefix="/api/v1")
app.include_router(prices_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")


def _json_safe(value: object) -> object:
    """Replace NaN and infinite floats with their text ("nan", "inf"), which JSON can carry."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422 response, but with NaN and infinite inputs echoed as text.

    The 422 body echoes each rejected input. JSON has no NaN or Infinity, so with FastAPI's
    default handler a body such as {"target_price": NaN} turned the 422 into a 500.
    """
    errors = _json_safe(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get(
    "/health",
    responses={503: {"description": "The daily price check is not working: detail says why"}},
)
def health_check(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """{"status": "ok"} while the daily price check works; otherwise 503, with the reason.

    503 when the scheduler is not running, or when no check of all items has succeeded in the
    last 26 hours, counted from the app's start until one has. Docker Compose's healthcheck reads
    this endpoint.
    """
    problem = health_problem(db)
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)
    return {"status": "ok"}

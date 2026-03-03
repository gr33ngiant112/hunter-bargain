"""FastAPI application entry point.

Wires up routes, database initialization, and the background scheduler.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from hunter_bargain.api.items import router as items_router
from hunter_bargain.api.prices import router as prices_router
from hunter_bargain.config import settings
from hunter_bargain.db import init_db
from hunter_bargain.services.scheduler import start_scheduler, stop_scheduler

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    # Startup
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
    version="0.1.0",
    lifespan=lifespan,
)

# Register API routers
app.include_router(items_router, prefix="/api/v1")
app.include_router(prices_router, prefix="/api/v1")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "ok"}

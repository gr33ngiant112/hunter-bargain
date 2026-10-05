"""APScheduler-based daily price check scheduler.

Runs price checks for all tracked items on a configurable cron schedule.
Designed to run inside the same process as the FastAPI app.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from hunter_bargain.config import settings
from hunter_bargain.db import SessionLocal
from hunter_bargain.models import Item
from hunter_bargain.services.searcher import run_price_check

logger = logging.getLogger(__name__)

# Module-level scheduler instance — started/stopped by the FastAPI lifespan.
scheduler = BackgroundScheduler()


def _scheduled_price_check() -> None:
    """Job function: check prices for every tracked item.

    Creates its own database session since this runs in a background thread,
    outside of the FastAPI request lifecycle.
    """
    logger.info("Scheduled price check starting...")
    db = SessionLocal()
    try:
        items = db.query(Item).all()
        if not items:
            logger.info("No items to check — skipping.")
            return

        for item in items:
            try:
                result = run_price_check(item=item, db=db)
                logger.info(
                    "Item %d (%r): %d results, lowest=$%s",
                    item.id,
                    item.name,
                    result.results_count,
                    f"{result.lowest_price:.2f}" if result.lowest_price else "N/A",
                )
            except Exception:
                logger.exception("Price check failed for item %d (%r)", item.id, item.name)
    finally:
        db.close()
    logger.info("Scheduled price check complete.")


def start_scheduler() -> None:
    """Register the daily price check job and start the scheduler."""
    # Without a time zone the trigger would use the host's local time.
    trigger = CronTrigger.from_crontab(settings.price_check_cron, timezone=settings.price_check_tz)
    scheduler.add_job(
        _scheduled_price_check,
        trigger=trigger,
        id="daily_price_check",
        name="Daily price check for all items",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — cron: %s (%s)", settings.price_check_cron, settings.price_check_tz
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

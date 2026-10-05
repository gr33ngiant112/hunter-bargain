"""APScheduler-based daily price check scheduler.

Runs price checks for all tracked items on a configurable cron schedule.
Designed to run inside the same process as the FastAPI app.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from hunter_bargain.config import settings
from hunter_bargain.db import SessionLocal
from hunter_bargain.models import Item
from hunter_bargain.services.searcher import run_price_check

logger = logging.getLogger(__name__)

# Module-level scheduler instance — started/stopped by the FastAPI lifespan.
scheduler = BackgroundScheduler()


def _scheduled_price_check() -> None:
    """Job function: check prices for every tracked item.

    Runs in a background thread, outside any request, so it opens its own sessions: one to list
    the item ids, then one per item. An item deleted during the run is skipped, and a failed
    check rolls back only its own session, so neither stops the rest of the run.
    """
    logger.info("Scheduled price check starting...")
    with SessionLocal() as db:
        item_ids = list(db.scalars(select(Item.id).order_by(Item.id)))
    if not item_ids:
        logger.info("No items to check — skipping.")
        return

    for item_id in item_ids:
        _check_item(item_id)
    logger.info("Scheduled price check complete.")


def _check_item(item_id: int) -> None:
    """Check one item in a session of its own; on failure, log it and roll back."""
    with SessionLocal() as db:
        item = db.get(Item, item_id)
        if item is None:
            logger.info("Item %d was deleted before its check — skipping.", item_id)
            return
        # Read now: after a failed commit the item's attributes can no longer be loaded.
        name = item.name
        try:
            result = run_price_check(item=item, db=db)
            logger.info(
                "Item %d (%r): %d results, lowest=$%s",
                item_id,
                name,
                result.results_count,
                f"{result.lowest_price:.2f}" if result.lowest_price else "N/A",
            )
        except Exception:
            db.rollback()
            logger.exception("Price check failed for item %d (%r)", item_id, name)


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

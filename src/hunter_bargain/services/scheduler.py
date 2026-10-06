"""APScheduler-based daily price check scheduler.

Runs price checks for all tracked items on a configurable cron schedule.
Designed to run inside the same process as the FastAPI app.
"""

import logging
import threading

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

# One price check of all items at a time. The daily job and POST /prices/check-all share this
# lock, so overlapping runs cannot check, and alert on, the same items twice. It is a plain Lock
# because a check-all run takes it in the request's thread and releases it in its own thread.
# Like the scheduler, it belongs to one process: the image runs a single uvicorn process, and
# each extra worker (uvicorn --workers) would run its own daily job under its own lock.
_run_lock = threading.Lock()


def _scheduled_price_check() -> None:
    """Job function: check prices for every tracked item, unless a check of all items is running.

    An error that ends the run reaches APScheduler, which logs it.
    """
    if not _run_lock.acquire(blocking=False):
        logger.warning(
            "Scheduled price check skipped: a price check of all items is already running."
        )
        return
    try:
        _check_all_items()
    finally:
        _run_lock.release()


def start_check_all() -> bool:
    """Start a price check of every item in a background thread; False if one is running.

    The lock is taken before the thread starts, so a second caller is refused at once, and the
    thread releases it however the run ends. The thread is a daemon: stopping the app ends it,
    and items already checked keep their saved results.
    """
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=_run_check_all, name="check-all", daemon=True).start()
    except BaseException:
        _run_lock.release()
        raise
    return True


def _run_check_all() -> None:
    try:
        _check_all_items()
    except Exception:
        logger.exception("Price check of all items failed")
    finally:
        _run_lock.release()


def _check_all_items() -> None:
    """Check prices for every tracked item.

    Runs in a background thread, outside any request, so it opens its own sessions: one to list
    the item ids, then one per item. An item deleted during the run is skipped, and a failed
    check rolls back only its own session, so neither stops the rest of the run.
    """
    logger.info("Price check of all items starting...")
    with SessionLocal() as db:
        item_ids = list(db.scalars(select(Item.id).order_by(Item.id)))
    if not item_ids:
        logger.info("No items to check — skipping.")
        return

    for item_id in item_ids:
        _check_item(item_id)
    logger.info("Price check of all items complete.")


def _check_item(item_id: int) -> None:
    """Check one item in a session of its own; a failure is logged, and closing the session
    at the end of the with block rolls back whatever the failed check left uncommitted."""
    with SessionLocal() as db:
        try:
            item = db.get(Item, item_id)
        except Exception:
            logger.exception("Could not load item %d for its price check", item_id)
            return
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

"""APScheduler-based daily price check scheduler.

Runs price checks for all tracked items on a configurable cron schedule, and at startup
catches up a daily check that was missed while the app was down.
Designed to run inside the same process as the FastAPI app.
"""

import datetime as dt
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import func, select, update

from hunter_bargain.config import settings
from hunter_bargain.db import SessionLocal
from hunter_bargain.models import CheckRun, Item
from hunter_bargain.services.searcher import run_price_check

logger = logging.getLogger(__name__)

# Module-level scheduler instance — started/stopped by the FastAPI lifespan.
# APScheduler's own misfire_grace_time is 1 s: a run that starts more than 1 s after its time
# (the host was suspended or busy) is skipped. With 3 hours it still runs. coalesce runs a job
# once when several of its run times are overdue, and max_instances=1 never runs it twice at
# once. These settings do not cover time the app was down, as the jobs live in memory:
# start_scheduler catches up a missed daily run from the check_runs records instead.
scheduler = BackgroundScheduler(
    job_defaults={"misfire_grace_time": 3 * 3600, "coalesce": True, "max_instances": 1}
)

# A daily run missed while the app was down runs this long after the app starts.
CATCH_UP_DELAY = dt.timedelta(minutes=1)

# One price check of all items at a time. The daily job and POST /prices/check-all share this
# lock, so overlapping runs cannot check, and alert on, the same items twice. It is a plain Lock
# because a check-all run takes it in the request's thread and releases it in its own thread.
# Like the scheduler, it belongs to one process: the image runs a single uvicorn process, and
# each extra worker (uvicorn --workers) would run its own daily job under its own lock.
_run_lock = threading.Lock()


def _now() -> dt.datetime:
    """The current time in UTC (tests freeze it)."""
    return dt.datetime.now(dt.UTC)


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


@contextmanager
def _recorded_run() -> Iterator[None]:
    """Record the check of all items that runs in the block as a check_runs row.

    The row is committed as "running" before the block runs. When the block ends it gets its
    finished_at and "succeeded", or "failed" if an exception ended the block. A run the process
    does not live to finish stays "running". Each write uses a short session of its own.
    """
    with SessionLocal() as db:
        run = CheckRun(started_at=_now(), status="running")
        db.add(run)
        db.commit()
        run_id = run.id
    status = "failed"
    try:
        yield
        status = "succeeded"
    finally:
        with SessionLocal() as db:
            db.execute(
                update(CheckRun)
                .where(CheckRun.id == run_id)
                .values(finished_at=_now(), status=status)
            )
            db.commit()


def _check_all_items() -> None:
    """Check prices for every tracked item, and record the run in check_runs.

    Runs in a background thread, outside any request, so it opens its own sessions: one to list
    the item ids, then one per item. An item deleted during the run is skipped, and a failed
    check rolls back only its own session, so neither stops the rest of the run.
    """
    logger.info("Price check of all items starting...")
    with _recorded_run():
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
    """Register the daily price check job, catch up a missed daily run, start the scheduler."""
    # Without a time zone the trigger would use the host's local time.
    trigger = CronTrigger.from_crontab(settings.price_check_cron, timezone=settings.price_check_tz)
    scheduler.add_job(
        _scheduled_price_check,
        trigger=trigger,
        id="daily_price_check",
        name="Daily price check for all items",
        replace_existing=True,
    )
    _schedule_catch_up(trigger)
    scheduler.start()
    logger.info(
        "Scheduler started — cron: %s (%s)", settings.price_check_cron, settings.price_check_tz
    )


def _schedule_catch_up(trigger: CronTrigger) -> None:
    """Add one check of all items, CATCH_UP_DELAY from now, if a daily run was missed.

    A daily run was missed if one of the trigger's run times has passed since the last
    successful check of all items finished: the app was down then, or that run failed or did
    not finish. Checks started through POST /prices/check-all count too, and their finish time
    is used because the daily job skips its run while one is still running. With no successful
    check recorded (a new database, or one upgraded from a version without check_runs) there is
    nothing to compare with, and no check is added.
    """
    now = _now()
    with SessionLocal() as db:
        finished_at = db.scalar(
            select(func.max(CheckRun.finished_at)).where(CheckRun.status == "succeeded")
        )
    if finished_at is None:
        logger.info("No successful price check of all items is recorded yet: nothing to catch up.")
        return
    # Stored in UTC (_now), but SQLite returns datetimes without their time zone.
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=dt.UTC)
    missed = trigger.get_next_fire_time(None, finished_at)
    if missed is None or missed > now:
        return
    run_at = now + CATCH_UP_DELAY
    scheduler.add_job(
        _scheduled_price_check,
        trigger=DateTrigger(run_date=run_at),
        id="catch_up_price_check",
        name="Catch-up price check for a missed daily run",
        replace_existing=True,
    )
    logger.warning(
        "Missed the daily price check due at %s (the last successful check of all items "
        "finished at %s): a catch-up check of all items runs at %s.",
        missed,
        finished_at,
        run_at,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

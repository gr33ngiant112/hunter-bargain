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
from dataclasses import asdict, dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from hunter_bargain.config import settings
from hunter_bargain.db import SessionLocal
from hunter_bargain.models import CheckRun, Item
from hunter_bargain.schemas import PriceCheckResult
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

# GET /health fails once the last successful check of all items is older than this: the daily
# job's day, plus two hours for a run that starts late or takes long.
MAX_SUCCESS_AGE = dt.timedelta(hours=26)

# When start_scheduler started the scheduler. Until a check of all items has succeeded (a new
# database), GET /health measures from this time instead.
_started_at: dt.datetime | None = None

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


@dataclass
class _RunCounts:
    """What a check of all items did: the counts its check_runs row gets when it ends."""

    items_checked: int = 0
    searches_used: int = 0
    engine_errors: int = 0
    alerts_sent: int = 0

    def add(self, result: PriceCheckResult) -> None:
        """Count one item's check that ran to the end."""
        self.items_checked += 1
        self.searches_used += result.searches_used
        self.engine_errors += len(result.engine_errors)
        self.alerts_sent += int(result.alert_sent)


@contextmanager
def _recorded_run() -> Iterator[_RunCounts]:
    """Record the check of all items that runs in the block as a check_runs row.

    The row is committed as "running" before the block runs. When the block ends it gets its
    finished_at, "succeeded" (or "failed" if an exception ended the block) and the counts the
    block added to the _RunCounts it was given. A run the process does not live to finish stays
    "running", without counts. Each write uses a short session of its own.
    """
    with SessionLocal() as db:
        run = CheckRun(started_at=_now(), status="running")
        db.add(run)
        db.commit()
        run_id = run.id
    counts = _RunCounts()
    status = "failed"
    try:
        yield counts
        status = "succeeded"
    finally:
        with SessionLocal() as db:
            db.execute(
                update(CheckRun)
                .where(CheckRun.id == run_id)
                .values(finished_at=_now(), status=status, **asdict(counts))
            )
            db.commit()


def _check_all_items() -> None:
    """Check prices for every tracked item, and record the run in check_runs.

    Runs in a background thread, outside any request, so it opens its own sessions: one to list
    the item ids, then one per item. An item deleted during the run is skipped, and a failed
    check rolls back only its own session, so neither stops the rest of the run.
    """
    logger.info("Price check of all items starting...")
    with _recorded_run() as counts:
        with SessionLocal() as db:
            item_ids = list(db.scalars(select(Item.id).order_by(Item.id)))
        if not item_ids:
            logger.info("No items to check — skipping.")
            return

        for item_id in item_ids:
            result = _check_item(item_id)
            if result is not None:
                counts.add(result)
    logger.info("Price check of all items complete.")


def _check_item(item_id: int) -> PriceCheckResult | None:
    """Check one item in a session of its own and return the result; None if the item is gone
    or its check failed. A failure is logged, and closing the session at the end of the with
    block rolls back whatever the failed check left uncommitted."""
    with SessionLocal() as db:
        try:
            item = db.get(Item, item_id)
        except Exception:
            logger.exception("Could not load item %d for its price check", item_id)
            return None
        if item is None:
            logger.info("Item %d was deleted before its check — skipping.", item_id)
            return None
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
            return None
        return result


def start_scheduler() -> None:
    """Register the daily price check job, catch up a missed daily run, start the scheduler."""
    global _started_at
    now = _now()
    # Without a time zone the trigger would use the host's local time.
    trigger = CronTrigger.from_crontab(settings.price_check_cron, timezone=settings.price_check_tz)
    scheduler.add_job(
        _scheduled_price_check,
        trigger=trigger,
        id="daily_price_check",
        name="Daily price check for all items",
        replace_existing=True,
    )
    _schedule_catch_up(trigger, now)
    scheduler.start()
    _started_at = now
    logger.info(
        "Scheduler started — cron: %s (%s)", settings.price_check_cron, settings.price_check_tz
    )


def _last_success(db: Session) -> dt.datetime | None:
    """When the last successful check of all items finished, in UTC; None if none has."""
    finished_at = db.scalar(
        select(func.max(CheckRun.finished_at)).where(CheckRun.status == "succeeded")
    )
    # Stored in UTC (_now), but SQLite returns datetimes without their time zone.
    if finished_at is not None and finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=dt.UTC)
    return finished_at


def _schedule_catch_up(trigger: CronTrigger, now: dt.datetime) -> None:
    """Add one check of all items, CATCH_UP_DELAY from now, if a daily run was missed.

    A daily run was missed if one of the trigger's run times has passed since the last
    successful check of all items finished: the app was down then, or that run failed or did
    not finish. Checks started through POST /prices/check-all count too, and their finish time
    is used because the daily job skips its run while one is still running. With no successful
    check recorded (a new database, or one upgraded from a version without check_runs) there is
    nothing to compare with, and no check is added.
    """
    with SessionLocal() as db:
        finished_at = _last_success(db)
    if finished_at is None:
        logger.info("No successful price check of all items is recorded yet: nothing to catch up.")
        return
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


def health_problem(db: Session) -> str | None:
    """Why the daily price check is not working, for GET /health; None if it is.

    It is not when the scheduler is not running, or when the last successful check of all items
    finished more than MAX_SUCCESS_AGE ago. Until one has succeeded (a new database), the time is
    measured from the scheduler's start, so a fresh install is healthy for its first day.
    """
    if not scheduler.running or _started_at is None:
        return "The scheduler is not running: no daily price check will run."
    last_success = _last_success(db)
    since = last_success or _started_at
    if _now() - since <= MAX_SUCCESS_AGE:
        return None
    hours = MAX_SUCCESS_AGE // dt.timedelta(hours=1)
    if last_success is None:
        return (
            "No check of all items has succeeded since the app started at"
            f" {since.isoformat(timespec='seconds')}, more than {hours} hours ago."
        )
    return (
        "The last successful check of all items finished at"
        f" {since.isoformat(timespec='seconds')}, more than {hours} hours ago."
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

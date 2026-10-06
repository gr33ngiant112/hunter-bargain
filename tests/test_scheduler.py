"""Tests for the scheduled price check job."""

import datetime as dt
import functools
import logging
import os
import threading
import time
from unittest.mock import patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from hunter_bargain.config import Settings, settings
from hunter_bargain.models import CheckRun, Item, PriceRecord
from hunter_bargain.schemas import PriceCheckResult
from hunter_bargain.services import scheduler as scheduler_module
from hunter_bargain.services.scheduler import _scheduled_price_check, start_scheduler


def test_scheduled_check_logs_item_names_with_repr(db_session, caplog):
    """Line breaks in item names stay inside their log records (logged with %r)."""
    checked = Item(name="Widget\nFake record", notify_email="a@x.com")
    failing = Item(name="Gadget\r\nFake record", notify_email="b@x.com")
    db_session.add_all([checked, failing])
    db_session.commit()
    checked_id, failing_id = checked.id, failing.id

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        if item.id == failing_id:
            raise RuntimeError("engine unavailable")
        return PriceCheckResult(
            item_id=item.id,
            item_name=item.name,
            lowest_price=None,
            lowest_source=None,
            lowest_url=None,
            results_count=0,
            records=[],
        )

    # The job's own session is the test database; run_price_check is faked, so no engine runs.
    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", return_value=db_session),
        patch("hunter_bargain.services.scheduler.run_price_check", side_effect=fake_check),
        caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"),
    ):
        _scheduled_price_check()

    messages = [record.getMessage() for record in caplog.records]
    assert f"Item {checked_id} ('Widget\\nFake record'): 0 results, lowest=$N/A" in messages
    assert f"Price check failed for item {failing_id} ('Gadget\\r\\nFake record')" in messages


@pytest.fixture
def daily_trigger(monkeypatch, db_session):
    """Return a function that runs start_scheduler and returns the daily job's trigger."""
    fresh = BackgroundScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", fresh)
    # start_scheduler reads the check_runs records: an empty test database, so no catch-up.
    monkeypatch.setattr(scheduler_module, "SessionLocal", _sessions(db_session))

    def start():
        start_scheduler()
        return fresh.get_job("daily_price_check").trigger

    yield start
    if fresh.running:
        fresh.shutdown(wait=False)


def test_price_check_time_zone_defaults_to_utc(monkeypatch):
    monkeypatch.delenv("PRICE_CHECK_TZ", raising=False)

    assert Settings(_env_file=None).price_check_tz == "UTC"


@pytest.mark.parametrize("zone", ["UTC", "America/Chicago", "Asia/Kolkata"])
def test_daily_job_runs_in_the_configured_time_zone(daily_trigger, monkeypatch, zone):
    """The cron fires in PRICE_CHECK_TZ, not in the host's local time zone."""
    monkeypatch.setattr(settings, "price_check_tz", zone)

    # APScheduler turns "UTC" into datetime.timezone.utc and other names into ZoneInfo objects.
    assert str(daily_trigger().timezone) == zone


def _empty_result(item: Item) -> PriceCheckResult:
    return PriceCheckResult(
        item_id=item.id,
        item_name=item.name,
        lowest_price=None,
        lowest_source=None,
        lowest_url=None,
        results_count=0,
        records=[],
    )


@pytest.fixture
def three_items(db_session):
    """Three items in the test database; returns their ids in order."""
    items = [Item(name=name, notify_email="a@x.com") for name in ("First", "Second", "Third")]
    db_session.add_all(items)
    db_session.commit()
    return [item.id for item in items]


def _sessions(db_session):
    """Sessions on the test database, as SessionLocal makes them on the real one."""
    return sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)


def _run_job(db_session, fake_check):
    """Run the daily job against the test database, with run_price_check faked by fake_check."""
    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", _sessions(db_session)),
        patch("hunter_bargain.services.scheduler.run_price_check", side_effect=fake_check),
    ):
        _scheduled_price_check()


def test_item_deleted_during_the_run_does_not_stop_the_rest(db_session, three_items):
    """#10: deleting the second item during the first item's check skips only that item."""
    first, second, third = three_items
    checked: list[int] = []

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        checked.append(item.id)
        if item.id == first:
            # A DELETE through the API's own session while the job runs, then the commit that
            # run_price_check makes after saving its price records.
            with _sessions(db_session)() as api_db:
                api_db.delete(api_db.get(Item, second))
                api_db.commit()
            db.commit()
        return _empty_result(item)

    _run_job(db_session, fake_check)

    assert checked == [first, third]


def test_database_error_on_one_item_does_not_stop_the_rest(db_session, three_items):
    """#10: a failed write for one item is rolled back, and the next item's check is saved."""
    first, second, third = three_items

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        # price is NOT NULL, so the second item's flush fails.
        price = None if item.id == second else 9.99
        db.add(PriceRecord(item_id=item.id, price=price, source="google_shopping"))
        db.commit()
        return _empty_result(item)

    _run_job(db_session, fake_check)

    db_session.expire_all()
    saved = sorted(record.item_id for record in db_session.query(PriceRecord))
    assert saved == [first, third]


def test_failed_check_is_logged_with_the_item_id_and_name(db_session, three_items, caplog):
    first, second, third = three_items

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        if item.id == second:
            db.add(PriceRecord(item_id=item.id, price=None, source="google_shopping"))
            db.commit()
        return _empty_result(item)

    with caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"):
        _run_job(db_session, fake_check)

    [failure] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failure.getMessage() == f"Price check failed for item {second} ('Second')"
    assert failure.exc_info is not None and failure.exc_info[0] is IntegrityError


def test_check_that_fails_after_its_item_was_deleted_is_logged_and_the_run_continues(
    db_session, three_items, caplog
):
    """#10: the failure is logged with the id and name read before the check.

    run_price_check commits once it has saved its price records, which expires the item. If the
    item is deleted after that and the check then fails, reading item.name in the handler would
    raise ObjectDeletedError again and end the run.
    """
    first, second, third = three_items
    checked: list[int] = []

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        checked.append(item.id)
        if item.id == second:
            db.commit()
            with _sessions(db_session)() as api_db:
                api_db.delete(api_db.get(Item, second))
                api_db.commit()
            raise RuntimeError("alert could not be sent")
        return _empty_result(item)

    with caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"):
        _run_job(db_session, fake_check)

    assert checked == [first, second, third]
    [failure] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failure.getMessage() == f"Price check failed for item {second} ('Second')"


def test_database_error_loading_an_item_does_not_stop_the_rest(
    db_session, three_items, monkeypatch, caplog
):
    """#10: a DB error while the job loads one item (a locked database) skips only that item."""
    first, second, third = three_items
    load = Session.get

    def get(self, entity, ident, *args, **kwargs):
        if entity is Item and ident == second:
            raise OperationalError("SELECT items", {}, Exception("database is locked"))
        return load(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", get)
    checked: list[int] = []

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        checked.append(item.id)
        return _empty_result(item)

    with caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"):
        _run_job(db_session, fake_check)

    assert checked == [first, third]
    [failure] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failure.getMessage() == f"Could not load item {second} for its price check"
    assert failure.exc_info is not None and failure.exc_info[0] is OperationalError


def test_failed_check_leaves_none_of_its_writes_behind(db_session, three_items):
    """#10: closing the item's session rolls back what a failed check wrote but did not commit."""
    first, second, third = three_items

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        db.add(PriceRecord(item_id=item.id, price=9.99, source="google_shopping"))
        if item.id == second:
            db.flush()  # written to the database, not committed
            raise RuntimeError("alert could not be sent")
        db.commit()
        return _empty_result(item)

    _run_job(db_session, fake_check)

    db_session.expire_all()
    saved = sorted(record.item_id for record in db_session.query(PriceRecord))
    assert saved == [first, third]


def test_daily_job_is_skipped_while_a_check_of_all_items_runs(db_session, three_items, caplog):
    """#6: when the cron trigger fires during a check-all run, no item is checked twice."""
    first, second, third = three_items
    checked: list[int] = []
    started, finish = threading.Event(), threading.Event()

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        checked.append(item.id)
        if item.id == first:
            started.set()
            finish.wait(timeout=5)
        return _empty_result(item)

    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", _sessions(db_session)),
        patch("hunter_bargain.services.scheduler.run_price_check", side_effect=fake_check),
        caplog.at_level(logging.WARNING, logger="hunter_bargain.services.scheduler"),
    ):
        assert scheduler_module.start_check_all() is True
        try:
            if not started.wait(timeout=5):
                pytest.fail("setup: the check of all items never started")
            _scheduled_price_check()  # the daily trigger fires during the run
        finally:
            finish.set()
            if not scheduler_module._run_lock.acquire(timeout=5):
                pytest.fail("the check of all items did not end within 5 s")
            scheduler_module._run_lock.release()

    assert checked == [first, second, third]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
        "Scheduled price check skipped: a price check of all items is already running."
    ]


def test_daily_job_frees_the_run_lock_when_it_fails():
    """#6: the job's error still reaches APScheduler's log, and the next run is not blocked."""
    broken = patch(
        "hunter_bargain.services.scheduler.SessionLocal",
        side_effect=OperationalError("SELECT items.id", {}, Exception("unable to open database")),
    )
    with broken, pytest.raises(OperationalError):
        _scheduled_price_check()

    assert scheduler_module._run_lock.acquire(blocking=False)
    scheduler_module._run_lock.release()


# #15: each check of all items is recorded in check_runs, and a daily run missed while the app
# was down is caught up at startup.


def _utc(day: int, hour: int, minute: int = 0) -> dt.datetime:
    """A time in October 2026, in UTC."""
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=dt.UTC)


def _freeze_clock(monkeypatch, *times: dt.datetime) -> None:
    """The scheduler's clock returns these times, one per call."""
    clock = iter(times)
    monkeypatch.setattr(scheduler_module, "_now", lambda: next(clock))


def _add_run(db_session, status: str, started_at: dt.datetime, finished_at: dt.datetime | None):
    db_session.add(CheckRun(status=status, started_at=started_at, finished_at=finished_at))
    db_session.commit()


def _runs(db_session) -> list[tuple[str, dt.datetime, dt.datetime | None]]:
    """Each check_runs row as (status, started_at, finished_at). SQLite returns the times
    without a time zone; they are stored in UTC."""

    def utc(value: dt.datetime | None) -> dt.datetime | None:
        return value.replace(tzinfo=dt.UTC) if value is not None else None

    db_session.expire_all()
    runs = db_session.scalars(select(CheckRun).order_by(CheckRun.id))
    return [(run.status, utc(run.started_at), utc(run.finished_at)) for run in runs]


def _messages(caplog, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == scheduler_module.__name__ and record.levelno == level
    ]


def _run_daily_job() -> None:
    _scheduled_price_check()


def _run_check_all() -> None:
    """POST /prices/check-all's background run, waited for."""
    assert scheduler_module.start_check_all() is True
    if not scheduler_module._run_lock.acquire(timeout=5):
        pytest.fail("the check of all items did not end within 5 s")
    scheduler_module._run_lock.release()


both_entry_points = pytest.mark.parametrize(
    "run_all", [_run_daily_job, _run_check_all], ids=["daily job", "check-all"]
)


@both_entry_points
def test_check_of_all_items_is_recorded_as_running_then_succeeded(
    db_session, three_items, monkeypatch, run_all
):
    """The row says "running" while the run lasts; a failed item check does not fail the run."""
    first, second, third = three_items
    _freeze_clock(monkeypatch, _utc(6, 9), _utc(6, 9, 2))
    during_the_run: list[tuple[object, ...]] = []

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        if item.id == first:
            with _sessions(db_session)() as other:
                rows = other.execute(select(CheckRun.status, CheckRun.finished_at))
                during_the_run.extend(tuple(row) for row in rows)
        if item.id == second:
            raise RuntimeError("engine unavailable")
        return _empty_result(item)

    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", _sessions(db_session)),
        patch("hunter_bargain.services.scheduler.run_price_check", side_effect=fake_check),
    ):
        run_all()

    assert during_the_run == [("running", None)]
    assert _runs(db_session) == [("succeeded", _utc(6, 9), _utc(6, 9, 2))]


@both_entry_points
def test_check_with_no_items_is_recorded_as_succeeded(db_session, monkeypatch, run_all):
    _freeze_clock(monkeypatch, _utc(6, 9), _utc(6, 9))

    with patch("hunter_bargain.services.scheduler.SessionLocal", _sessions(db_session)):
        run_all()

    assert _runs(db_session) == [("succeeded", _utc(6, 9), _utc(6, 9))]


# Listing the items fails, as with a locked database: an error that ends the whole run.
LIST_ITEMS_FAILS = OperationalError("SELECT items.id", {}, Exception("database is locked"))


def test_daily_job_that_an_error_ends_is_recorded_as_failed(db_session, three_items, monkeypatch):
    _freeze_clock(monkeypatch, _utc(6, 9), _utc(6, 9, 1))

    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", _sessions(db_session)),
        patch.object(Session, "scalars", side_effect=LIST_ITEMS_FAILS),
        pytest.raises(OperationalError),  # raised to APScheduler, which logs it
    ):
        _run_daily_job()

    assert _runs(db_session) == [("failed", _utc(6, 9), _utc(6, 9, 1))]


def test_check_all_run_that_an_error_ends_is_recorded_as_failed(
    db_session, three_items, monkeypatch, caplog
):
    _freeze_clock(monkeypatch, _utc(6, 9), _utc(6, 9, 1))

    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", _sessions(db_session)),
        patch.object(Session, "scalars", side_effect=LIST_ITEMS_FAILS),
        caplog.at_level(logging.ERROR, logger="hunter_bargain.services.scheduler"),
    ):
        _run_check_all()

    assert _messages(caplog, logging.ERROR) == ["Price check of all items failed"]
    assert _runs(db_session) == [("failed", _utc(6, 9), _utc(6, 9, 1))]


def test_scheduler_job_defaults_are_explicit():
    """A run that starts up to 3 hours late still runs; APScheduler's own grace time is 1 s."""
    assert scheduler_module.scheduler._job_defaults == {
        "misfire_grace_time": 3 * 3600,
        "coalesce": True,
        "max_instances": 1,
    }


@pytest.fixture
def jobs_at(db_session, monkeypatch):
    """Return a function that runs start_scheduler at a frozen time and returns its jobs by id.

    The daily job runs at 09:00 UTC, the check_runs records are the test database's, and the
    scheduler is a fresh one, started paused so that none of its jobs runs.
    """
    monkeypatch.setattr(settings, "price_check_cron", "0 9 * * *")
    monkeypatch.setattr(settings, "price_check_tz", "UTC")
    monkeypatch.setattr(scheduler_module, "SessionLocal", _sessions(db_session))
    fresh = BackgroundScheduler()
    monkeypatch.setattr(fresh, "start", functools.partial(fresh.start, paused=True))
    monkeypatch.setattr(scheduler_module, "scheduler", fresh)

    def start(now: dt.datetime) -> dict:
        _freeze_clock(monkeypatch, now)
        start_scheduler()
        return {job.id: job for job in fresh.get_jobs()}

    yield start
    if fresh.running:
        fresh.shutdown(wait=False)


def test_daily_run_missed_while_the_app_was_down_is_caught_up_once(db_session, jobs_at, caplog):
    """The app was down at 09:00 on Oct 6 and starts at 10:00: one check of all items at 10:01."""
    _add_run(db_session, "succeeded", _utc(5, 9), _utc(5, 9, 5))
    now = _utc(6, 10)

    with caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"):
        jobs = jobs_at(now)

    assert sorted(jobs) == ["catch_up_price_check", "daily_price_check"]
    catch_up = jobs["catch_up_price_check"]
    assert catch_up.func is _scheduled_price_check
    assert catch_up.next_run_time == now + dt.timedelta(minutes=1)
    # A single run: its trigger has no run time after that one.
    assert catch_up.trigger.get_next_fire_time(catch_up.next_run_time, _utc(31, 0)) is None
    assert _messages(caplog, logging.WARNING) == [
        "Missed the daily price check due at 2026-10-06 09:00:00+00:00 (the last successful"
        " check of all items finished at 2026-10-05 09:05:00+00:00): a catch-up check of all"
        " items runs at 2026-10-06 10:01:00+00:00."
    ]


def test_no_catch_up_when_the_last_success_is_after_the_last_daily_run_time(db_session, jobs_at):
    """Oct 6's 09:00 run succeeded; at 08:59 on Oct 7 the next one is not due yet."""
    _add_run(db_session, "succeeded", _utc(5, 9), _utc(5, 9, 5))
    _add_run(db_session, "succeeded", _utc(6, 9), _utc(6, 9, 5))

    assert sorted(jobs_at(_utc(7, 8, 59))) == ["daily_price_check"]


def test_check_that_ran_over_the_daily_run_time_counts_for_that_run(db_session, jobs_at):
    """A check-all run from 08:58 to 09:03 made the daily job skip 09:00 and checked every item.

    Its finish time is compared, not its start time: no catch-up.
    """
    _add_run(db_session, "succeeded", _utc(6, 8, 58), _utc(6, 9, 3))

    assert sorted(jobs_at(_utc(6, 12))) == ["daily_price_check"]


@pytest.mark.parametrize("status", ["failed", "running"])
def test_failed_or_unfinished_check_since_the_last_success_is_caught_up(
    db_session, jobs_at, status
):
    """Oct 6's 09:00 run failed, or the app stopped during it: caught up at the next start."""
    _add_run(db_session, "succeeded", _utc(5, 9), _utc(5, 9, 5))
    _add_run(db_session, status, _utc(6, 9), _utc(6, 9, 1) if status == "failed" else None)

    assert sorted(jobs_at(_utc(6, 12))) == ["catch_up_price_check", "daily_price_check"]


@pytest.mark.parametrize("status", [None, "failed", "running"], ids=["no run", "failed", "running"])
def test_no_catch_up_without_a_successful_check_on_record(db_session, jobs_at, caplog, status):
    """A new database, or one just upgraded to check_runs: no check starts at the first start."""
    if status is not None:
        _add_run(db_session, status, _utc(5, 9), _utc(5, 9, 1) if status == "failed" else None)

    with caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"):
        jobs = jobs_at(_utc(6, 10))

    assert sorted(jobs) == ["daily_price_check"]
    assert "No successful price check of all items is recorded yet: nothing to catch up." in (
        _messages(caplog, logging.INFO)
    )


@pytest.fixture
def host_time_zone():
    """Return a function that sets the process's local time zone (TZ) until the test ends."""
    saved = os.environ.get("TZ")

    def set_zone(zone: str) -> None:
        os.environ["TZ"] = zone
        time.tzset()

    yield set_zone
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset to set the local zone")
@pytest.mark.parametrize(
    ("zone", "finished_at", "now", "caught_up"),
    [
        # Taken as local time, 09:05 UTC on Oct 5 would be 03:35 UTC, before that day's run.
        ("Asia/Kolkata", _utc(5, 9, 5), _utc(6, 8), False),
        # Taken as local time, 08:59 UTC on Oct 6 would be 13:59 UTC, after that day's run.
        ("America/Chicago", _utc(6, 8, 59), _utc(6, 12), True),
    ],
    ids=["Asia/Kolkata", "America/Chicago"],
)
def test_run_times_read_back_from_sqlite_are_taken_as_utc(
    db_session, jobs_at, host_time_zone, zone, finished_at, now, caught_up
):
    """SQLite returns the stored UTC times without a time zone, whatever the host's zone is."""
    host_time_zone(zone)
    _add_run(db_session, "succeeded", finished_at - dt.timedelta(minutes=5), finished_at)
    assert db_session.scalar(select(CheckRun.finished_at)).tzinfo is None  # as SQLite returns it

    jobs = jobs_at(now)

    assert ("catch_up_price_check" in jobs) is caught_up

"""Tests for the scheduled price check job."""

import logging
from unittest.mock import patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from hunter_bargain.config import Settings, settings
from hunter_bargain.models import Item, PriceRecord
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
def daily_trigger(monkeypatch):
    """Return a function that runs start_scheduler and returns the daily job's trigger."""
    fresh = BackgroundScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", fresh)

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

"""Tests for the price check API endpoints."""

import logging
import threading
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from hunter_bargain.schemas import PriceCheckResult
from hunter_bargain.services import scheduler as scheduler_module


def test_check_price_not_found(client):
    """POST /api/v1/prices/check/999 returns 404."""
    resp = client.post("/api/v1/prices/check/999")
    assert resp.status_code == 404


@patch("hunter_bargain.api.prices.run_price_check")
def test_check_price_success(mock_run, client):
    """POST /api/v1/prices/check/{id} triggers a price check."""
    # Create an item first
    create_resp = client.post(
        "/api/v1/items/",
        json={"name": "Gadget", "notify_email": "g@x.com", "target_price": 50.0},
    )
    item_id = create_resp.json()["id"]

    mock_run.return_value = PriceCheckResult(
        item_id=item_id,
        item_name="Gadget",
        lowest_price=39.99,
        lowest_source="google_shopping",
        lowest_merchant="Walmart",
        lowest_url="http://example.com",
        results_count=1,
        records=[],
    )

    resp = client.post(f"/api/v1/prices/check/{item_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lowest_price"] == 39.99
    assert data["item_name"] == "Gadget"
    assert (data["lowest_source"], data["lowest_merchant"]) == ("google_shopping", "Walmart")


def _add_item(client, name: str) -> int:
    resp = client.post("/api/v1/items/", json={"name": name, "notify_email": "g@x.com"})
    return resp.json()["id"]


def _empty_result(item) -> PriceCheckResult:
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
def run_sessions(db_session):
    """Background runs open their sessions on the test database, as SessionLocal does."""
    sessions = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    with patch("hunter_bargain.services.scheduler.SessionLocal", sessions):
        yield


def _wait_for_the_run_to_end() -> None:
    """Return once no check of all items holds the run lock (fail after 5 s)."""
    if not scheduler_module._run_lock.acquire(timeout=5):
        pytest.fail("the check of all items did not end within 5 s")
    scheduler_module._run_lock.release()


def _blocking_check(checked: list[int], started: threading.Event, finish: threading.Event):
    """A run_price_check that records the item and waits for finish, so a run stays active."""

    def check(item, db) -> PriceCheckResult:
        checked.append(item.id)
        started.set()
        if not finish.wait(timeout=5):
            raise TimeoutError("the test never let the check finish")
        return _empty_result(item)

    return check


def test_check_all_answers_202_at_once_and_checks_every_item_in_the_background(
    client, run_sessions
):
    """#6: check-all returns while the check still runs, and a second call meanwhile gets 409."""
    first, second = _add_item(client, "First"), _add_item(client, "Second")
    checked: list[int] = []
    started, finish = threading.Event(), threading.Event()

    with patch(
        "hunter_bargain.services.scheduler.run_price_check",
        side_effect=_blocking_check(checked, started, finish),
    ):
        try:
            resp = client.post("/api/v1/prices/check-all")
            if not started.wait(timeout=5):
                pytest.fail("setup: the background check never started")
            # The first item's check has not finished yet.
            again = client.post("/api/v1/prices/check-all")
        finally:
            finish.set()
            _wait_for_the_run_to_end()

    assert resp.status_code == 202
    assert resp.json() == {"status": "started"}
    assert again.status_code == 409
    assert again.json() == {"detail": "A price check of all items is already running"}
    assert checked == [first, second]


def test_check_all_returns_409_while_the_daily_job_runs(client, run_sessions):
    """#6: the API and the daily job share one run lock."""
    _add_item(client, "First")
    checked: list[int] = []
    started, finish = threading.Event(), threading.Event()

    with patch(
        "hunter_bargain.services.scheduler.run_price_check",
        side_effect=_blocking_check(checked, started, finish),
    ):
        daily_job = threading.Thread(target=scheduler_module._scheduled_price_check)
        daily_job.start()
        try:
            if not started.wait(timeout=5):
                pytest.fail("setup: the daily job never started its check")
            resp = client.post("/api/v1/prices/check-all")
        finally:
            finish.set()
            daily_job.join(timeout=5)

    assert resp.status_code == 409
    assert len(checked) == 1  # only the daily job's check ran


def test_failed_check_all_run_is_logged_and_frees_the_lock(caplog):
    """#6: a run that fails (here: the database cannot be opened) does not block later runs."""
    broken = patch(
        "hunter_bargain.services.scheduler.SessionLocal",
        side_effect=OperationalError("SELECT items.id", {}, Exception("unable to open database")),
    )
    with broken, caplog.at_level(logging.ERROR, logger="hunter_bargain.services.scheduler"):
        assert scheduler_module.start_check_all() is True
        _wait_for_the_run_to_end()

    [failure] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failure.getMessage() == "Price check of all items failed"
    assert failure.exc_info is not None and failure.exc_info[0] is OperationalError

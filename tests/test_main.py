"""The app's own routes, in main.py."""

import datetime as dt
import functools
from collections.abc import Iterator

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from hunter_bargain.models import CheckRun
from hunter_bargain.services import scheduler as scheduler_module

# When the app starts in these tests.
STARTED = dt.datetime(2026, 10, 6, 10, 0, tzinfo=dt.UTC)


class Clock:
    """The scheduler's clock (_now), set by the test."""

    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def clock(client, db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    """The app's scheduler, started at STARTED, and its clock, which a test moves on.

    The client fixture leaves the scheduler out of the app's startup; this runs the real
    start_scheduler, on a fresh scheduler started paused so that no job runs, with the check_runs
    records in the test database.
    """
    clock = Clock(STARTED)
    monkeypatch.setattr(scheduler_module, "_now", clock)
    monkeypatch.setattr(
        scheduler_module,
        "SessionLocal",
        sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False),
    )
    fresh = BackgroundScheduler()
    monkeypatch.setattr(fresh, "start", functools.partial(fresh.start, paused=True))
    monkeypatch.setattr(scheduler_module, "scheduler", fresh)
    monkeypatch.setattr(scheduler_module, "_started_at", None)  # restored after the test
    scheduler_module.start_scheduler()
    yield clock
    if fresh.running:
        fresh.shutdown(wait=False)


def _add_run(db_session: Session, status: str, finished_at: dt.datetime) -> None:
    started_at = finished_at - dt.timedelta(minutes=2)
    db_session.add(CheckRun(status=status, started_at=started_at, finished_at=finished_at))
    db_session.commit()


def test_health_check(client, clock):
    """GET /health is ok while the scheduler runs and a check of all items is not overdue: here
    the app has just started on a new database, and no check has run yet."""
    resp = client.get("/health")
    assert resp.status_code == 200
    # Exactly this body: Docker Compose's healthcheck and CI's smoke test of the image read it.
    assert resp.text == '{"status":"ok"}'


def test_health_is_503_when_the_scheduler_never_started(client):
    """The client fixture leaves start_scheduler out of the app's startup: no daily check runs."""
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "The scheduler is not running: no daily price check will run."}


def test_health_is_503_once_the_scheduler_stops(client, clock):
    assert client.get("/health").status_code == 200

    scheduler_module.stop_scheduler()

    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "The scheduler is not running: no daily price check will run."}


def test_health_is_503_when_the_last_successful_check_is_over_26_hours_old(
    client, clock, db_session
):
    finished = dt.datetime(2026, 10, 6, 9, 2, tzinfo=dt.UTC)
    _add_run(db_session, "succeeded", finished)

    clock.now = finished + dt.timedelta(hours=26)
    assert client.get("/health").status_code == 200

    clock.now = finished + dt.timedelta(hours=26, seconds=1)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {
        "detail": "The last successful check of all items finished at 2026-10-06T09:02:00+00:00,"
        " more than 26 hours ago."
    }

    # The next day's check succeeds.
    _add_run(db_session, "succeeded", clock.now)
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("status", ["failed", "running"])
def test_failed_or_unfinished_check_does_not_count_as_a_success(client, clock, db_session, status):
    """A newer run that failed, or that the app did not live to finish, leaves the success old."""
    _add_run(db_session, "succeeded", STARTED - dt.timedelta(hours=20))
    _add_run(db_session, status, STARTED + dt.timedelta(hours=4))

    clock.now = STARTED + dt.timedelta(hours=7)

    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith(
        "The last successful check of all items finished at 2026-10-05T14:00:00+00:00"
    )


def test_new_database_is_healthy_for_26_hours_after_the_app_starts(client, clock, db_session):
    """No check of all items has succeeded yet, so the time is measured from the app's start;
    a failed check does not end the wait."""
    _add_run(db_session, "failed", STARTED + dt.timedelta(hours=23))

    clock.now = STARTED + dt.timedelta(hours=26)
    assert client.get("/health").status_code == 200

    clock.now = STARTED + dt.timedelta(hours=26, seconds=1)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {
        "detail": "No check of all items has succeeded since the app started at"
        " 2026-10-06T10:00:00+00:00, more than 26 hours ago."
    }

    _add_run(db_session, "succeeded", clock.now)
    assert client.get("/health").status_code == 200


def test_last_success_counts_from_before_the_app_started(client, clock, db_session):
    """After the app was down for two days, /health says so until a check succeeds again (the
    catch-up check runs a minute after the start)."""
    _add_run(db_session, "succeeded", STARTED - dt.timedelta(days=2))

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["detail"].startswith(
        "The last successful check of all items finished at 2026-10-04T10:00:00+00:00"
    )

"""GET /api/v1/runs: the record of each price check of all items (#24)."""

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from hunter_bargain.models import CheckRun


def _utc(day: int, hour: int, minute: int = 0, second: int = 0) -> dt.datetime:
    """A time in October 2026, in UTC."""
    return dt.datetime(2026, 10, day, hour, minute, second, tzinfo=dt.UTC)


def _add_runs(db_session: Session, count: int) -> None:
    """`count` successful daily runs, one a day from October 1, each with its counts."""
    for day in range(1, count + 1):
        db_session.add(
            CheckRun(
                started_at=_utc(day, 9),
                finished_at=_utc(day, 9, 0, 41),
                status="succeeded",
                items_checked=day,
                searches_used=2 * day,
                engine_errors=0,
                alerts_sent=0,
            )
        )
    db_session.commit()


def test_no_runs_yet(client):
    resp = client.get("/api/v1/runs")

    assert resp.status_code == 200
    assert resp.json() == []


def test_runs_newest_first_with_their_counts_and_utc_times(client, db_session):
    """Times are stored in UTC and SQLite returns them without a zone; the API says UTC."""
    db_session.add_all(
        [
            CheckRun(
                started_at=_utc(5, 9),
                finished_at=_utc(5, 9, 0, 41),
                status="succeeded",
                items_checked=3,
                searches_used=4,
                engine_errors=2,
                alerts_sent=1,
            ),
            CheckRun(
                started_at=_utc(6, 9),
                finished_at=_utc(6, 9, 0, 5),
                status="failed",
                items_checked=0,
                searches_used=0,
                engine_errors=0,
                alerts_sent=0,
            ),
            # Running, or the app did not live to finish it: no counts yet.
            CheckRun(started_at=_utc(7, 9), status="running"),
        ]
    )
    db_session.commit()

    resp = client.get("/api/v1/runs")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": 3,
            "started_at": "2026-10-07T09:00:00Z",
            "finished_at": None,
            "status": "running",
            "items_checked": None,
            "searches_used": None,
            "engine_errors": None,
            "alerts_sent": None,
        },
        {
            "id": 2,
            "started_at": "2026-10-06T09:00:00Z",
            "finished_at": "2026-10-06T09:00:05Z",
            "status": "failed",
            "items_checked": 0,
            "searches_used": 0,
            "engine_errors": 0,
            "alerts_sent": 0,
        },
        {
            "id": 1,
            "started_at": "2026-10-05T09:00:00Z",
            "finished_at": "2026-10-05T09:00:41Z",
            "status": "succeeded",
            "items_checked": 3,
            "searches_used": 4,
            "engine_errors": 2,
            "alerts_sent": 1,
        },
    ]


def test_runs_default_to_the_latest_ten(client, db_session):
    _add_runs(db_session, 12)

    runs = client.get("/api/v1/runs").json()

    assert [run["items_checked"] for run in runs] == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]


def test_limit_sets_how_many_runs(client, db_session):
    _add_runs(db_session, 3)

    runs = client.get("/api/v1/runs", params={"limit": 1}).json()

    assert [run["started_at"] for run in runs] == ["2026-10-03T09:00:00Z"]


@pytest.mark.parametrize("limit", [0, 101, "all"])
def test_limit_outside_1_to_100_is_refused(client, limit):
    resp = client.get("/api/v1/runs", params={"limit": limit})

    assert resp.status_code == 422

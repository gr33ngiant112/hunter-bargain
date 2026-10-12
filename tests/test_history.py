"""Price history (#31): GET /items/{id}/prices and /stats, and the lows run_price_check reads."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hunter_bargain.config import settings
from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.services import history
from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.searcher import run_price_check

NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.UTC)


def _at(day: int, hour: int, month: int = 10) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, 0, tzinfo=dt.UTC)


# (price, checked_at) for the item; the 10.00 at 10-10 ties with today's 10.00.
PRICES = [
    (12.00, _at(12, 8)),
    (10.00, _at(12, 9)),
    (11.00, _at(11, 20)),
    (18.00, _at(11, 21)),  # the 11th's median is 13.00, its mean 14.00
    (13.00, _at(11, 22)),
    (10.00, _at(10, 10)),
    (9.00, _at(1, 10, month=7)),  # 103 days before NOW: only the all-time low sees it
]


def _add_item(db, name: str = "Widget") -> Item:
    item = Item(name=name, notify_email="user@example.com", target_price=20.0)
    db.add(item)
    db.commit()
    return item


def _add_prices(db, item: Item, prices: list[tuple[float, dt.datetime]]) -> None:
    for price, checked_at in prices:
        db.add(
            PriceRecord(
                item_id=item.id, price=price, source="google_shopping", checked_at=checked_at
            )
        )
    db.commit()


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(history, "_now", lambda: NOW)


@pytest.fixture
def widget(db_session, frozen_now):
    """An item with PRICES, and another item's price that must never show up."""
    item = _add_item(db_session)
    _add_prices(db_session, item, PRICES)
    _add_prices(db_session, _add_item(db_session, "Other"), [(1.00, _at(12, 10))])
    return item


def test_prices_are_listed_newest_first(client, widget):
    resp = client.get(f"/api/v1/items/{widget.id}/prices")

    assert resp.status_code == 200
    assert [r["price"] for r in resp.json()] == [10.00, 12.00, 13.00, 18.00, 11.00, 10.00, 9.00]
    assert {r["item_id"] for r in resp.json()} == {widget.id}


def test_prices_limit_keeps_the_newest(client, widget):
    resp = client.get(f"/api/v1/items/{widget.id}/prices", params={"limit": 2})

    assert [r["price"] for r in resp.json()] == [10.00, 12.00]


@pytest.mark.parametrize(
    "since", ["2026-10-11T21:00:00Z", "2026-10-11T23:00:00+02:00", "2026-10-11T21:00:00"]
)
def test_prices_since_keeps_those_from_then_on(client, widget, since):
    """The same moment with a zone, in another zone, or without a zone (read as UTC)."""
    resp = client.get(f"/api/v1/items/{widget.id}/prices", params={"since": since})

    assert [r["price"] for r in resp.json()] == [10.00, 12.00, 13.00, 18.00]


def test_stats_give_each_days_low_and_median_and_the_lows(client, widget):
    resp = client.get(f"/api/v1/items/{widget.id}/stats", params={"days": 90})

    assert resp.status_code == 200
    stats = resp.json()
    assert (stats["item_id"], stats["item_name"], stats["days"]) == (widget.id, "Widget", 90)
    assert stats["per_day"] == [
        {"day": "2026-10-10", "low": 10.0, "median": 10.0, "count": 1},
        {"day": "2026-10-11", "low": 11.0, "median": 13.0, "count": 3},
        {"day": "2026-10-12", "low": 10.0, "median": 11.0, "count": 2},
    ]
    # Of the two 10.00 prices, the later one.
    low = stats["lowest_in_days"]
    assert (low["price"], low["checked_at"][:16]) == (10.0, "2026-10-12T09:00")
    assert (low["currency"], low["source"]) == ("USD", "google_shopping")
    all_time = stats["all_time_low"]
    assert (all_time["price"], all_time["checked_at"][:16]) == (9.0, "2026-07-01T10:00")


def test_stats_window_starts_days_before_now(client, widget):
    resp = client.get(f"/api/v1/items/{widget.id}/stats", params={"days": 1})

    stats = resp.json()
    assert [d["day"] for d in stats["per_day"]] == ["2026-10-11", "2026-10-12"]
    assert stats["per_day"][0]["count"] == 3  # 11-20:00 is before NOW minus one day
    assert stats["lowest_in_days"]["price"] == 10.0
    assert stats["all_time_low"]["price"] == 9.0


def test_stats_without_prices(client, db_session, frozen_now):
    item = _add_item(db_session)

    stats = client.get(f"/api/v1/items/{item.id}/stats").json()

    assert (stats["days"], stats["per_day"]) == (90, [])
    assert (stats["lowest_in_days"], stats["all_time_low"]) == (None, None)
    assert client.get(f"/api/v1/items/{item.id}/prices").json() == []


@pytest.mark.parametrize("path", ["prices", "stats"])
def test_unknown_item_is_404(client, path):
    resp = client.get(f"/api/v1/items/999/{path}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item 999 not found"


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("prices", {"limit": 0}),
        ("prices", {"limit": 1001}),
        ("prices", {"since": "yesterday"}),
        ("stats", {"days": 0}),
        ("stats", {"days": 3651}),
    ],
)
def test_out_of_range_parameters_are_422(client, widget, path, params):
    assert client.get(f"/api/v1/items/{widget.id}/{path}", params=params).status_code == 422


def test_price_check_reads_the_lows_with_its_own_prices_and_sends_them(db_session):
    """The 90-day low ignores a lower price from 100 days ago; the all-time low doesn't."""
    item = _add_item(db_session)
    now = dt.datetime.now(dt.UTC)
    _add_prices(
        db_session,
        item,
        [
            (9.00, now - dt.timedelta(days=200)),
            (10.00, now - dt.timedelta(days=100)),
            (15.00, now - dt.timedelta(days=30)),
            (10.50, now - dt.timedelta(days=10)),
        ],
    )
    engine = MagicMock()
    engine.name = "fake_engine"
    engine.search.return_value = [
        SearchResult(title="Widget", price=12.0, currency="USD", source="fake_engine")
    ]

    with (
        patch("hunter_bargain.services.searcher._ENGINES", [engine]),
        patch("hunter_bargain.services.searcher.send_price_alert", return_value=True) as send,
    ):
        result = run_price_check(item=item, db=db_session)

    assert result.lowest_price == 12.0
    assert result.low_90_days is not None and result.all_time_low is not None
    assert result.low_90_days.price == 10.50
    assert result.low_90_days.checked_at.date() == (now - dt.timedelta(days=10)).date()
    assert result.all_time_low.price == 9.00
    assert send.call_args.kwargs["low_90_days"] == result.low_90_days
    assert send.call_args.kwargs["all_time_low"] == result.all_time_low


def test_a_new_low_is_its_own_90_day_and_all_time_low(db_session):
    item = _add_item(db_session)
    _add_prices(db_session, item, [(15.00, dt.datetime.now(dt.UTC) - dt.timedelta(days=5))])
    engine = MagicMock()
    engine.name = "fake_engine"
    engine.search.return_value = [
        SearchResult(title="Widget", price=12.0, currency="USD", source="fake_engine")
    ]

    with (
        patch("hunter_bargain.services.searcher._ENGINES", [engine]),
        patch("hunter_bargain.services.searcher.send_price_alert", return_value=True),
    ):
        result = run_price_check(item=item, db=db_session)

    assert result.low_90_days is not None and result.all_time_low is not None
    assert (result.low_90_days.price, result.low_90_days.source) == (12.0, "fake_engine")
    assert result.all_time_low == result.low_90_days


# #29: the lows leave out merchants that may not alert, as the alert does. A record saved before
# merchants were stored has none, and counts as a listing that names no merchant.


@pytest.fixture
def merchants(db_session, frozen_now):
    """An item's prices: a denylist candidate's $5, Walmart's $8, and an old record's $6 that
    has no merchant."""
    item = _add_item(db_session)
    for price, merchant, checked_at in [
        (5.00, "Scam Shop", _at(12, 9)),
        (8.00, "Walmart", _at(12, 9)),
        (6.00, None, _at(2, 9)),
    ]:
        db_session.add(
            PriceRecord(
                item_id=item.id,
                price=price,
                source="google_shopping",
                merchant=merchant,
                checked_at=checked_at,
            )
        )
    db_session.commit()
    return item


@pytest.mark.parametrize(
    ("denylist", "allowlist", "low"),
    [
        ([], [], 5.00),
        # The old record without a merchant counts like a listing that names none...
        (["Scam Shop"], [], 6.00),
        # ...which an allowlist leaves out.
        ([], ["Walmart"], 8.00),
        (["scam  shop"], ["Walmart", "Scam Shop"], 8.00),
        (["Scam Shop", "Walmart"], ["Scam Shop", "Walmart"], None),
    ],
)
def test_lows_leave_out_merchants_that_may_not_alert(
    db_session, merchants, monkeypatch, denylist, allowlist, low
):
    monkeypatch.setattr(settings, "merchant_denylist", denylist)
    monkeypatch.setattr(settings, "merchant_allowlist", allowlist)

    all_time = history.lowest_price(db_session, merchants.id)
    in_90_days = history.lowest_price(db_session, merchants.id, since=history.days_ago(90))

    assert (all_time.price if all_time else None) == low
    assert (in_90_days.price if in_90_days else None) == low


def test_stats_lows_leave_out_a_denylisted_merchant(client, db_session, merchants, monkeypatch):
    monkeypatch.setattr(settings, "merchant_denylist", ["Scam Shop"])

    stats = client.get(f"/api/v1/items/{merchants.id}/stats", params={"days": 30}).json()

    assert (stats["lowest_in_days"]["price"], stats["all_time_low"]["price"]) == (6.0, 6.0)
    # The prices themselves are all still listed, and name their merchant.
    prices = client.get(f"/api/v1/items/{merchants.id}/prices").json()
    assert [(p["price"], p["merchant"]) for p in prices] == [
        (8.0, "Walmart"),
        (5.0, "Scam Shop"),
        (6.0, None),
    ]

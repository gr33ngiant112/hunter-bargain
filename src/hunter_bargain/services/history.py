"""An item's price history: its recorded prices, per-day lows and medians, and its lowest prices.

The records are the ones each price check stores (searcher.run_price_check), one per relevant
listing. Times are stored in UTC, and SQLite returns them without a zone.
"""

import datetime as dt
import statistics
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from hunter_bargain.models import PriceRecord
from hunter_bargain.schemas import DailyPrices, PriceLow

# The period of the "lowest in 90 days" price that alerts and `hb check` show.
LOW_WINDOW = dt.timedelta(days=90)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def days_ago(days: int) -> dt.datetime:
    """The moment `days` days before now."""
    return _now() - dt.timedelta(days=days)


def as_utc(moment: dt.datetime) -> dt.datetime:
    """The same moment in UTC; a time without a zone is taken to be UTC, as stored."""
    return moment.replace(tzinfo=dt.UTC) if moment.tzinfo is None else moment.astimezone(dt.UTC)


def recent_records(
    db: Session, item_id: int, *, since: dt.datetime | None = None, limit: int = 100
) -> list[PriceRecord]:
    """The item's records, newest first: at most `limit`, recorded at or after `since` if given."""
    query = select(PriceRecord).where(PriceRecord.item_id == item_id)
    if since is not None:
        query = query.where(PriceRecord.checked_at >= as_utc(since))
    query = query.order_by(PriceRecord.checked_at.desc(), PriceRecord.id.desc()).limit(limit)
    return list(db.scalars(query))


def daily_prices(db: Session, item_id: int, since: dt.datetime) -> list[DailyPrices]:
    """The low, median and count of the item's prices on each UTC day since `since`."""
    rows = db.execute(
        select(PriceRecord.price, PriceRecord.checked_at).where(
            PriceRecord.item_id == item_id, PriceRecord.checked_at >= as_utc(since)
        )
    )
    by_day: defaultdict[dt.date, list[float]] = defaultdict(list)
    for price, checked_at in rows:
        by_day[as_utc(checked_at).date()].append(price)
    return [
        DailyPrices(day=day, low=min(prices), median=statistics.median(prices), count=len(prices))
        for day, prices in sorted(by_day.items())
    ]


def lowest_price(db: Session, item_id: int, since: dt.datetime | None = None) -> PriceLow | None:
    """The item's lowest recorded price, since `since` if given; on a tie, its latest time."""
    query = select(PriceRecord).where(PriceRecord.item_id == item_id)
    if since is not None:
        query = query.where(PriceRecord.checked_at >= as_utc(since))
    query = query.order_by(PriceRecord.price, PriceRecord.checked_at.desc()).limit(1)
    record = db.scalars(query).first()
    if record is None:
        return None
    low = PriceLow.model_validate(record)
    return low.model_copy(update={"checked_at": as_utc(low.checked_at)})

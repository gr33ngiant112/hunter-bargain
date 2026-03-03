"""Multi-engine price search orchestrator.

Queries all registered search engines in sequence, aggregates results,
persists price records, and triggers notifications when targets are met.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.schemas import PriceCheckResult, PriceRecordResponse
from hunter_bargain.services.engines.base import SearchEngine, SearchResult
from hunter_bargain.services.engines.bing import BingShoppingEngine
from hunter_bargain.services.engines.google import GoogleShoppingEngine
from hunter_bargain.services.notifier import send_price_alert

logger = logging.getLogger(__name__)

# Registry of active search engines. Add new engines here.
_ENGINES: list[SearchEngine] = [
    GoogleShoppingEngine(),
    BingShoppingEngine(),
]


def _build_query(item: Item) -> str:
    """Construct a search query from an item's name and optional keywords."""
    parts = [item.name]
    if item.keywords:
        parts.append(item.keywords)
    return " ".join(parts)


def _persist_results(results: list[SearchResult], item: Item, db: Session) -> list[PriceRecord]:
    """Save search results as PriceRecord rows and return them."""
    records: list[PriceRecord] = []
    for r in results:
        record = PriceRecord(
            item_id=item.id,
            price=r.price,
            currency=r.currency,
            source=r.source,
            url=r.url,
            title=r.title,
        )
        db.add(record)
        records.append(record)
    db.commit()
    # Refresh to populate auto-generated fields (id, checked_at)
    for rec in records:
        db.refresh(rec)
    return records


def run_price_check(item: Item, db: Session) -> PriceCheckResult:
    """Execute a price check across all engines for a single item.

    1. Build search query from item name + keywords.
    2. Query each registered engine.
    3. Persist all price observations.
    4. If any price meets the target, fire an email notification.
    5. Return structured results.
    """
    query = _build_query(item)
    logger.info("Running price check for item %d: %r", item.id, query)

    all_results: list[SearchResult] = []
    for engine in _ENGINES:
        try:
            results = engine.search(query)
            all_results.extend(results)
        except Exception:
            logger.exception("Engine %s failed for item %d", engine.name, item.id)

    # Sort all results by price ascending
    all_results.sort(key=lambda r: r.price)

    # Persist to database
    records = _persist_results(all_results, item, db)

    # Determine lowest price
    lowest: SearchResult | None = all_results[0] if all_results else None

    # Check if target price is met and notify
    if lowest and item.target_price and lowest.price <= item.target_price:
        logger.info(
            "Target met for item %d (%s): $%.2f <= $%.2f",
            item.id,
            item.name,
            lowest.price,
            item.target_price,
        )
        send_price_alert(item=item, result=lowest)

    return PriceCheckResult(
        item_id=item.id,
        item_name=item.name,
        lowest_price=lowest.price if lowest else None,
        lowest_source=lowest.source if lowest else None,
        lowest_url=lowest.url if lowest else None,
        results_count=len(all_results),
        records=[PriceRecordResponse.model_validate(r) for r in records],
    )

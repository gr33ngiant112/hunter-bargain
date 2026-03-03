"""Multi-engine price search orchestrator.

Queries all registered search engines in sequence, aggregates results,
persists price records, and triggers notifications when targets are met.
"""

from __future__ import annotations

import logging
import re

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
    """Construct a search query from an item's name and optional keywords.

    Keywords may be comma-separated (e.g. "unlocked,256GB") — these are
    split into individual terms so the search engine treats them as separate
    words rather than a single blob.
    """
    parts = [item.name]
    if item.keywords:
        kw_terms = [t.strip() for t in item.keywords.split(",") if t.strip()]
        parts.extend(kw_terms)
    return " ".join(parts)


_ACCESSORY_TERMS = frozenset(
    {
        "case",
        "cover",
        "protector",
        "screen",
        "film",
        "tempered",
        "glass",
        "sleeve",
        "pouch",
        "skin",
        "decal",
        "sticker",
        "mount",
        "holder",
        "stand",
        "charger",
        "cable",
        "adapter",
        "strap",
        "band",
        "armband",
        "holster",
        "wallet",
        "folio",
        "bumper",
        "shell",
        "grip",
        "kickstand",
        "stylus",
        "pen",
        "earbuds",
        "earphones",
        "replacement",
        "cleaning",
        "cloth",
        "wipe",
        "ring",
        "lanyard",
        "dock",
        "cradle",
        "keyboard",
        "trackpad",
        "mousepad",
        "webcam",
        "tripod",
        "selfie",
        "gimbal",
        "lens",
        "filter",
        "temper",
    }
)

# Extensions (from Google Shopping) that indicate an accessory listing
_ACCESSORY_EXTENSIONS = frozenset(
    {
        "protective case",
        "phone case",
        "screen protector",
        "accessory",
        "accessories",
        "case",
        "cover",
        "cable",
        "charger",
        "adapter",
        "mount",
        "holder",
        "stand",
    }
)

_NOISE_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "for",
        "and",
        "or",
        "of",
        "in",
        "with",
        "to",
        "by",
    }
)

_MIN_RELEVANCE_RATIO = 0.5
_PRICE_FLOOR_RATIO = 0.10


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text) if len(w) >= 2}


def _has_accessory_extension(result: SearchResult) -> bool:
    """Return True if the result's extensions metadata indicates an accessory."""
    if not result.extensions:
        return False
    return any(ext.lower() in _ACCESSORY_EXTENSIONS for ext in result.extensions)


def _is_relevant(result: SearchResult, item_name: str, target_price: float | None = None) -> bool:
    """Check whether a search result is relevant to the target item.

    A result is irrelevant (returns False) when any of these hold:
    - Its price falls below the price floor (10% of target_price).
    - Its extensions metadata contains accessory-category terms.
    - Fewer than half the significant item-name words appear in the title.
    - The title contains accessory terms absent from the item name.
    """
    if target_price and target_price > 0 and result.price < target_price * _PRICE_FLOOR_RATIO:
        return False

    if _has_accessory_extension(result):
        return False

    name_tokens = _tokenize(item_name) - _NOISE_WORDS
    if not name_tokens:
        return True

    title_tokens = _tokenize(result.title)

    matched = name_tokens & title_tokens
    ratio = len(matched) / len(name_tokens)
    if ratio < _MIN_RELEVANCE_RATIO:
        return False

    name_lower = item_name.lower()
    return all(not (term in title_tokens and term not in name_lower) for term in _ACCESSORY_TERMS)


def _filter_relevant(results: list[SearchResult], item: Item) -> list[SearchResult]:
    relevant = [r for r in results if _is_relevant(r, item.name, item.target_price)]
    filtered_count = len(results) - len(relevant)
    if filtered_count:
        logger.info(
            "Relevance filter: kept %d of %d results for item %d (%d filtered out)",
            len(relevant),
            len(results),
            item.id,
            filtered_count,
        )
    return relevant


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

    relevant_results = _filter_relevant(all_results, item)

    relevant_results.sort(key=lambda r: r.price)

    records = _persist_results(relevant_results, item, db)

    lowest: SearchResult | None = relevant_results[0] if relevant_results else None

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
        results_count=len(relevant_results),
        records=[PriceRecordResponse.model_validate(r) for r in records],
    )

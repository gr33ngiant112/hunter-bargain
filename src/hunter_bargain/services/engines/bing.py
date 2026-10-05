"""Bing Shopping search via SerpAPI."""

from __future__ import annotations

import logging

from hunter_bargain.config import settings
from hunter_bargain.services.engines.base import (
    EngineError,
    SearchEngine,
    SearchResult,
    fetch_serpapi,
    is_installment_offer,
    is_usd_price,
    usable_price,
)

logger = logging.getLogger(__name__)


class BingShoppingEngine(SearchEngine):
    """Search Bing Shopping for product prices using SerpAPI.

    Requires a valid SERPAPI_KEY in environment configuration.
    """

    @property
    def name(self) -> str:
        return "bing_shopping"

    def search(self, query: str) -> list[SearchResult]:
        if not settings.serpapi_key:
            raise EngineError("SERPAPI_KEY is not set")

        params = {
            "engine": "bing_shopping",
            "q": query,
            "api_key": settings.serpapi_key.get_secret_value(),
            "count": 10,
        }
        data = fetch_serpapi(params)
        shopping_results = data.get("shopping_results", [])

        results: list[SearchResult] = []
        for item in shopping_results:
            title = item.get("title", "")
            price_str = item.get("price")
            # A payment-plan amount is not the item's price: never compare it with the target.
            if is_installment_offer(item, "installments"):
                logger.debug(
                    "Bing Shopping: skipped payment-plan offer %r (price %r)", title, price_str
                )
                continue
            if not is_usd_price(price_str):
                logger.debug("Bing Shopping: skipped %r, no USD price (price %r)", title, price_str)
                continue
            price = item.get("extracted_price")
            if price is None:
                price = _parse_price(price_str)
            # A bad row is skipped on its own; the other rows still count.
            price = usable_price(price)
            if price is None:
                logger.debug(
                    "Bing Shopping: skipped %r, no usable price (extracted_price %r, price %r)",
                    title,
                    item.get("extracted_price"),
                    price_str,
                )
                continue

            results.append(
                SearchResult(
                    title=title,
                    price=price,
                    currency="USD",
                    source=self.name,
                    # external_link is the merchant's page; link is a bing.com URL.
                    url=item.get("external_link") or item.get("link"),
                    merchant=item.get("seller") or None,
                )
            )

        results.sort(key=lambda r: r.price)
        logger.info("Bing Shopping: found %d results for %r", len(results), query)
        return results


def _parse_price(price_str: str) -> float | None:
    """Extract a numeric price from a string like '$1,299.00'."""
    if not price_str:
        return None
    cleaned = price_str.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None

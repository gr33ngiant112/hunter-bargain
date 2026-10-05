"""Google Shopping search via SerpAPI."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import requests
from serpapi import GoogleSearch

from hunter_bargain.config import settings
from hunter_bargain.services.engines.base import SearchEngine, SearchResult

logger = logging.getLogger(__name__)


class GoogleShoppingEngine(SearchEngine):
    """Search Google Shopping for product prices using SerpAPI.

    Requires a valid SERPAPI_KEY in environment configuration.
    """

    @property
    def name(self) -> str:
        return "google_shopping"

    def search(self, query: str) -> list[SearchResult]:
        if not settings.serpapi_key:
            logger.warning("SERPAPI_KEY not configured — skipping Google Shopping search")
            return []

        try:
            params = {
                "engine": "google_shopping",
                "q": query,
                "api_key": settings.serpapi_key.get_secret_value(),
                "num": 10,
            }
            search = GoogleSearch(params)
            data = search.get_dict()
            shopping_results = data.get("shopping_results", [])

            results: list[SearchResult] = []
            for item in shopping_results:
                # SerpAPI returns extracted_price as a float or price as a string like "$1,299.00"
                price = item.get("extracted_price")
                if price is None:
                    # Try parsing the price string
                    price_str = item.get("price", "")
                    price = _parse_price(price_str)

                if price is not None and price > 0:
                    raw_ext = item.get("extensions")
                    extensions = tuple(raw_ext) if raw_ext else None
                    results.append(
                        SearchResult(
                            title=item.get("title", ""),
                            price=price,
                            currency="USD",
                            source=self.name,
                            url=item.get("link"),
                            extensions=extensions,
                        )
                    )

            # Sort by price ascending — cheapest first
            results.sort(key=lambda r: r.price)
            logger.info("Google Shopping: found %d results for %r", len(results), query)
            return results

        except requests.RequestException as e:
            # The request URL carries the API key: log the error type and host only.
            url = getattr(e.request, "url", None) or ""
            logger.error(
                "Google Shopping search failed for %r: %s (host %s)",
                query,
                type(e).__name__,
                urlsplit(url).hostname,
            )
            return []
        except Exception:
            logger.exception("Google Shopping search failed for %r", query)
            return []


def _parse_price(price_str: str) -> float | None:
    """Extract a numeric price from a string like '$1,299.00'."""
    if not price_str:
        return None
    # Strip currency symbols and commas
    cleaned = price_str.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None

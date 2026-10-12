"""Google Shopping and Bing Shopping searches via SerpAPI: one engine, a field map for each."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from hunter_bargain.config import settings
from hunter_bargain.services.engines.base import (
    EngineError,
    SearchEngine,
    SearchResult,
    fetch_serpapi,
    is_installment_offer,
    is_usd_price,
    parse_usd_amount,
    usable_price,
)

logger = logging.getLogger(__name__)

# SerpAPI's engine parameter, which is also the engine's name in results and records.
EngineName = Literal["google_shopping", "bing_shopping"]


@dataclass(frozen=True)
class RowFields:
    """The fields of an engine's shopping_results rows that a SearchResult is built from.

    Attributes:
        label: The engine's name in log lines.
        installment: The payment-plan object of a row.
        merchant: The seller's name.
        url: The listing's link.
        fallback_url: The link to use when a row has no ``url`` field, or None.
        extensions: Metadata tags, or None if the engine's rows have none.
        condition: The condition of a second-hand offer, or None if the engine's rows have none.
    """

    label: str
    installment: str
    merchant: str
    url: str
    fallback_url: str | None = None
    extensions: str | None = None
    condition: str | None = None


ROW_FIELDS: dict[EngineName, RowFields] = {
    "google_shopping": RowFields(
        label="Google Shopping",
        installment="installment",
        merchant="source",
        # Rows carry no merchant URL: product_link is the Google product page.
        url="product_link",
        extensions="extensions",
        # Set on a second-hand offer, e.g. "used" or "refurbished": any value counts (#3).
        condition="second_hand_condition",
    ),
    "bing_shopping": RowFields(
        label="Bing Shopping",
        installment="installments",
        merchant="seller",
        # external_link is the merchant's page; link is a bing.com URL.
        url="external_link",
        fallback_url="link",
    ),
}


class SerpApiShoppingEngine(SearchEngine):
    """Search Google Shopping or Bing Shopping for product prices using SerpAPI.

    Requires a valid SERPAPI_KEY in environment configuration.
    """

    def __init__(self, engine: EngineName) -> None:
        self._engine = engine
        self._fields = ROW_FIELDS[engine]

    @property
    def name(self) -> str:
        return self._engine

    def search(self, query: str) -> list[SearchResult]:
        if not settings.serpapi_key:
            raise EngineError("SERPAPI_KEY is not set")

        params = {
            "engine": self._engine,
            "q": query,
            "api_key": settings.serpapi_key.get_secret_value(),
        }
        data = fetch_serpapi(params)
        shopping_results = data.get("shopping_results", [])
        fields = self._fields

        results: list[SearchResult] = []
        for item in shopping_results:
            title = item.get("title", "")
            price_str = item.get("price")
            # A payment-plan amount is not the item's price: never compare it with the target.
            if is_installment_offer(item, fields.installment):
                logger.debug(
                    "%s: skipped payment-plan offer %r (price %r)", fields.label, title, price_str
                )
                continue
            if not is_usd_price(price_str):
                logger.debug(
                    "%s: skipped %r, no USD price (price %r)", fields.label, title, price_str
                )
                continue
            # SerpAPI returns extracted_price as a float or price as a string like "$1,299.00"
            price = item.get("extracted_price")
            if price is None:
                # Try parsing the price string
                price = parse_usd_amount(price_str)
            # A bad row is skipped on its own; the other rows still count.
            price = usable_price(price)
            if price is None:
                logger.debug(
                    "%s: skipped %r, no usable price (extracted_price %r, price %r)",
                    fields.label,
                    title,
                    item.get("extracted_price"),
                    price_str,
                )
                continue

            url = item.get(fields.url)
            if not url and fields.fallback_url:
                url = item.get(fields.fallback_url)
            raw_ext = item.get(fields.extensions) if fields.extensions else None
            condition = None
            if fields.condition:
                condition = str(item.get(fields.condition) or "").strip() or None
            results.append(
                SearchResult(
                    title=title,
                    price=price,
                    currency="USD",
                    source=self.name,
                    url=url,
                    extensions=tuple(raw_ext) if raw_ext else None,
                    merchant=item.get(fields.merchant) or None,
                    condition=condition,
                )
            )

        # Sort by price ascending — cheapest first
        results.sort(key=lambda r: r.price)
        logger.info("%s: found %d results for %r", fields.label, len(results), query)
        return results

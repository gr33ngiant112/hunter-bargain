"""Abstract base class for all search engine implementations."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# A US dollar price as SerpAPI shows it for US results: "$" and then the amount, e.g. "$1,299.00".
_USD_PRICE = re.compile(r"\$\d")

# A payment-plan price ends with the payment period, e.g. "$20.99/mo".
_INSTALLMENT_SUFFIXES = ("/mo", "/wk")


@dataclass(frozen=True)
class SearchResult:
    """A single price result from a search engine.

    Attributes:
        title: Product listing title.
        price: Numeric price value.
        currency: ISO 4217 currency code (default USD).
        source: Engine identifier (e.g. "google_shopping").
        url: Direct link to the product listing.
        extensions: Optional metadata tags from the search engine (e.g.
            ``["Smartphone", "5G", "OLED"]`` from Google Shopping).  Useful
            for distinguishing product categories from accessories.
        merchant: Seller named by the listing (Google Shopping ``source``,
            Bing ``seller``).  Third-party text: escape it wherever it is shown.
    """

    title: str
    price: float
    currency: str
    source: str
    url: str | None = None
    extensions: tuple[str, ...] | None = None
    merchant: str | None = None


def is_usd_price(price: object) -> bool:
    """Return True if a SerpAPI price string is in US dollars, e.g. "$1,299.00".

    Item targets are in USD and the requests set no country (gl), but a row can still be priced
    in another currency, e.g. "TRY 28,782.94". Any other string, or no string, is not USD. A
    plain "$" cannot tell other dollar currencies apart, so it counts as USD.
    """
    return isinstance(price, str) and _USD_PRICE.match(price.strip()) is not None


def is_installment_offer(row: dict[str, Any], installment_key: str) -> bool:
    """Return True if a SerpAPI row offers a payment plan, so its price is not the full price.

    Such a row carries an installment object (``installment`` on Google Shopping,
    ``installments`` on Bing) or a price string ending in the period, e.g. "$20.99/mo".
    """
    if row.get(installment_key):
        return True
    price = row.get("price")
    return isinstance(price, str) and price.strip().lower().endswith(_INSTALLMENT_SUFFIXES)


class SearchEngine(ABC):
    """Interface that every search engine must implement.

    Each engine takes a search query and returns a list of SearchResult objects.
    Engines should handle their own error cases gracefully (return empty list on failure).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable engine name used in logs and records."""

    @abstractmethod
    def search(self, query: str) -> list[SearchResult]:
        """Search for products matching *query* and return price results.

        Args:
            query: Product search string (e.g. "iPhone 15 Pro 256GB").

        Returns:
            List of SearchResult objects, sorted by price ascending.
            Returns an empty list if the search fails or yields no results.
        """

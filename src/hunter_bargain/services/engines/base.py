"""Abstract base class for all search engine implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """A single price result from a search engine.

    Attributes:
        title: Product listing title.
        price: Numeric price value.
        currency: ISO 4217 currency code (default USD).
        source: Engine identifier (e.g. "google_shopping").
        url: Direct link to the product listing.
    """

    title: str
    price: float
    currency: str
    source: str
    url: str | None = None


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

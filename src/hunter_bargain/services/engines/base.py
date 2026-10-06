"""Abstract base class for all search engine implementations, and their shared SerpAPI call."""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from serpapi import SerpApiClient

logger = logging.getLogger(__name__)

# A US dollar price as SerpAPI shows it for US results: "$" and then the amount, e.g. "$1,299.00".
_USD_PRICE = re.compile(r"\$\d")

# A payment-plan price ends with the payment period, e.g. "$20.99/mo".
_INSTALLMENT_SUFFIXES = ("/mo", "/wk")

# Seconds to wait for SerpAPI to connect, and then for each read. The client's default is
# 60000 s (16.7 hours), long enough for one stalled search to hold the scheduler's job thread.
SERPAPI_TIMEOUT = 20

# SerpAPI's error string is third-party text: an engine error keeps at most this many characters.
_MAX_ERROR_LENGTH = 200


class EngineError(Exception):
    """The engine could not search, so its lack of results does not mean "no results".

    The message is built from the HTTP status and SerpAPI's own ``error`` string only. Exception
    text and the request URL carry the API key, so neither goes into it: the message is logged
    and returned by the API (PriceCheckResult.engine_errors).
    """


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
        condition: Condition of a second-hand offer (Google Shopping
            ``second_hand_condition``, e.g. "used" or "refurbished"); None when the
            row gives none.  Bing rows have no condition field.
    """

    title: str
    price: float
    currency: str
    source: str
    url: str | None = None
    extensions: tuple[str, ...] | None = None
    merchant: str | None = None
    condition: str | None = None


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


def usable_price(price: object) -> float | None:
    """Return a row's price as a float if it is a real number, finite and above zero.

    The JSON can hold anything here: a string, a boolean, NaN or Infinity (Python's json module
    reads both), or an integer too large for a float. None means the row has no usable price.
    """
    if isinstance(price, bool) or not isinstance(price, int | float):
        return None
    try:
        value = float(price)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def fetch_serpapi(params: dict[str, Any]) -> dict[str, Any]:
    """Run one SerpAPI search and return its JSON body; raise EngineError if it failed.

    Failure is judged by the HTTP status, as serpapi.com/api-status-and-error-codes documents:
    any status but 200 (401: the key was rejected, 429: no searches left or the hourly limit
    reached, 5xx: SerpAPI's side), a body that is not a JSON object, or a 200 body whose
    search_metadata.status is "Error". A 200 body with status "Success" can still carry an
    ``error`` string, e.g. "Google hasn't returned any results for this query.": that search
    worked and found nothing, so its body is returned.
    """
    engine, query = params["engine"], params["q"]
    # GoogleSearch cannot take a timeout, and get_dict() would read any error body as results.
    client = SerpApiClient({**params, "output": "json"}, timeout=SERPAPI_TIMEOUT)
    try:
        response = client.get_response()
    except requests.RequestException as e:
        # The request URL carries the API key: log the error type and host only.
        url = getattr(e.request, "url", None) or ""
        logger.error(
            "SerpAPI %s search for %r failed: %s (host %s)",
            engine,
            query,
            type(e).__name__,
            urlsplit(url).hostname,
        )
        raise EngineError(f"request failed ({type(e).__name__})") from None

    try:
        body = response.json()
    except ValueError:  # e.g. a proxy's HTML error page; its text is never used
        body = None
    error = _serpapi_error(body)
    if response.status_code != 200:
        raise EngineError(_with_error(_http_status_error(response.status_code), error))
    if not isinstance(body, dict):
        raise EngineError("HTTP 200, the response is not a JSON object")
    metadata = body.get("search_metadata")
    if isinstance(metadata, dict) and metadata.get("status") == "Error":
        raise EngineError(_with_error("HTTP 200, search status Error", error))
    if error:
        information = body.get("search_information")
        state = information.get("shopping_results_state") if isinstance(information, dict) else None
        logger.info(
            "SerpAPI %s search for %r found no results: %r (shopping_results_state %r)",
            engine,
            query,
            error,
            state,
        )
    return body


def _serpapi_error(body: object) -> str | None:
    """SerpAPI's ``error`` string in a JSON body, cut to _MAX_ERROR_LENGTH characters."""
    error = body.get("error") if isinstance(body, dict) else None
    return error[:_MAX_ERROR_LENGTH] if isinstance(error, str) and error else None


def _http_status_error(status: int) -> str:
    match status:
        case 401:
            return "HTTP 401, SerpAPI rejected the API key"
        case 429:
            return "HTTP 429, SerpAPI searches used up or hourly limit reached"
        case _ if status >= 500:
            return f"HTTP {status}, server error, try again later"
        case _:
            return f"HTTP {status}, SerpAPI refused the request"


def _with_error(reason: str, error: str | None) -> str:
    return f"{reason}: {error}" if error else reason


class SearchEngine(ABC):
    """Interface that every search engine must implement.

    Each engine takes a search query and returns a list of SearchResult objects. An engine that
    cannot search (no API key, a failed request, a SerpAPI error) raises EngineError, so an
    empty list always means that the search worked and found no usable results.
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
            Returns an empty list if the search yields no usable results.

        Raises:
            EngineError: The engine could not search.
        """

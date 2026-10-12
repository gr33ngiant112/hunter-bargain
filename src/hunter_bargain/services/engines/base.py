"""Abstract base class for all search engine implementations, and the shared SerpAPI calls."""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# A US dollar price as SerpAPI shows it for US results: "$" and then the amount, e.g. "$1,299.00".
_USD_PRICE = re.compile(r"\$\d")

# A plain dollar amount: "$", whole dollars with or without comma thousands separators, and at
# most two decimals, e.g. "$1,299.00" or "$29.99". Comma decimals ("$1.299,00", "$12,99",
# "$0,299") and a dot between thousands ("$1.299") do not match: read the US way, they are off
# by a factor of 100 or 1000.
_PLAIN_USD_AMOUNT = re.compile(r"\$((?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?)")

# A payment-plan price ends with the payment period, e.g. "$20.99/mo".
_INSTALLMENT_SUFFIXES = ("/mo", "/wk")

# Where SerpAPI's endpoints are, e.g. its search at /search. The tests point it at a local stub.
SERPAPI_BASE_URL = "https://serpapi.com"

# Seconds to wait for SerpAPI to connect, and then for each read: a stalled search must not hold
# the scheduler's job thread.
SERPAPI_TIMEOUT = 20

# SerpAPI's error string is third-party text: an engine error keeps at most this many characters.
_MAX_ERROR_LENGTH = 200

# A plan_renewal_date as the Account API gives it, e.g. "2026-11-12".
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


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
        rating: The item's rating out of 5 (``rating``), or None.
        reviews: The item's review count as text, e.g. "13,000" (Google Shopping
            sends a number) or "5K+" (Bing sends a string), or None.
        multiple_sources: True when Google Shopping says more than one seller has
            the item (``multiple_sources``).  Bing rows have no such field.
    """

    title: str
    price: float
    currency: str
    source: str
    url: str | None = None
    extensions: tuple[str, ...] | None = None
    merchant: str | None = None
    condition: str | None = None
    rating: float | None = None
    reviews: str | None = None
    multiple_sources: bool = False


def is_usd_price(price: object) -> bool:
    """Return True if a SerpAPI price string is in US dollars, e.g. "$1,299.00".

    Item targets are in USD and the requests set no country (gl), but a row can still be priced
    in another currency, e.g. "TRY 28,782.94". Any other string, or no string, is not USD. A
    plain "$" cannot tell other dollar currencies apart, so it counts as USD.
    """
    return isinstance(price, str) and _USD_PRICE.match(price.strip()) is not None


def parse_usd_amount(price: object) -> float | None:
    """Return the amount of a plain dollar price string, e.g. 1299.0 for "$1,299.00".

    The engines read a row's price string only when the row has no extracted_price. Any other
    string gives None, so the row is skipped rather than compared at a wrong amount: a comma
    decimal ("$1.299,00" read as 1.299), a monthly or "From" price, or another currency (#30).
    """
    if not isinstance(price, str):
        return None
    match = _PLAIN_USD_AMOUNT.fullmatch(price.strip())
    return float(match.group(1).replace(",", "")) if match else None


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
    try:
        response = httpx.get(
            f"{SERPAPI_BASE_URL}/search",
            params={**params, "output": "json"},
            timeout=SERPAPI_TIMEOUT,
        )
    except httpx.HTTPError as e:
        # The request URL carries the API key: log the error type and host only.
        logger.error(
            "SerpAPI %s search for %r failed: %s (host %s)",
            engine,
            query,
            type(e).__name__,
            urlsplit(SERPAPI_BASE_URL).hostname,
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


@dataclass(frozen=True)
class SerpApiAccount:
    """The five fields the app reads from SerpAPI's Account API; None where the answer has none.

    The answer also holds the API key and the account's email address. Neither is read, so
    nothing that keeps or logs a SerpApiAccount can leak them.
    """

    total_searches_left: int | None
    this_month_usage: int | None
    searches_per_month: int | None
    # The plan's next renewal, e.g. "2026-11-12"; None without a monthly plan.
    plan_renewal_date: str | None
    account_rate_limit_per_hour: int | None


def fetch_serpapi_account(api_key: str) -> SerpApiAccount:
    """Read the account's searches from SerpAPI's Account API, which costs no search.

    Raises EngineError, built as fetch_serpapi builds it, if the request fails or SerpAPI answers
    with an error. The request URL holds the key, and the answer the key and the account's email
    address, so neither is logged: only SerpApiAccount's fields are read from the answer.
    """
    try:
        response = httpx.get(
            f"{SERPAPI_BASE_URL}/account.json",
            params={"api_key": api_key},
            timeout=SERPAPI_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise EngineError(f"request failed ({type(e).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200:
        error = _serpapi_error(body)
        raise EngineError(_with_error(_http_status_error(response.status_code), error))
    if not isinstance(body, dict):
        raise EngineError("HTTP 200, the response is not a JSON object")
    renewal = body.get("plan_renewal_date")
    if not (isinstance(renewal, str) and _DATE.fullmatch(renewal)):
        renewal = None  # third-party text that goes into a log line and an email
    return SerpApiAccount(
        total_searches_left=_count(body.get("total_searches_left")),
        this_month_usage=_count(body.get("this_month_usage")),
        searches_per_month=_count(body.get("searches_per_month")),
        plan_renewal_date=renewal,
        account_rate_limit_per_hour=_count(body.get("account_rate_limit_per_hour")),
    )


def _count(value: object) -> int | None:
    """A count from SerpAPI's JSON: an integer of 0 or more, else None (true is not a count)."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


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

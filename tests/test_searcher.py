"""Tests for the search engine implementations and orchestrator."""

import datetime as dt
import json
import logging
import threading
import time
from typing import Any, NamedTuple
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from hunter_bargain.config import settings as app_settings
from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.schemas import PriceCheckResult
from hunter_bargain.services.engines import base
from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.engines.serpapi_shopping import SerpApiShoppingEngine
from hunter_bargain.services.searcher import (
    _build_query,
    _has_accessory_extension,
    _is_relevant,
    run_price_check,
)

# shopping_results rows from SerpAPI's documented examples, fetched 2026-10-05, trimmed to the
# fields the engines read (and Google's position). The lira row also keeps its alternative_price,
# which the engines do not use. Google Shopping rows have no "link" field.

# Google Shopping, https://serpapi.com/shopping-results: "Results for: q: Coffee", rows 1 to 3.
GOOGLE_FOLGERS = {
    "position": 1,
    "title": "Folgers Classic Roast Ground Coffee",
    "product_link": "https://www.google.com/shopping/product/16914877625280977865?gl=us",
    "source": "Walmart",
    "price": "$5.88",
    "extracted_price": 5.88,
    "extensions": ["Nearby, 3 mi"],
}
GOOGLE_BUSTELO = {
    "position": 2,
    "title": "Bustelo Coffee Espresso",
    "product_link": "https://www.google.com/shopping/product/1780156926901513261?gl=us",
    "source": "Walgreens.com",
    "price": "$4.99",
    "extracted_price": 4.99,
    "extensions": ["Nearby, 11 mi", "37% OFF"],
}
GOOGLE_MAXWELL_HOUSE = {
    "position": 3,
    "title": "Maxwell House Original Roast Ground Coffee",
    "product_link": "https://www.google.com/shopping/product/13722422005171491253?gl=us",
    "source": "BJ's Wholesale Club",
    "price": "$18.49",
    "extracted_price": 18.49,
    "extensions": ["Nearby, 6 mi"],
}
# Same page, "Results for: q: iphone": a monthly-installment price.
GOOGLE_MONTHLY = {
    "position": 5,
    "title": "Restored Apple iPhone 15 Pro Max",
    "product_link": "https://www.google.com/shopping/product/7869457262630415269?gl=us",
    "source": "AT&T",
    "price": "$20.99/mo",
    "extracted_price": 20.99,
    "installment": {"price": "$20.99/mo", "extracted_price": 20.99, "period": 36},
}
# Same page, "Results for: q: Apple - iPhone 14 128GB - Starlight": a gl=tr search, priced in
# Turkish lira, with the price in euros in alternative_price.
GOOGLE_LIRA = {
    "position": 6,
    "title": "Apple iPhone 14 128GB Starlight",
    "product_link": "https://www.google.ad/shopping/product/1?gl=tr&prds=pid:11787635087997176317",
    "source": "Dakauf",
    "price": "TRY 28,782.94",
    "extracted_price": 28782.94,
    "alternative_price": {"price": "€608", "extracted_price": 608, "currency": "€"},
}
# The same page's JSON structure overview documents second_hand_condition as "Description of
# condition when the product is second hand (Ex: 'used', or 'refurbished')". No example row on the
# page carries one, so the tests add it to GOOGLE_FOLGERS.
SECOND_HAND_CONDITIONS = ["used", "refurbished"]

# Bing Shopping, https://serpapi.com/bing-shopping-api: "Example results for q: jacket", rows 1
# and 2. link is a bing.com/ck/a redirect; external_link is the seller's own page.
BING_PUFFER_JACKET = {
    "link": (
        "https://www.bing.com/ck/a?!&&p=bf4baaa5a030c160JmltdHM9MTcyMjU1NjgwMCZpZ3VpZD0xY"
        "Tk5ZGU1NC0xYTk0LTY2N2EtMGYwNC1jYTliMWI2NTY3ODEmaW5zaWQ9NTM1NQ&ptn=3&ver=2&hsh=3&"
        "fclid=1a99de54-1a94-667a-0f04-ca9b1b656781&u=a1L3Nob3AvcHJvZHVjdHBhZ2U_cT1qYWNrZ"
        "XQmZmlsdGVycz1zY2VuYXJpbyUzYSUyMjE3JTIyK2dUeXBlJTNhJTIyMTIlMjIrZ0lkJTNhJTIyMTU1N"
        "zU1Nzc0NDUzJTIyK2dJZEhhc2glM2ElMjIwJTIyK2dHbG9iYWxPZmZlcklkcyUzYSUyMjE1NTc1NTc3N"
        "DQ1MyUyMitBdWNDb250ZXh0R3VpZCUzYSUyMjAlMjIrR3JvdXBFbnRpdHlJZCUzYSUyMjE1NTc1NTc3N"
        "DQ1MyUyMitOb25TcG9uc29yZWRPZmZlciUzYSUyMlRydWUlMjImcHJvZHVjdHBhZ2U9dHJ1ZSZGT1JNP"
        "VNIUFBEUCZicm93c2U9dHJ1ZQ&ntb=1"
    ),
    "title": "Women's Packable Puffer Jacket - Blue - Medium - The Vermont Country Store",
    "seller": "Vermont Country Store",
    "price": "$99.95",
    "extracted_price": 99.95,
    "external_link": (
        "https://www.vermontcountrystore.com/womens-packable-puffer-jacket/product/90087?"
        "variantColorName=color&variantColorValue=Navy&variantSizeName=size&variantSizeVa"
        "lue=Medium+%2810-12%29"
    ),
}
BING_BI_SWING_JACKET = {
    "link": (
        "https://www.bing.com/ck/a?!&&p=ae764520149a27daJmltdHM9MTcyMjU1NjgwMCZpZ3VpZD0xY"
        "Tk5ZGU1NC0xYTk0LTY2N2EtMGYwNC1jYTliMWI2NTY3ODEmaW5zaWQ9NTYwNA&ptn=3&ver=2&hsh=3&"
        "fclid=1a99de54-1a94-667a-0f04-ca9b1b656781&u=a1L3Nob3AvcHJvZHVjdHBhZ2U_cT1qYWNrZ"
        "XQmZmlsdGVycz1zY2VuYXJpbyUzYSUyMjE3JTIyK2dUeXBlJTNhJTIyMTIlMjIrZ0lkJTNhJTIyMTk5M"
        "zY2ODI3NTYxJTIyK2dJZEhhc2glM2ElMjIwJTIyK2dHbG9iYWxPZmZlcklkcyUzYSUyMjE5OTM2NjgyN"
        "zU2MSUyMitBdWNDb250ZXh0R3VpZCUzYSUyMjAlMjIrR3JvdXBFbnRpdHlJZCUzYSUyMjE5OTM2NjgyN"
        "zU2MSUyMitOb25TcG9uc29yZWRPZmZlciUzYSUyMlRydWUlMjImcHJvZHVjdHBhZ2U9dHJ1ZSZGT1JNP"
        "VNIUFBEUCZicm93c2U9dHJ1ZQ&ntb=1"
    ),
    "title": "Ralph Lauren Bi-Swing Jacket - Size M In Cooper Brown",
    "seller": "Ralph Lauren",
    "price": "$168.00",
    "extracted_price": 168.0,
    "external_link": (
        "https://www.ralphlauren.com/men-clothing-jackets-coats-vests/bi-swing-jacket/007"
        "7527919.html?utm_source=CSE&utm_medium=BingPLA_0_0_0"
    ),
}
# Same page, "Example results for q: iphone".
BING_IPHONE_13 = {
    "link": (
        "https://www.bing.com/shop/productpage?q=iphone&filters=scenario%3a%2217%22+gType"
        "%3a%2212%22+gId%3a%22136779445735%22+gIdHash%3a%220%22+gGlobalOfferIds%3a%221367"
        "79445735%22+AucContextGuid%3a%220%22+GroupEntityId%3a%22136779445735%22+NonSpons"
        "oredOffer%3a%22True%22&productpage=true&FORM=SHPPDP&browse=true"
    ),
    "title": "Apple - iPhone 13 5G 128GB (Unlocked) - Midnight",
    "seller": "Best Buy",
    "price": "$579.99",
    "extracted_price": 579.99,
    "external_link": (
        "https://www.bestbuy.com/site/apple-iphone-13-5g-128gb-unlocked-midnight/6417788."
        "p?skuId=6417788&contractId=unactivated&utm_source=feed"
    ),
}
# Bing's installments object, with the example values from the page's JSON structure overview.
# No example row on the page carries one, so the tests add it to BING_IPHONE_13.
BING_INSTALLMENTS = {"price": "$0", "text": "now", "installments": "$41.67/mo", "duration": "24"}

# extracted_price values that are not a usable price (#8). Python's json module reads NaN and
# Infinity in a response as floats, and a long integer literal as an int too large for a float.
BAD_EXTRACTED_PRICES = [
    pytest.param("5.88", id="string"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(True, id="bool"),
    pytest.param(0, id="zero"),
    pytest.param(-5.88, id="negative"),
    pytest.param(10**400, id="int-too-large-for-float"),
]
# Dollar price strings that give no usable amount, for rows without extracted_price: zero, and
# strings that are not a plain dollar amount, which read as 1.299 and 1299.0 before #30.
BAD_PRICE_STRINGS = [
    pytest.param("$0.00", id="zero"),
    pytest.param("$1e999", id="infinity"),
    pytest.param("$1.299,00", id="comma-decimal"),
    pytest.param("$12,99", id="comma-cents"),
]

# SerpAPI's documented error responses, https://serpapi.com/api-status-and-error-codes (fetched
# 2026-10-05), with "..." parts of the examples left out.
INVALID_KEY_BODY = {
    "error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"
}
NO_SEARCHES_LEFT_BODY = {"error": "Your account has run out of searches."}
MISSING_QUERY_BODY = {"error": "Missing query `q` parameter."}
SEARCH_ERROR_BODY = {  # served with HTTP 503 on the page
    "search_metadata": {
        "id": "64c32cfeb68f8bc186aaa013",
        "status": "Error",
        "json_endpoint": "https://serpapi.com/searches/d46c933a4a843289/64c32cfeb68f8bc186aaa013.json",
    },
    "error": "We couldn't get valid results for this search. Please try again later.",
}
# Same page, "Status: Success with empty organic results in Google Search API" (HTTP 200). The page
# says the *_results_state key is named for each engine's results; Google Shopping's docs name it
# shopping_results_state, so that key replaces the example's organic_results_state.
NO_RESULTS_BODY = {
    "search_metadata": {
        "id": "6540ac26b68f8b2910019dd5",
        "status": "Success",
        "json_endpoint": "https://serpapi.com/searches/d1add70d119063c8/6540ac26b68f8b2910019dd5.json",
    },
    "search_information": {"shopping_results_state": "Fully empty"},
    "error": "Google hasn't returned any results for this query.",
}

ENGINE_LOGGER = "hunter_bargain.services.engines.serpapi_shopping"


def _without(row: dict, *fields: str) -> dict:
    """A documented row with some of its fields left out."""
    return {key: value for key, value in row.items() if key not in fields}


class EngineSearches(NamedTuple):
    """One engine's searches, answered by the local stub SerpAPI (stub_serpapi in conftest.py)."""

    stub: Any
    engine: str  # SerpAPI's engine parameter


def _serve(searches: EngineSearches, *rows: dict) -> None:
    """Make the stub SerpAPI answer the engine's searches with these shopping_results rows."""
    searches.stub.bodies[searches.engine] = json.dumps({"shopping_results": list(rows)})


def _skip_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "skipped" in r.getMessage()]


@pytest.fixture
def google_search(serpapi_key, stub_serpapi):
    """Google Shopping searches through the real client, answered by the local stub SerpAPI."""
    searches = EngineSearches(stub_serpapi, "google_shopping")
    _serve(searches)
    return searches


@pytest.fixture
def bing_search(serpapi_key, stub_serpapi):
    """Bing Shopping searches through the real client, answered by the local stub SerpAPI."""
    searches = EngineSearches(stub_serpapi, "bing_shopping")
    _serve(searches)
    return searches


@pytest.fixture
def serpapi_key(monkeypatch):
    """A fake key in the app's settings, for engines that call the local stub SerpAPI."""
    monkeypatch.setattr(app_settings, "serpapi_key", SecretStr("test-key"))


@pytest.fixture
def engines(google_search, bing_search):
    """Both real engines in the searcher's registry, their searches answered by the stub."""
    with patch(
        "hunter_bargain.services.searcher._ENGINES",
        [SerpApiShoppingEngine("google_shopping"), SerpApiShoppingEngine("bing_shopping")],
    ):
        yield google_search, bing_search


@pytest.fixture
def send_alert():
    """send_price_alert as the searcher calls it, patched: no email is sent."""
    with patch("hunter_bargain.services.searcher.send_price_alert") as send:
        yield send


def _tracked_item(db_session, name: str, target_price: float) -> Item:
    item = Item(name=name, notify_email="n@x.com", target_price=target_price)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


class TestParsePrice:
    """The price string parser both engines use for a row without extracted_price."""

    def test_standard_price(self):
        assert base.parse_usd_amount("$1,299.00") == 1299.00

    def test_no_comma(self):
        assert base.parse_usd_amount("$29.99") == 29.99

    def test_empty_string(self):
        assert base.parse_usd_amount("") is None

    def test_none(self):
        assert base.parse_usd_amount(None) is None

    def test_invalid(self):
        assert base.parse_usd_amount("free") is None

    @pytest.mark.parametrize("price_str", ["$20.99/mo", "From $999"])
    def test_monthly_or_from_price_is_not_a_number(self, price_str):
        """The fallback parser gives no number for a monthly price or a "From" price (#35)."""
        assert base.parse_usd_amount(price_str) is None

    @pytest.mark.parametrize(
        ("price_str", "expected"),
        [
            ("$1,299", 1299.0),
            ("$1299", 1299.0),
            ("$1,234,567.89", 1234567.89),
            ("$100,000", 100000.0),
            ("$29.9", 29.9),
            ("$0.99", 0.99),
            (" $5.88 ", 5.88),
        ],
    )
    def test_plain_dollar_amount(self, price_str, expected):
        assert base.parse_usd_amount(price_str) == expected

    @pytest.mark.parametrize(
        "price_str",
        [
            "1.299,00",  # a comma decimal, read as 1.299 before #30
            "$1.299,00",
            "$12,99",  # read as 1299.0 before #30
            "$1,29",
            "$1.299",  # 1,299 dollars with a dot between thousands, or 1.299 dollars
            "$1,2999.00",  # a thousands group of four digits, read as 12999.0 before #30
            "$0,299",  # a comma decimal again: thousands groups can't follow a 0, read as 299.0
            "£1,299",
            "US$1,299.00",
            "$1e999",
            "$\u0661\u0662\u0663",  # Arabic-Indic digits, which float() would read as 123
            "$",
            "$.99",
        ],
    )
    def test_not_a_plain_dollar_amount(self, price_str):
        """Any other string gives None, so the row is skipped rather than priced wrong (#30)."""
        assert base.parse_usd_amount(price_str) is None


class TestPriceChecks:
    """The checks in engines/base.py that decide whether a row's price can be used (#35)."""

    @pytest.mark.parametrize(
        ("price", "expected"),
        [
            ("$5.88", True),
            # Bing's documented "Best Buy Canada" row (bestbuy.ca) is priced "$1,449.99", probably
            # in CAD, but "$" alone cannot tell, so it counts as USD: a known limitation.
            ("$1,449.99", True),
            ("$20.99/mo", True),  # in dollars, but is_installment_offer rejects it
            ("TRY 28,782.94", False),
            ("€608", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_usd_price(self, price, expected):
        assert base.is_usd_price(price) is expected

    @pytest.mark.parametrize(
        ("row", "key", "expected"),
        [
            (GOOGLE_MONTHLY, "installment", True),
            # Variants of the documented row that keep only one sign of a payment plan.
            (_without(GOOGLE_MONTHLY, "installment"), "installment", True),
            ({**GOOGLE_MONTHLY, "price": "$20.99"}, "installment", True),
            (
                {**_without(GOOGLE_MONTHLY, "installment"), "price": "$20.99/MO"},
                "installment",
                True,
            ),
            (
                {**_without(GOOGLE_MONTHLY, "installment"), "price": "$20.99/wk"},
                "installment",
                True,
            ),
            ({**BING_IPHONE_13, "installments": BING_INSTALLMENTS}, "installments", True),
            (GOOGLE_FOLGERS, "installment", False),
            (BING_IPHONE_13, "installments", False),
        ],
        ids=[
            "google-documented",
            "google-mo-suffix-only",
            "google-installment-object-only",
            "google-MO-uppercase",
            "google-wk-suffix",
            "bing-installments-object",
            "google-full-price",
            "bing-full-price",
        ],
    )
    def test_is_installment_offer(self, row, key, expected):
        assert base.is_installment_offer(row, key) is expected


class TestBuildQuery:
    """Tests for search query construction."""

    def test_name_only(self):
        item = MagicMock(spec=Item)
        item.name = "MacBook Air"
        item.keywords = None
        assert _build_query(item) == "MacBook Air"

    def test_name_with_keywords(self):
        item = MagicMock(spec=Item)
        item.name = "MacBook Air"
        item.keywords = "M3 16GB midnight"
        assert _build_query(item) == "MacBook Air M3 16GB midnight"

    def test_comma_separated_keywords(self):
        item = MagicMock(spec=Item)
        item.name = "Google Pixel 10 Pro XL"
        item.keywords = "google,pixel,10,pro,xl"
        assert _build_query(item) == "Google Pixel 10 Pro XL google pixel 10 pro xl"

    def test_comma_keywords_with_spaces(self):
        item = MagicMock(spec=Item)
        item.name = "iPhone 16"
        item.keywords = "unlocked, 256GB, black"
        assert _build_query(item) == "iPhone 16 unlocked 256GB black"


class TestGoogleShoppingEngine:
    """Tests for the Google Shopping engine, its searches answered by the local stub SerpAPI."""

    @patch("hunter_bargain.services.engines.serpapi_shopping.settings")
    def test_missing_api_key_is_an_engine_error(self, mock_settings):
        """Without a key no search runs: that is reported, not read as zero results (#8)."""
        mock_settings.serpapi_key = SecretStr("")

        with pytest.raises(base.EngineError, match="^SERPAPI_KEY is not set$"):
            SerpApiShoppingEngine("google_shopping").search("iPhone")

    def test_returns_sorted_results(self, google_search):
        _serve(google_search, GOOGLE_FOLGERS, GOOGLE_BUSTELO, GOOGLE_MAXWELL_HOUSE)

        results = SerpApiShoppingEngine("google_shopping").search("coffee")

        assert [(r.title, r.price) for r in results] == [
            ("Bustelo Coffee Espresso", 4.99),
            ("Folgers Classic Roast Ground Coffee", 5.88),
            ("Maxwell House Original Roast Ground Coffee", 18.49),
        ]
        assert {(r.source, r.currency) for r in results} == {("google_shopping", "USD")}

    def test_url_is_product_link_and_merchant_is_source(self, google_search):
        """A row has product_link and no link: the result links there and names the seller (#34)."""
        _serve(google_search, GOOGLE_FOLGERS)

        [result] = SerpApiShoppingEngine("google_shopping").search("coffee")

        assert result.url == "https://www.google.com/shopping/product/16914877625280977865?gl=us"
        assert result.merchant == "Walmart"
        assert result.source == "google_shopping"
        assert result.extensions == ("Nearby, 3 mi",)

    @pytest.mark.parametrize(
        "row",
        [
            GOOGLE_MONTHLY,
            # Variants of the documented row that keep only one sign of a payment plan.
            _without(GOOGLE_MONTHLY, "installment"),
            {**GOOGLE_MONTHLY, "price": "$20.99"},
        ],
        ids=["documented", "mo-suffix-only", "installment-object-only"],
    )
    def test_skips_monthly_price(self, google_search, caplog, row):
        _serve(google_search, row, GOOGLE_FOLGERS)

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("google_shopping").search("iphone")

        assert [r.title for r in results] == ["Folgers Classic Roast Ground Coffee"]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "payment-plan offer 'Restored Apple iPhone 15 Pro Max'" in record.getMessage()

    def test_skips_foreign_currency_price(self, google_search, caplog):
        """The lira row is not taken as 28782.94 USD, and its euro alternative_price is not used."""
        _serve(google_search, GOOGLE_LIRA, GOOGLE_FOLGERS)

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("google_shopping").search("iphone")

        assert [r.title for r in results] == ["Folgers Classic Roast Ground Coffee"]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "'Apple iPhone 14 128GB Starlight', no USD price (price 'TRY 28,782.94')" in (
            record.getMessage()
        )

    def test_skips_row_without_price_string(self, google_search):
        """Without a price string the currency is unknown, so extracted_price is not used."""
        _serve(google_search, _without(GOOGLE_FOLGERS, "price"))

        assert SerpApiShoppingEngine("google_shopping").search("coffee") == []

    def test_parses_price_string_without_extracted_price(self, google_search):
        _serve(google_search, _without(GOOGLE_FOLGERS, "extracted_price"))

        [result] = SerpApiShoppingEngine("google_shopping").search("coffee")

        assert (result.price, result.currency) == (5.88, "USD")

    @pytest.mark.parametrize("condition", SECOND_HAND_CONDITIONS)
    def test_second_hand_condition_is_the_result_condition(self, google_search, condition):
        """A row's second_hand_condition is the result's condition; without one, None (#3)."""
        row = {**GOOGLE_FOLGERS, "second_hand_condition": condition}
        _serve(google_search, row, GOOGLE_BUSTELO)

        results = SerpApiShoppingEngine("google_shopping").search("coffee")

        assert [(r.title, r.condition) for r in results] == [
            ("Bustelo Coffee Espresso", None),
            ("Folgers Classic Roast Ground Coffee", condition),
        ]

    @pytest.mark.parametrize("extracted_price", BAD_EXTRACTED_PRICES)
    def test_unusable_extracted_price_skips_only_that_row(
        self, google_search, caplog, extracted_price
    ):
        """One bad row is skipped; the engine still returns the others (#8)."""
        _serve(
            google_search, {**GOOGLE_FOLGERS, "extracted_price": extracted_price}, GOOGLE_BUSTELO
        )

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("google_shopping").search("coffee")

        assert [(r.title, r.price) for r in results] == [("Bustelo Coffee Espresso", 4.99)]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "skipped 'Folgers Classic Roast Ground Coffee', no usable price" in (
            record.getMessage()
        )

    @pytest.mark.parametrize("price", BAD_PRICE_STRINGS)
    def test_unusable_parsed_price_skips_only_that_row(self, google_search, caplog, price):
        row = {**_without(GOOGLE_FOLGERS, "extracted_price"), "price": price}
        _serve(google_search, row, GOOGLE_BUSTELO)

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("google_shopping").search("coffee")

        assert [(r.title, r.price) for r in results] == [("Bustelo Coffee Espresso", 4.99)]
        [record] = _skip_records(caplog)
        assert "skipped 'Folgers Classic Roast Ground Coffee', no usable price" in (
            record.getMessage()
        )


class TestBingShoppingEngine:
    """Tests for the Bing Shopping engine, its searches answered by the local stub SerpAPI."""

    def test_url_is_external_link_and_merchant_is_seller(self, bing_search):
        """The seller's own page, not the bing.com/ck redirect in link (#34)."""
        _serve(bing_search, BING_BI_SWING_JACKET, BING_PUFFER_JACKET)

        results = SerpApiShoppingEngine("bing_shopping").search("jacket")

        assert [(r.price, r.merchant, r.url) for r in results] == [
            (99.95, "Vermont Country Store", BING_PUFFER_JACKET["external_link"]),
            (168.0, "Ralph Lauren", BING_BI_SWING_JACKET["external_link"]),
        ]
        assert not any(r.url.startswith("https://www.bing.com/") for r in results)
        assert {(r.source, r.currency) for r in results} == {("bing_shopping", "USD")}

    def test_falls_back_to_link_without_external_link(self, bing_search):
        _serve(bing_search, _without(BING_PUFFER_JACKET, "external_link"))

        [result] = SerpApiShoppingEngine("bing_shopping").search("jacket")

        assert result.url == BING_PUFFER_JACKET["link"]
        assert result.merchant == "Vermont Country Store"

    def test_skips_installments_offer(self, bing_search, caplog):
        """A row with an installments object is skipped, like Google's monthly prices (#35)."""
        _serve(
            bing_search, {**BING_IPHONE_13, "installments": BING_INSTALLMENTS}, BING_PUFFER_JACKET
        )

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("bing_shopping").search("iphone")

        assert [r.title for r in results] == [BING_PUFFER_JACKET["title"]]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "payment-plan offer 'Apple - iPhone 13 5G 128GB (Unlocked) - Midnight'" in (
            record.getMessage()
        )

    def test_skips_row_without_price_string(self, bing_search, caplog):
        """Without a price string the currency is unknown, so extracted_price is not used."""
        _serve(bing_search, _without(BING_PUFFER_JACKET, "price"))

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            assert SerpApiShoppingEngine("bing_shopping").search("jacket") == []

        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "no USD price (price None)" in record.getMessage()

    def test_parses_price_string_without_extracted_price(self, bing_search):
        _serve(bing_search, _without(BING_PUFFER_JACKET, "extracted_price"))

        [result] = SerpApiShoppingEngine("bing_shopping").search("jacket")

        assert (result.price, result.currency) == (99.95, "USD")

    @pytest.mark.parametrize("extracted_price", BAD_EXTRACTED_PRICES)
    def test_unusable_extracted_price_skips_only_that_row(
        self, bing_search, caplog, extracted_price
    ):
        """One bad row is skipped; the engine still returns the others (#8)."""
        row = {**BING_PUFFER_JACKET, "extracted_price": extracted_price}
        _serve(bing_search, row, BING_BI_SWING_JACKET)

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("bing_shopping").search("jacket")

        assert [(r.title, r.price) for r in results] == [(BING_BI_SWING_JACKET["title"], 168.0)]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert f"skipped {BING_PUFFER_JACKET['title']!r}, no usable price" in record.getMessage()

    @pytest.mark.parametrize("price", BAD_PRICE_STRINGS)
    def test_unusable_parsed_price_skips_only_that_row(self, bing_search, caplog, price):
        row = {**_without(BING_PUFFER_JACKET, "extracted_price"), "price": price}
        _serve(bing_search, row, BING_BI_SWING_JACKET)

        with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
            results = SerpApiShoppingEngine("bing_shopping").search("jacket")

        assert [(r.title, r.price) for r in results] == [(BING_BI_SWING_JACKET["title"], 168.0)]
        [record] = _skip_records(caplog)
        assert f"skipped {BING_PUFFER_JACKET['title']!r}, no usable price" in record.getMessage()


@pytest.mark.parametrize(
    "engine_name", ["google_shopping", "bing_shopping"], ids=["google", "bing"]
)
class TestSerpApiResponses:
    """Responses from a local stub SerpAPI, through the real client and httpx (#8)."""

    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            pytest.param(
                401,
                INVALID_KEY_BODY,
                "HTTP 401, SerpAPI rejected the API key: Invalid API key. Your API key should be"
                " here: https://serpapi.com/manage-api-key",
                id="401-invalid-key",
            ),
            pytest.param(
                429,
                NO_SEARCHES_LEFT_BODY,
                "HTTP 429, SerpAPI searches used up or hourly limit reached: Your account has run"
                " out of searches.",
                id="429-no-searches-left",
            ),
            pytest.param(
                400,
                MISSING_QUERY_BODY,
                "HTTP 400, SerpAPI refused the request: Missing query `q` parameter.",
                id="400-missing-query",
            ),
            pytest.param(
                503,
                SEARCH_ERROR_BODY,
                "HTTP 503, server error, try again later: We couldn't get valid results for this"
                " search. Please try again later.",
                id="503-status-error",
            ),
            # Variant: the documented status Error body with HTTP 200.
            pytest.param(
                200,
                SEARCH_ERROR_BODY,
                "HTTP 200, search status Error: We couldn't get valid results for this search."
                " Please try again later.",
                id="200-status-error",
            ),
        ],
    )
    def test_error_response_is_logged_and_returned_as_engine_error(
        self,
        engine_name,
        status,
        body,
        expected,
        serpapi_key,
        stub_serpapi,
        db_session,
        send_alert,
        caplog,
    ):
        stub_serpapi.status, stub_serpapi.body = status, json.dumps(body)
        engine = SerpApiShoppingEngine(engine_name)
        item = _tracked_item(db_session, "Widget", 20.0)

        with patch("hunter_bargain.services.searcher._ENGINES", [engine]):
            result = run_price_check(item=item, db=db_session)

        assert result.engine_errors == [f"{engine.name}: {expected}"]
        assert (result.results_count, result.lowest_price) == (0, None)
        assert result.searches_used == 0  # #24: a failed search is an engine error instead
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [f"Engine {engine.name} failed for item {item.id}: {expected!r}"]

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (502, "HTTP 502, server error, try again later"),
            (200, "HTTP 200, the response is not a JSON object"),
        ],
        ids=["502", "200"],
    )
    def test_non_json_body_is_reported_by_status_only(
        self, engine_name, status, expected, serpapi_key, stub_serpapi, db_session, send_alert
    ):
        """An HTML page, e.g. from a proxy, is an engine error; its text is not used."""
        stub_serpapi.status, stub_serpapi.content_type = status, "text/html"
        stub_serpapi.body = "<html><body><h1>Bad Gateway</h1></body></html>"
        engine = SerpApiShoppingEngine(engine_name)
        item = _tracked_item(db_session, "Widget", 20.0)

        with patch("hunter_bargain.services.searcher._ENGINES", [engine]):
            result = run_price_check(item=item, db=db_session)

        assert result.engine_errors == [f"{engine.name}: {expected}"]

    def test_long_error_string_is_cut_to_200_characters(
        self, engine_name, serpapi_key, stub_serpapi, db_session, send_alert
    ):
        """SerpAPI's error string is third-party text: an engine error keeps 200 characters."""
        stub_serpapi.status, stub_serpapi.body = 401, json.dumps({"error": "x" * 1000})
        engine = SerpApiShoppingEngine(engine_name)
        item = _tracked_item(db_session, "Widget", 20.0)

        with patch("hunter_bargain.services.searcher._ENGINES", [engine]):
            result = run_price_check(item=item, db=db_session)

        assert result.engine_errors == [
            f"{engine.name}: HTTP 401, SerpAPI rejected the API key: {'x' * 200}"
        ]

    def test_success_with_error_string_is_zero_results_not_an_engine_error(
        self, engine_name, serpapi_key, stub_serpapi, db_session, send_alert, caplog
    ):
        """HTTP 200 with status Success and an error string: the search found nothing."""
        caplog.set_level(logging.INFO)
        stub_serpapi.body = json.dumps(NO_RESULTS_BODY)
        engine = SerpApiShoppingEngine(engine_name)
        item = _tracked_item(db_session, "Widget", 20.0)

        with patch("hunter_bargain.services.searcher._ENGINES", [engine]):
            result = run_price_check(item=item, db=db_session)

        assert (result.results_count, result.lowest_price, result.engine_errors) == (0, None, [])
        assert result.searches_used == 1  # #24: the search worked, and found nothing
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert (
            f"SerpAPI {engine.name} search for 'Widget' found no results:"
            ' "Google hasn\'t returned any results for this query."'
            " (shopping_results_state 'Fully empty')"
        ) in [r.getMessage() for r in caplog.records]

    def test_request_has_a_finite_timeout(
        self, engine_name, serpapi_key, stub_serpapi, monkeypatch
    ):
        """httpx gets 20 s to connect and for each read (and write, and pooled connection)."""
        timeouts = []
        handle_request = httpx.HTTPTransport.handle_request

        def handle_and_record(transport, request):
            timeouts.append(request.extensions["timeout"])
            return handle_request(transport, request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_and_record)

        results = SerpApiShoppingEngine(engine_name).search("widget")

        assert [r.price for r in results] == [9.99]  # the stub answered
        assert timeouts == [{"connect": 20, "read": 20, "write": 20, "pool": 20}]

    def test_request_sends_the_engine_query_key_and_output_only(
        self, engine_name, serpapi_key, stub_serpapi
    ):
        """No num (Google) or count (Bing): SerpAPI's docs give neither an effect (#22)."""
        SerpApiShoppingEngine(engine_name).search("widget")

        assert stub_serpapi.queries == [
            {"engine": [engine_name], "q": ["widget"], "api_key": ["test-key"], "output": ["json"]}
        ]


class TestIsRelevant:
    def test_exact_product_match(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL 256GB", price=999.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL") is True

    def test_accessory_filtered_out(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL Case Cover", price=12.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL") is False

    def test_screen_protector_filtered_out(self):
        result = SearchResult(
            title="Tempered Glass Screen Protector for Pixel 10 Pro",
            price=8.0,
            currency="USD",
            source="test",
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL") is False

    def test_low_word_overlap_filtered(self):
        result = SearchResult(
            title="Samsung Galaxy S25 Ultra", price=1199.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL") is False

    def test_accessory_term_allowed_when_in_item_name(self):
        result = SearchResult(
            title="iPhone 16 Pro Case - OtterBox", price=49.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "iPhone 16 Pro Case") is True

    def test_partial_match_above_threshold(self):
        result = SearchResult(
            title="Google Pixel 10 Pro - Unlocked 128GB", price=899.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL") is True

    def test_price_floor_filters_cheap_results(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL 256GB", price=29.99, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL", target_price=1000.0) is False

    def test_price_floor_keeps_reasonable_price(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL 256GB", price=899.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL", target_price=1000.0) is True

    def test_price_floor_skipped_when_no_target(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL 256GB", price=5.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL", target_price=None) is True

    def test_accessory_extension_filters_result(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL",
            price=15.0,
            currency="USD",
            source="test",
            extensions=("Phone Case", "Protective Case"),
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL", target_price=1000.0) is False

    def test_non_accessory_extension_passes(self):
        result = SearchResult(
            title="Google Pixel 10 Pro XL 256GB",
            price=999.0,
            currency="USD",
            source="test",
            extensions=("Smartphone", "5G", "OLED"),
        )
        assert _is_relevant(result, "Google Pixel 10 Pro XL", target_price=1000.0) is True

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Apple iPhone 15 Pro 256GB Natural Titanium", True),
            ("Apple iPhone 15 Pro 256 GB Natural Titanium", True),
            ("Apple iPhone 15 Pro 128GB Natural Titanium", False),
            ("Apple iPhone 15 Pro 512GB Natural Titanium", False),
            ("Apple iPhone 15 Pro Natural Titanium", False),
        ],
        ids=["256GB", "256-space-GB", "128GB", "512GB", "no-storage"],
    )
    def test_keyword_is_a_required_term(self, title, expected):
        """Keywords are required terms (#3): "256GB" or "256 GB" is the 256GB model."""
        result = SearchResult(title=title, price=999.0, currency="USD", source="test")
        # The name alone matches every one of these titles: the keyword decides.
        assert _is_relevant(result, "iPhone 15 Pro") is True
        assert _is_relevant(result, "iPhone 15 Pro", keywords="256GB") is expected

    @pytest.mark.parametrize(
        ("keywords", "expected"),
        [
            ("256GB, unlocked", True),
            ("unlocked,256GB", True),
            ("256GB, unlocked, blue titanium", False),
            ("256GB, verizon", False),
        ],
    )
    def test_every_comma_separated_keyword_term_is_required(self, keywords, expected):
        result = SearchResult(
            title="Apple iPhone 15 Pro 256GB Unlocked - Natural Titanium",
            price=999.0,
            currency="USD",
            source="test",
        )
        assert _is_relevant(result, "iPhone 15 Pro", keywords=keywords) is expected

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Apple iPhone 15 Pro 256GB - Natural Titanium", True),
            ("Apple iPhone 15 Pro 256GB - Natural Titanium (Renewed)", False),
            ("Apple iPhone 15 Pro 256GB Refurbished", False),
            ("Restored Apple iPhone 15 Pro 256GB", False),
            ("Apple iPhone 15 Pro 256GB Pre-Owned", False),
            ("Used Apple iPhone 15 Pro 256GB", False),
            ("Apple iPhone 15 Pro 256GB - Open Box", False),
            ("Apple iPhone 15 Pro 256GB For Parts or Not Working", False),
        ],
        ids=["new", "renewed", "refurbished", "restored", "pre-owned", "used", "open-box", "parts"],
    )
    def test_second_hand_title_is_rejected(self, title, expected):
        """Renewed, refurbished and other second-hand listings are rejected by default (#3)."""
        result = SearchResult(title=title, price=700.0, currency="USD", source="test")
        assert _is_relevant(result, "iPhone 15 Pro") is expected

    @pytest.mark.parametrize(
        ("condition", "expected"), [(None, True), *((c, False) for c in SECOND_HAND_CONDITIONS)]
    )
    def test_second_hand_condition_is_rejected(self, condition, expected):
        """A Google row's second_hand_condition rejects it, whatever its title says (#3)."""
        result = SearchResult(
            title="Folgers Classic Roast Ground Coffee",
            price=5.88,
            currency="USD",
            source="google_shopping",
            condition=condition,
        )
        assert _is_relevant(result, "Folgers Classic Roast Ground Coffee") is expected

    @pytest.mark.parametrize("item_name", ["The", "!!!", "A"])
    @pytest.mark.parametrize(
        "title", ["Phone Case", "Sony WH-1000XM5 Wireless Noise Canceling Headphones"]
    )
    def test_name_without_a_matchable_token_rejects(self, item_name, title):
        """Such a name used to accept every listing, accessories included (#3)."""
        result = SearchResult(title=title, price=50.0, currency="USD", source="test")
        assert _is_relevant(result, item_name) is False

    @pytest.mark.parametrize(
        ("item_name", "title"),
        [
            # Titles written for this test. The item's model code and another of the same shape:
            ("Sony WH-1000XM5", "Sony WH-1000XM4 and WH-1000XM5 Headphones Bundle"),
            ("Dyson V15 Detect", "Dyson V11 and V15 Detect Cordless Vacuum Bundle"),
            # The item's number, but not after the word it follows in the item's name:
            ("PlayStation 5", "PlayStation 4 Slim 1TB Console with 5 Games"),
        ],
    )
    def test_title_naming_another_model_is_rejected(self, item_name, title):
        """Another model's identifier rejects a title that also has the item's own (#3 step 2)."""
        result = SearchResult(title=title, price=300.0, currency="USD", source="test")
        assert _is_relevant(result, item_name) is False

    @pytest.mark.parametrize(
        "title",
        [
            # Titles written for this test: one screen size, three ways to write the inches.
            'Samsung 27" Odyssey G5 Gaming Monitor',
            "Samsung 27-Inch Odyssey G5 Gaming Monitor",
            "Samsung 27 in. Odyssey G5 Gaming Monitor",
        ],
        ids=["inch-mark", "hyphen-inch", "in"],
    )
    def test_screen_size_matches_however_the_title_writes_inches(self, title):
        """The name's "27 inch" is a required token, and '27"' in a title is the same size."""
        result = SearchResult(title=title, price=250.0, currency="USD", source="test")
        assert _is_relevant(result, "Samsung Odyssey G5 27 inch") is True

    def test_bracketed_text_between_a_word_and_its_number_is_skipped(self):
        """'(Slim)' between "PlayStation" and "5" does not move the 5 (a title written for this
        test); the bracket is left out of the check that the 5 follows "PlayStation"."""
        result = SearchResult(
            title="Sony PlayStation (Slim) 5 Console", price=450.0, currency="USD", source="test"
        )
        assert _is_relevant(result, "PlayStation 5") is True


class TestHasAccessoryExtension:
    def test_no_extensions(self):
        result = SearchResult(title="Widget", price=10.0, currency="USD", source="test")
        assert _has_accessory_extension(result) is False

    def test_accessory_extension_detected(self):
        result = SearchResult(
            title="Widget",
            price=10.0,
            currency="USD",
            source="test",
            extensions=("Screen Protector", "Glass"),
        )
        assert _has_accessory_extension(result) is True

    def test_non_accessory_extensions(self):
        result = SearchResult(
            title="Widget",
            price=10.0,
            currency="USD",
            source="test",
            extensions=("Smartphone", "128GB"),
        )
        assert _has_accessory_extension(result) is False


class TestRunPriceCheck:
    """Tests for the full price check orchestrator."""

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_aggregates_results_and_persists(self, mock_engines, mock_notify, db_session):
        """Verify that results from multiple engines are aggregated and saved."""
        mock_engine = MagicMock()
        mock_engine.name = "mock_engine"
        mock_engine.search.return_value = [
            SearchResult(
                title="Test Item - Great Deal",
                price=50.0,
                currency="USD",
                source="mock_engine",
                url="http://deal.com",
            ),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Test Item", notify_email="t@x.com", target_price=100.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        result = run_price_check(item=item, db=db_session)

        assert result.item_id == item.id
        assert result.lowest_price == 50.0
        assert result.results_count == 1

        records = db_session.query(PriceRecord).filter_by(item_id=item.id).all()
        assert len(records) == 1
        assert records[0].price == 50.0

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_notifies_when_target_met(self, mock_engines, mock_notify, db_session):
        """Verify notification fires when lowest price <= target."""
        mock_engine = MagicMock()
        mock_engine.name = "mock"
        mock_engine.search.return_value = [
            SearchResult(title="Tracked Widget Bargain", price=45.0, currency="USD", source="mock"),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Tracked Widget", notify_email="n@x.com", target_price=50.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        run_price_check(item=item, db=db_session)
        mock_notify.assert_called_once()

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_target_met_log_quotes_item_name(self, mock_engines, mock_notify, db_session, caplog):
        """A line break in an item name stays inside its log record (logged with %r)."""
        mock_engine = MagicMock()
        mock_engine.name = "mock"
        mock_engine.search.return_value = [
            SearchResult(
                title="Tracked Widget Fake Record", price=45.0, currency="USD", source="mock"
            ),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Tracked Widget\nFake record", notify_email="n@x.com", target_price=50.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        with caplog.at_level(logging.INFO, logger="hunter_bargain.services.searcher"):
            run_price_check(item=item, db=db_session)

        mock_notify.assert_called_once()
        target_met = [r.getMessage() for r in caplog.records if "Target met" in r.getMessage()]
        assert target_met == [
            f"Target met for item {item.id} ('Tracked Widget\\nFake record'): $45.00 <= $50.00"
        ]

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_no_notification_when_above_target(self, mock_engines, mock_notify, db_session):
        """Verify no notification when lowest price > target."""
        mock_engine = MagicMock()
        mock_engine.name = "mock"
        mock_engine.search.return_value = [
            SearchResult(
                title="Tracked Widget Premium", price=200.0, currency="USD", source="mock"
            ),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Tracked Widget", notify_email="n@x.com", target_price=100.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        run_price_check(item=item, db=db_session)
        mock_notify.assert_not_called()

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_filters_irrelevant_accessories(self, mock_engines, mock_notify, db_session):
        """Verify accessories are filtered out and the actual product is selected."""
        mock_engine = MagicMock()
        mock_engine.name = "mock"
        mock_engine.search.return_value = [
            SearchResult(
                title="Google Pixel 10 Pro XL Case", price=12.0, currency="USD", source="mock"
            ),
            SearchResult(
                title="Google Pixel 10 Pro XL 256GB Unlocked",
                price=999.0,
                currency="USD",
                source="mock",
            ),
            SearchResult(
                title="Screen Protector for Pixel 10", price=8.0, currency="USD", source="mock"
            ),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Google Pixel 10 Pro XL", notify_email="n@x.com", target_price=1100.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        result = run_price_check(item=item, db=db_session)

        assert result.results_count == 1
        assert result.lowest_price == 999.0

    def test_item_keywords_reach_the_relevance_check(self, db_session, send_alert):
        """The cheaper 128GB listing is not the lowest price of an item with keyword 256GB (#3)."""
        engine = MagicMock()
        engine.name = "mock"
        engine.search.return_value = [
            SearchResult(
                title="Apple iPhone 15 Pro 128GB", price=700.0, currency="USD", source="mock"
            ),
            SearchResult(
                title="Apple iPhone 15 Pro 256GB", price=850.0, currency="USD", source="mock"
            ),
        ]
        item = Item(
            name="iPhone 15 Pro", keywords="256GB", notify_email="n@x.com", target_price=900.0
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        with patch("hunter_bargain.services.searcher._ENGINES", [engine]):
            result = run_price_check(item=item, db=db_session)

        assert (result.results_count, result.lowest_price) == (1, 850.0)
        assert send_alert.call_args.kwargs["result"].title == "Apple iPhone 15 Pro 256GB"
        assert [r.title for r in db_session.query(PriceRecord)] == ["Apple iPhone 15 Pro 256GB"]

    def test_missing_key_is_an_engine_error_for_each_engine(
        self, db_session, send_alert, monkeypatch, caplog
    ):
        """Without SERPAPI_KEY the check lists both engines as failed, not as "no results"."""
        monkeypatch.setattr(app_settings, "serpapi_key", SecretStr(""))
        item = _tracked_item(db_session, "Widget", 20.0)
        engines = [SerpApiShoppingEngine("google_shopping"), SerpApiShoppingEngine("bing_shopping")]

        with patch("hunter_bargain.services.searcher._ENGINES", engines):
            result = run_price_check(item=item, db=db_session)

        assert result.engine_errors == [
            "google_shopping: SERPAPI_KEY is not set",
            "bing_shopping: SERPAPI_KEY is not set",
        ]
        assert result.searches_used == 0
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [
            f"Engine google_shopping failed for item {item.id}: 'SERPAPI_KEY is not set'",
            f"Engine bing_shopping failed for item {item.id}: 'SERPAPI_KEY is not set'",
        ]

    def test_unexpected_engine_exception_is_recorded_by_type_only(
        self, db_session, send_alert, caplog
    ):
        """A bug in one engine is logged with its traceback; the other engine's results count."""
        broken = MagicMock()
        broken.name = "broken"
        # Exception text can hold the request URL and its key (#5): only the type is returned.
        broken.search.side_effect = TypeError(
            "failed: https://serpapi.com/search?api_key=fake-key-in-exception-text"
        )
        working = MagicMock()
        working.name = "working"
        working.search.return_value = [
            SearchResult(title="Tracked Widget", price=45.0, currency="USD", source="working")
        ]
        item = _tracked_item(db_session, "Tracked Widget", 50.0)

        with patch("hunter_bargain.services.searcher._ENGINES", [broken, working]):
            result = run_price_check(item=item, db=db_session)

        assert result.engine_errors == ["broken: unexpected error (TypeError)"]
        assert (result.lowest_price, result.lowest_source) == (45.0, "working")
        assert result.searches_used == 1  # #24: the working engine's search only
        [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert record.getMessage() == f"Engine broken failed for item {item.id}"
        assert record.exc_info is not None  # logged with its traceback
        assert "fake-key-in-exception-text" not in caplog.text  # redacted by the log setup

    def test_engines_search_at_the_same_time(
        self, serpapi_key, stub_serpapi, send_alert, db_session
    ):
        """The stub answers neither search until both have reached it (#22). Run in turn, the
        first search would wait there until the barrier's timeout broke it, and both would fail."""
        stub_serpapi.barrier = threading.Barrier(2, timeout=5)
        item = _tracked_item(db_session, "Widget", 5.0)

        result = run_price_check(item=item, db=db_session)  # the app's own two engines

        assert sorted(q["engine"][0] for q in stub_serpapi.queries) == [
            "bing_shopping",
            "google_shopping",
        ]
        assert (result.engine_errors, result.searches_used) == ([], 2)
        # The stub answers both with a $9.99 Widget: the first engine's stays the lowest.
        assert result.lowest_source == "google_shopping"
        assert [r.source for r in result.records] == ["google_shopping", "bing_shopping"]

    def test_engine_order_is_kept_whichever_search_ends_first(self, db_session, send_alert):
        """The first engine's search ends after the second's, yet its result still comes first:
        of two equal prices it is the lowest, as when the engines ran in turn (#22)."""
        second_ended = threading.Event()

        def first_search(query: str) -> list[SearchResult]:
            assert second_ended.wait(timeout=5)
            time.sleep(0.1)  # for the second search to return
            return [
                SearchResult(title="Tracked Widget", price=45.0, currency="USD", source="first")
            ]

        def second_search(query: str) -> list[SearchResult]:
            second_ended.set()
            return [
                SearchResult(title="Tracked Widget", price=45.0, currency="USD", source="second")
            ]

        first, second = MagicMock(), MagicMock()
        first.name, first.search.side_effect = "first", first_search
        second.name, second.search.side_effect = "second", second_search
        item = _tracked_item(db_session, "Tracked Widget", 50.0)

        with patch("hunter_bargain.services.searcher._ENGINES", [first, second]):
            result = run_price_check(item=item, db=db_session)

        assert (result.engine_errors, result.lowest_source) == ([], "first")
        assert [r.source for r in result.records] == ["first", "second"]
        assert send_alert.call_args.kwargs["result"].source == "first"


class TestDocumentedRowsThroughPriceCheck:
    """Documented rows through the real engines, relevance filter, storage and alert decision."""

    @pytest.mark.parametrize("target", [20.99, 200.0, 209.0, 400.0, 900.0])
    def test_monthly_price_is_never_the_alert_price(self, engines, send_alert, db_session, target):
        """Below a $209.90 target the row clears the 10% floor; only the new check stops it."""
        _serve(engines[0], GOOGLE_MONTHLY)
        item = _tracked_item(db_session, "iPhone 15 Pro Max", target)

        result = run_price_check(item=item, db=db_session)

        send_alert.assert_not_called()
        assert result.lowest_price is None
        assert db_session.query(PriceRecord).count() == 0

    @pytest.mark.parametrize("target", [600.0, 1000.0, 5000.0])
    def test_bing_installments_offer_is_never_the_alert_price(
        self, engines, send_alert, db_session, target
    ):
        _serve(engines[1], {**BING_IPHONE_13, "installments": BING_INSTALLMENTS})
        item = _tracked_item(db_session, "iPhone 13 128GB", target)

        result = run_price_check(item=item, db=db_session)

        send_alert.assert_not_called()
        assert result.lowest_price is None
        assert db_session.query(PriceRecord).count() == 0

    @pytest.mark.parametrize("condition", SECOND_HAND_CONDITIONS)
    def test_second_hand_row_is_never_the_alert_price(
        self, engines, send_alert, db_session, condition
    ):
        """The title is the item's name, so only second_hand_condition can drop the row (#3).

        Without it the same row is the alert price: see test_lowest_result_names_merchant_and_link.
        """
        _serve(engines[0], {**GOOGLE_FOLGERS, "second_hand_condition": condition})
        item = _tracked_item(db_session, "Folgers Classic Roast Ground Coffee", 6.0)

        result = run_price_check(item=item, db=db_session)

        send_alert.assert_not_called()
        assert result.lowest_price is None
        assert db_session.query(PriceRecord).count() == 0

    @pytest.mark.parametrize("target", [1000.0, 30000.0])
    def test_foreign_currency_price_is_never_stored_as_usd(
        self, engines, send_alert, db_session, target
    ):
        """The item name matches the lira row's title, so only the currency check can drop it."""
        _serve(engines[0], GOOGLE_LIRA)
        item = _tracked_item(db_session, "iPhone 14 128GB Starlight", target)

        result = run_price_check(item=item, db=db_session)

        assert db_session.query(PriceRecord).count() == 0
        send_alert.assert_not_called()
        assert result.lowest_price is None

    @pytest.mark.parametrize(
        ("engine", "rows", "item_name", "target", "expected"),
        [
            (
                0,
                [GOOGLE_MAXWELL_HOUSE, GOOGLE_FOLGERS],
                "Folgers Classic Roast Ground Coffee",
                6.0,
                (5.88, "google_shopping", "Walmart", GOOGLE_FOLGERS["product_link"]),
            ),
            (
                1,
                [BING_BI_SWING_JACKET, BING_PUFFER_JACKET],
                "Packable Puffer Jacket",
                100.0,
                (
                    99.95,
                    "bing_shopping",
                    "Vermont Country Store",
                    BING_PUFFER_JACKET["external_link"],
                ),
            ),
        ],
        ids=["google", "bing"],
    )
    def test_lowest_result_names_merchant_and_link(
        self, engines, send_alert, db_session, engine, rows, item_name, target, expected
    ):
        """The check result and the alert carry the merchant and link; storage keeps the engine."""
        _serve(engines[engine], *rows)
        item = _tracked_item(db_session, item_name, target)

        result = run_price_check(item=item, db=db_session)

        assert (
            result.lowest_price,
            result.lowest_source,
            result.lowest_merchant,
            result.lowest_url,
        ) == expected
        alerted = send_alert.call_args.kwargs["result"]
        assert (alerted.price, alerted.source, alerted.merchant, alerted.url) == expected
        assert {r.source for r in db_session.query(PriceRecord)} == {result.lowest_source}
        assert result.searches_used == 2  # #24: both engines searched


# #9: a price that stays on target is emailed once; after that, a new low or the cooldown.

T0 = dt.datetime(2026, 10, 6, 9, 0, tzinfo=dt.UTC)


def _check_at(
    db_session, monkeypatch, item: Item, price: float, at: dt.datetime
) -> PriceCheckResult:
    """Check `item` at time `at`, with one engine that finds it at `price`."""
    engine = MagicMock()
    engine.name = "mock"
    engine.search.return_value = [
        SearchResult(title=f"{item.name} Bargain", price=price, currency="USD", source="mock")
    ]
    monkeypatch.setattr("hunter_bargain.services.searcher._now", lambda: at)
    with patch("hunter_bargain.services.searcher._ENGINES", [engine]):
        return run_price_check(item=item, db=db_session)


def _alert_state(db_session, item: Item) -> tuple[float | None, dt.datetime | None]:
    """The item's committed alert state: the rollback drops anything left uncommitted. SQLite
    returns last_alerted_at without a time zone; it is stored in UTC."""
    db_session.rollback()
    alerted_at = item.last_alerted_at
    return item.last_alert_price, alerted_at.replace(tzinfo=dt.UTC) if alerted_at else None


class TestAlertDedup:
    """Alert state on items: one email per deal, then only a new low or after the cooldown."""

    @pytest.fixture(autouse=True)
    def policy(self, monkeypatch, send_alert):
        """The default policy (any new low; 7 days), and a send_price_alert that succeeds."""
        monkeypatch.setattr(app_settings, "alert_min_drop_pct", 0.0)
        monkeypatch.setattr(app_settings, "alert_cooldown_days", 7.0)
        send_alert.return_value = True

    def test_repeated_checks_at_an_unchanged_price_send_one_alert(
        self, db_session, monkeypatch, send_alert
    ):
        item = _tracked_item(db_session, "Tracked Widget", target_price=50.0)

        sent = [
            _check_at(db_session, monkeypatch, item, 45.0, T0 + dt.timedelta(days=day)).alert_sent
            for day in range(3)
        ]

        assert sent == [True, False, False]
        assert send_alert.call_count == 1
        assert _alert_state(db_session, item) == (45.0, T0)

    def test_a_new_low_sends_a_new_alert(self, db_session, monkeypatch, send_alert):
        """Higher prices, still on target, and a return to the last alert's price send nothing."""
        item = _tracked_item(db_session, "Tracked Widget", target_price=50.0)
        prices = [45.0, 44.99, 46.0, 44.99, 44.0]

        sent = [
            _check_at(
                db_session, monkeypatch, item, price, T0 + dt.timedelta(hours=hour)
            ).alert_sent
            for hour, price in enumerate(prices)
        ]

        assert sent == [True, True, False, False, True]
        assert [c.kwargs["result"].price for c in send_alert.call_args_list] == [45.0, 44.99, 44.0]
        assert _alert_state(db_session, item) == (44.0, T0 + dt.timedelta(hours=4))

    def test_after_the_cooldown_the_same_price_is_emailed_again(
        self, db_session, monkeypatch, send_alert
    ):
        item = _tracked_item(db_session, "Tracked Widget", target_price=50.0)
        cooldown = dt.timedelta(days=7)

        sent = [
            _check_at(db_session, monkeypatch, item, 45.0, at).alert_sent
            for at in (T0, T0 + cooldown - dt.timedelta(seconds=1), T0 + cooldown)
        ]

        assert sent == [True, False, True]
        assert send_alert.call_count == 2
        assert _alert_state(db_session, item) == (45.0, T0 + cooldown)

    def test_a_failed_send_leaves_the_state_alone_and_the_next_check_tries_again(
        self, db_session, monkeypatch, send_alert
    ):
        item = _tracked_item(db_session, "Tracked Widget", target_price=50.0)
        send_alert.return_value = False  # SMTP not configured, refused or failed

        assert _check_at(db_session, monkeypatch, item, 45.0, T0).alert_sent is False
        assert _alert_state(db_session, item) == (None, None)

        send_alert.return_value = True
        later = T0 + dt.timedelta(hours=1)
        assert _check_at(db_session, monkeypatch, item, 45.0, later).alert_sent is True
        assert send_alert.call_count == 2
        assert _alert_state(db_session, item) == (45.0, later)

    def test_price_above_target_sends_nothing_and_keeps_no_state(
        self, db_session, monkeypatch, send_alert
    ):
        item = _tracked_item(db_session, "Tracked Widget", target_price=50.0)

        assert _check_at(db_session, monkeypatch, item, 55.0, T0).alert_sent is False
        send_alert.assert_not_called()
        assert _alert_state(db_session, item) == (None, None)

    @pytest.mark.parametrize(
        ("price", "alerted"),
        [(90.01, False), (90.0, True), (80.0, True)],
        ids=["less-than-10pct-lower", "exactly-10pct-lower", "20pct-lower"],
    )
    def test_alert_min_drop_pct_sets_how_much_lower_a_new_low_must_be(
        self, db_session, monkeypatch, price, alerted
    ):
        monkeypatch.setattr(app_settings, "alert_min_drop_pct", 10.0)
        item = _tracked_item(db_session, "Tracked Widget", target_price=100.0)
        assert _check_at(db_session, monkeypatch, item, 100.0, T0).alert_sent is True

        later = T0 + dt.timedelta(days=1)
        assert _check_at(db_session, monkeypatch, item, price, later).alert_sent is alerted

    @pytest.mark.parametrize(
        ("last", "price", "alerted"),
        [(1.00, 0.93, True), (1.00, 0.94, False), (1.05, 0.98, False)],
        ids=["exactly-7pct-lower", "6pct-lower", "6.67pct-lower"],
    )
    def test_a_drop_of_exactly_alert_min_drop_pct_is_a_new_low(
        self, db_session, monkeypatch, last, price, alerted
    ):
        """Thresholds that binary floating point cannot hold exactly: 1.00 less 7% is
        0.9299999999999999 as a float, and 1.05 less 7% (0.9765) rounds up to 0.98 in cents."""
        monkeypatch.setattr(app_settings, "alert_min_drop_pct", 7.0)
        item = _tracked_item(db_session, "Tracked Widget", target_price=2.0)
        assert _check_at(db_session, monkeypatch, item, last, T0).alert_sent is True

        later = T0 + dt.timedelta(days=1)
        assert _check_at(db_session, monkeypatch, item, price, later).alert_sent is alerted


# #29: which merchants' prices may alert (MERCHANT_DENYLIST, MERCHANT_ALLOWLIST), and a price far
# below the check's median flagged "verify merchant". One engine finds the item at the offers.


def _offers(*offers: tuple[float, str | None]) -> MagicMock:
    """An engine that finds the Tracked Widget at each (price, merchant) offer."""
    engine = MagicMock()
    engine.name = "mock"
    engine.search.return_value = [
        SearchResult(
            title="Tracked Widget", price=price, currency="USD", source="mock", merchant=merchant
        )
        for price, merchant in offers
    ]
    return engine


def _check_offers(
    db_session, *offers: tuple[float, str | None], target_price: float | None = 200.0
) -> PriceCheckResult:
    item = Item(name="Tracked Widget", notify_email="n@x.com", target_price=target_price)
    db_session.add(item)
    db_session.commit()
    with patch("hunter_bargain.services.searcher._ENGINES", [_offers(*offers)]):
        return run_price_check(item=item, db=db_session)


class TestMerchantLists:
    def test_denylisted_merchant_never_triggers_an_alert(self, db_session, send_alert, monkeypatch):
        """Its lower price is saved, but the next merchant's price is the one alerted on."""
        monkeypatch.setattr(app_settings, "merchant_denylist", ["Scam Shop"])

        result = _check_offers(
            db_session, (50.0, "Scam Shop"), (80.0, "Best Buy"), (85.0, "Walmart")
        )

        alerted = send_alert.call_args.kwargs["result"]
        assert (alerted.price, alerted.merchant) == (80.0, "Best Buy")
        assert (result.lowest_price, result.lowest_merchant) == (80.0, "Best Buy")
        assert (result.results_count, result.merchant_excluded) == (3, 1)
        assert sorted(r.price for r in db_session.query(PriceRecord)) == [50.0, 80.0, 85.0]

    def test_no_alert_when_every_offer_is_from_a_denylisted_merchant(
        self, db_session, send_alert, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "merchant_denylist", ["Scam Shop"])

        result = _check_offers(db_session, (50.0, "Scam Shop"), (60.0, " scam  SHOP "))

        send_alert.assert_not_called()
        assert (result.lowest_price, result.lowest_merchant) == (None, None)
        assert (result.results_count, result.merchant_excluded) == (2, 2)
        assert db_session.query(PriceRecord).count() == 2

    def test_allowlist_alerts_only_on_its_merchants(self, db_session, send_alert, monkeypatch):
        """A listing that names no merchant cannot be on the allowlist."""
        monkeypatch.setattr(app_settings, "merchant_allowlist", ["Best Buy"])

        result = _check_offers(db_session, (40.0, None), (50.0, "Walmart"), (90.0, "Best Buy"))

        assert send_alert.call_args.kwargs["result"].merchant == "Best Buy"
        assert (result.lowest_price, result.merchant_excluded) == (90.0, 2)

    def test_without_lists_the_lowest_price_alerts_whoever_sells_it(self, db_session, send_alert):
        result = _check_offers(db_session, (50.0, "Scam Shop"), (40.0, None), (80.0, "Best Buy"))

        assert send_alert.call_args.kwargs["result"].price == 40.0
        assert (result.lowest_price, result.merchant_excluded) == (40.0, 0)


class TestVerifyMerchant:
    def test_price_50_percent_below_the_median_is_flagged(self, db_session, send_alert):
        result = _check_offers(db_session, (50.0, "A"), (100.0, "B"), (100.0, "C"))

        assert result.verify_merchant is True
        assert send_alert.call_args.kwargs["verify_merchant"] is True

    @pytest.mark.parametrize(
        ("prices", "flagged"),
        [
            ((59.99, 100.0, 100.0), True),
            # Exactly 40% below is not "more than": 6.18 is 10.30 less 40%, though in binary
            # floating point 10.3 * 0.6 is 6.180000000000001.
            ((60.0, 100.0, 100.0), False),
            ((6.18, 10.3, 10.3), False),
            ((6.17, 10.3, 10.3), True),
            # An even count: the median is the mean of the middle two, here 100.
            ((59.0, 90.0, 110.0, 120.0), True),
            ((60.0, 90.0, 110.0, 120.0), False),
            # Two prices are too few to tell a typical price from an outlier.
            ((10.0, 100.0), False),
        ],
    )
    def test_flagged_only_more_than_40_percent_below_the_median(self, db_session, prices, flagged):
        offers = [(price, f"Shop {i}") for i, price in enumerate(prices)]

        result = _check_offers(db_session, *offers, target_price=None)

        assert result.verify_merchant is flagged

    def test_verify_merchant_pct_sets_how_far_below(self, db_session, monkeypatch):
        monkeypatch.setattr(app_settings, "verify_merchant_pct", 50.0)

        assert (
            _check_offers(db_session, (51.0, "A"), (100.0, "B"), (100.0, "C")).verify_merchant
            is False
        )
        assert (
            _check_offers(db_session, (49.0, "A"), (100.0, "B"), (100.0, "C")).verify_merchant
            is True
        )

    def test_the_median_leaves_out_merchants_that_may_not_alert(self, db_session, monkeypatch):
        """Four listings from a denylisted seller would make the median 20; it is 100 without."""
        monkeypatch.setattr(app_settings, "merchant_denylist", ["Scam Shop"])
        offers = [(20.0, "Scam Shop")] * 4 + [(50.0, "Best Buy"), (100.0, "B"), (100.0, "C")]

        result = _check_offers(db_session, *offers, target_price=None)

        assert (result.lowest_price, result.merchant_excluded) == (50.0, 4)
        assert result.verify_merchant is True

    def test_no_flag_without_a_price(self, db_session, monkeypatch):
        monkeypatch.setattr(app_settings, "merchant_denylist", ["Scam Shop"])

        result = _check_offers(
            db_session, (20.0, "Scam Shop"), (100.0, "Scam Shop"), (100.0, "Scam Shop")
        )

        assert (result.lowest_price, result.verify_merchant) == (None, False)

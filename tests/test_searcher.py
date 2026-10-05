"""Tests for the search engine implementations and orchestrator."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.services.engines import bing, google
from hunter_bargain.services.engines.base import (
    SearchResult,
    is_installment_offer,
    is_usd_price,
)
from hunter_bargain.services.engines.bing import BingShoppingEngine
from hunter_bargain.services.engines.google import GoogleShoppingEngine, _parse_price
from hunter_bargain.services.searcher import (
    _build_query,
    _has_accessory_extension,
    _is_relevant,
    run_price_check,
)

# shopping_results rows from SerpAPI's documented examples, fetched 2026-10-05, trimmed to the
# fields the engines read plus the position or title. Google Shopping rows have no "link" field.

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

GOOGLE_LOGGER = "hunter_bargain.services.engines.google"
BING_LOGGER = "hunter_bargain.services.engines.bing"


def _without(row: dict, *fields: str) -> dict:
    """A documented row with some of its fields left out."""
    return {key: value for key, value in row.items() if key not in fields}


def _serve(search_cls: MagicMock, *rows: dict) -> None:
    """Make a patched SerpAPI client class return these shopping_results rows."""
    search_cls.return_value.get_dict.return_value = {"shopping_results": list(rows)}


def _skip_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "skipped" in r.getMessage()]


@pytest.fixture
def google_search():
    """The Google Shopping engine's SerpAPI client class, patched, with a fake key."""
    with (
        patch("hunter_bargain.services.engines.google.settings") as settings,
        patch("hunter_bargain.services.engines.google.GoogleSearch") as search_cls,
    ):
        settings.serpapi_key = SecretStr("test-key")
        _serve(search_cls)
        yield search_cls


@pytest.fixture
def bing_search():
    """The Bing Shopping engine's SerpAPI client class, patched, with a fake key."""
    with (
        patch("hunter_bargain.services.engines.bing.settings") as settings,
        patch("hunter_bargain.services.engines.bing.GoogleSearch") as search_cls,
    ):
        settings.serpapi_key = SecretStr("test-key")
        _serve(search_cls)
        yield search_cls


@pytest.fixture
def engines(google_search, bing_search):
    """Both real engines in the searcher's registry, each answering from its patched client."""
    with patch(
        "hunter_bargain.services.searcher._ENGINES", [GoogleShoppingEngine(), BingShoppingEngine()]
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
    """Tests for the price string parser."""

    def test_standard_price(self):
        assert _parse_price("$1,299.00") == 1299.00

    def test_no_comma(self):
        assert _parse_price("$29.99") == 29.99

    def test_empty_string(self):
        assert _parse_price("") is None

    def test_none(self):
        assert _parse_price(None) is None

    def test_invalid(self):
        assert _parse_price("free") is None

    @pytest.mark.parametrize("parse", [google._parse_price, bing._parse_price])
    @pytest.mark.parametrize("price_str", ["$20.99/mo", "From $999"])
    def test_monthly_or_from_price_is_not_a_number(self, parse, price_str):
        """The fallback parser gives no number for a monthly price or a "From" price (#35)."""
        assert parse(price_str) is None


class TestPriceChecks:
    """The checks in engines/base.py that decide whether a row's price can be used (#35)."""

    @pytest.mark.parametrize(
        ("price", "expected"),
        [
            ("$5.88", True),
            ("$1,449.99", True),
            ("$20.99/mo", True),  # in dollars, but is_installment_offer rejects it
            ("TRY 28,782.94", False),
            ("€608", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_usd_price(self, price, expected):
        assert is_usd_price(price) is expected

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
        assert is_installment_offer(row, key) is expected


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
    """Tests for the Google Shopping engine (mocked SerpAPI calls)."""

    @patch("hunter_bargain.services.engines.google.settings")
    def test_skips_when_no_api_key(self, mock_settings):
        mock_settings.serpapi_key = SecretStr("")
        engine = GoogleShoppingEngine()
        results = engine.search("iPhone")
        assert results == []

    def test_returns_sorted_results(self, google_search):
        _serve(google_search, GOOGLE_FOLGERS, GOOGLE_BUSTELO, GOOGLE_MAXWELL_HOUSE)

        results = GoogleShoppingEngine().search("coffee")

        assert [(r.title, r.price) for r in results] == [
            ("Bustelo Coffee Espresso", 4.99),
            ("Folgers Classic Roast Ground Coffee", 5.88),
            ("Maxwell House Original Roast Ground Coffee", 18.49),
        ]
        assert {(r.source, r.currency) for r in results} == {("google_shopping", "USD")}

    def test_url_is_product_link_and_merchant_is_source(self, google_search):
        """A row has product_link and no link: the result links there and names the seller (#34)."""
        _serve(google_search, GOOGLE_FOLGERS)

        [result] = GoogleShoppingEngine().search("coffee")

        assert result.url == "https://www.google.com/shopping/product/16914877625280977865?gl=us"
        assert result.merchant == "Walmart"
        assert result.source == "google_shopping"
        assert result.extensions == ("Nearby, 3 mi",)

    @pytest.mark.parametrize(
        "row",
        [GOOGLE_MONTHLY, _without(GOOGLE_MONTHLY, "installment")],
        ids=["documented", "mo-suffix-only"],
    )
    def test_skips_monthly_price(self, google_search, caplog, row):
        _serve(google_search, row, GOOGLE_FOLGERS)

        with caplog.at_level(logging.DEBUG, logger=GOOGLE_LOGGER):
            results = GoogleShoppingEngine().search("iphone")

        assert [r.title for r in results] == ["Folgers Classic Roast Ground Coffee"]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "payment-plan offer 'Restored Apple iPhone 15 Pro Max'" in record.getMessage()

    def test_skips_foreign_currency_price(self, google_search, caplog):
        """The lira row is not taken as 28782.94 USD, and its euro alternative_price is not used."""
        _serve(google_search, GOOGLE_LIRA, GOOGLE_FOLGERS)

        with caplog.at_level(logging.DEBUG, logger=GOOGLE_LOGGER):
            results = GoogleShoppingEngine().search("iphone")

        assert [r.title for r in results] == ["Folgers Classic Roast Ground Coffee"]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "'Apple iPhone 14 128GB Starlight', no USD price (price 'TRY 28,782.94')" in (
            record.getMessage()
        )

    def test_skips_row_without_price_string(self, google_search):
        """Without a price string the currency is unknown, so extracted_price is not used."""
        _serve(google_search, _without(GOOGLE_FOLGERS, "price"))

        assert GoogleShoppingEngine().search("coffee") == []

    def test_parses_price_string_without_extracted_price(self, google_search):
        _serve(google_search, _without(GOOGLE_FOLGERS, "extracted_price"))

        [result] = GoogleShoppingEngine().search("coffee")

        assert (result.price, result.currency) == (5.88, "USD")


class TestBingShoppingEngine:
    """Tests for the Bing Shopping engine (mocked SerpAPI calls)."""

    def test_url_is_external_link_and_merchant_is_seller(self, bing_search):
        """The seller's own page, not the bing.com/ck redirect in link (#34)."""
        _serve(bing_search, BING_BI_SWING_JACKET, BING_PUFFER_JACKET)

        results = BingShoppingEngine().search("jacket")

        assert [(r.price, r.merchant, r.url) for r in results] == [
            (99.95, "Vermont Country Store", BING_PUFFER_JACKET["external_link"]),
            (168.0, "Ralph Lauren", BING_BI_SWING_JACKET["external_link"]),
        ]
        assert not any(r.url.startswith("https://www.bing.com/") for r in results)
        assert {(r.source, r.currency) for r in results} == {("bing_shopping", "USD")}

    def test_falls_back_to_link_without_external_link(self, bing_search):
        _serve(bing_search, _without(BING_PUFFER_JACKET, "external_link"))

        [result] = BingShoppingEngine().search("jacket")

        assert result.url == BING_PUFFER_JACKET["link"]
        assert result.merchant == "Vermont Country Store"

    def test_skips_installments_offer(self, bing_search, caplog):
        """A row with an installments object is skipped, like Google's monthly prices (#35)."""
        _serve(
            bing_search, {**BING_IPHONE_13, "installments": BING_INSTALLMENTS}, BING_PUFFER_JACKET
        )

        with caplog.at_level(logging.DEBUG, logger=BING_LOGGER):
            results = BingShoppingEngine().search("iphone")

        assert [r.title for r in results] == [BING_PUFFER_JACKET["title"]]
        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "payment-plan offer 'Apple - iPhone 13 5G 128GB (Unlocked) - Midnight'" in (
            record.getMessage()
        )

    def test_skips_row_without_price_string(self, bing_search, caplog):
        """Without a price string the currency is unknown, so extracted_price is not used."""
        _serve(bing_search, _without(BING_PUFFER_JACKET, "price"))

        with caplog.at_level(logging.DEBUG, logger=BING_LOGGER):
            assert BingShoppingEngine().search("jacket") == []

        [record] = _skip_records(caplog)
        assert record.levelno == logging.DEBUG
        assert "no USD price (price None)" in record.getMessage()

    def test_parses_price_string_without_extracted_price(self, bing_search):
        _serve(bing_search, _without(BING_PUFFER_JACKET, "extracted_price"))

        [result] = BingShoppingEngine().search("jacket")

        assert (result.price, result.currency) == (99.95, "USD")


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


class TestDocumentedRowsThroughPriceCheck:
    """Documented rows through the real engines, relevance filter, storage and alert decision."""

    @pytest.mark.parametrize("target", [20.99, 200.0, 209.9, 400.0, 900.0])
    def test_monthly_price_is_never_the_alert_price(self, engines, send_alert, db_session, target):
        """Up to a $209.90 target the row passes the 10% floor; only the new check stops it."""
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

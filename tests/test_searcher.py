"""Tests for the search engine implementations and orchestrator."""

from unittest.mock import MagicMock, patch

from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.engines.google import GoogleShoppingEngine, _parse_price
from hunter_bargain.services.searcher import _build_query, run_price_check


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


class TestGoogleShoppingEngine:
    """Tests for the Google Shopping engine (mocked SerpAPI calls)."""

    @patch("hunter_bargain.services.engines.google.settings")
    def test_skips_when_no_api_key(self, mock_settings):
        mock_settings.serpapi_key = ""
        engine = GoogleShoppingEngine()
        results = engine.search("iPhone")
        assert results == []

    @patch("hunter_bargain.services.engines.google.GoogleSearch")
    @patch("hunter_bargain.services.engines.google.settings")
    def test_returns_sorted_results(self, mock_settings, mock_google_search_cls):
        mock_settings.serpapi_key = "test-key"
        mock_instance = MagicMock()
        mock_instance.get_dict.return_value = {
            "shopping_results": [
                {"title": "Expensive", "extracted_price": 999.0, "link": "http://a.com"},
                {"title": "Cheap", "extracted_price": 499.0, "link": "http://b.com"},
                {"title": "Mid", "extracted_price": 749.0, "link": "http://c.com"},
            ]
        }
        mock_google_search_cls.return_value = mock_instance

        engine = GoogleShoppingEngine()
        results = engine.search("Widget")

        assert len(results) == 3
        assert results[0].price == 499.0
        assert results[0].title == "Cheap"
        assert results[2].price == 999.0


class TestRunPriceCheck:
    """Tests for the full price check orchestrator."""

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_aggregates_results_and_persists(self, mock_engines, mock_notify, db_session):
        """Verify that results from multiple engines are aggregated and saved."""
        # Set up a mock engine
        mock_engine = MagicMock()
        mock_engine.name = "mock_engine"
        mock_engine.search.return_value = [
            SearchResult(
                title="Deal",
                price=50.0,
                currency="USD",
                source="mock_engine",
                url="http://deal.com",
            ),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        # Create a real item in the test DB
        item = Item(name="Test Item", notify_email="t@x.com", target_price=100.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        result = run_price_check(item=item, db=db_session)

        assert result.item_id == item.id
        assert result.lowest_price == 50.0
        assert result.results_count == 1

        # Verify persistence
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
            SearchResult(title="Bargain", price=45.0, currency="USD", source="mock"),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Tracked", notify_email="n@x.com", target_price=50.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        run_price_check(item=item, db=db_session)
        mock_notify.assert_called_once()

    @patch("hunter_bargain.services.searcher.send_price_alert")
    @patch("hunter_bargain.services.searcher._ENGINES")
    def test_no_notification_when_above_target(self, mock_engines, mock_notify, db_session):
        """Verify no notification when lowest price > target."""
        mock_engine = MagicMock()
        mock_engine.name = "mock"
        mock_engine.search.return_value = [
            SearchResult(title="Pricey", price=200.0, currency="USD", source="mock"),
        ]
        mock_engines.__iter__ = lambda self: iter([mock_engine])

        item = Item(name="Tracked", notify_email="n@x.com", target_price=100.0)
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        run_price_check(item=item, db=db_session)
        mock_notify.assert_not_called()

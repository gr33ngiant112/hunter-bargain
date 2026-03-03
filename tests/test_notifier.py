"""Tests for the email notification service."""

from unittest.mock import MagicMock, patch

from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.notifier import send_price_alert


class TestSendPriceAlert:
    """Tests for send_price_alert — all SMTP calls are mocked."""

    @patch("hunter_bargain.services.notifier.settings")
    def test_skips_when_smtp_not_configured(self, mock_settings):
        mock_settings.smtp_user = ""
        mock_settings.smtp_password = ""
        item = MagicMock()
        item.id = 1
        result = SearchResult(title="X", price=50.0, currency="USD", source="test")
        assert send_price_alert(item=item, result=result) is False

    @patch("hunter_bargain.services.notifier.smtplib.SMTP")
    @patch("hunter_bargain.services.notifier.settings")
    def test_sends_email_successfully(self, mock_settings, mock_smtp_cls):
        mock_settings.smtp_user = "bot@example.com"
        mock_settings.smtp_password = "secret"
        mock_settings.smtp_host = "smtp.example.com"
        mock_settings.smtp_port = 587
        mock_settings.email_from = "bot@example.com"

        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        item = MagicMock()
        item.id = 1
        item.name = "Widget"
        item.target_price = 60.0
        item.notify_email = "user@example.com"

        result = SearchResult(
            title="Great Widget",
            price=49.99,
            currency="USD",
            source="google_shopping",
            url="http://example.com/widget",
        )

        assert send_price_alert(item=item, result=result) is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("bot@example.com", "secret")
        mock_server.send_message.assert_called_once()


def test_health_check(client):
    """GET /health returns ok status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

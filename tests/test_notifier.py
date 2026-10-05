"""Tests for the email notification service."""

import logging
import smtplib
import ssl
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from hunter_bargain.config import Settings
from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.notifier import send_price_alert

FAKE_PASSWORD = "fake-pass-for-test"


def _item() -> MagicMock:
    item = MagicMock()
    item.id = 1
    item.name = "Widget"
    item.target_price = 60.0
    item.notify_email = "user@example.com"
    return item


def _result() -> SearchResult:
    return SearchResult(title="Great Widget", price=49.99, currency="USD", source="google_shopping")


def _server(smtp_cls: MagicMock) -> MagicMock:
    """Make `with smtp_cls(...) as server` bind a mock server and let exceptions propagate."""
    server = MagicMock()
    smtp_cls.return_value.__enter__ = MagicMock(return_value=server)
    smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
    return server


def _assert_verifying(context: object) -> None:
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.fixture
def smtp_settings(monkeypatch):
    """Real Settings with fake SMTP values, patched into the notifier.

    `_env_file=None` keeps any local .env out of the tests. SMTP_TIMEOUT is set through
    the environment so the tests also cover the variable name. ALERT_RECIPIENTS allows
    `_item()`'s address.
    """
    monkeypatch.setenv("SMTP_TIMEOUT", "12.5")
    monkeypatch.setenv("ALERT_RECIPIENTS", "user@example.com")
    fake = Settings(
        _env_file=None,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password=FAKE_PASSWORD,
    )
    with patch("hunter_bargain.services.notifier.settings", fake):
        yield fake


@pytest.fixture
def smtp_classes():
    """Patch SMTP and SMTP_SSL so no test can open a real connection."""
    with (
        patch("hunter_bargain.services.notifier.smtplib.SMTP") as smtp_cls,
        patch("hunter_bargain.services.notifier.smtplib.SMTP_SSL") as smtp_ssl_cls,
    ):
        yield smtp_cls, smtp_ssl_cls


class TestSendPriceAlert:
    """Tests for send_price_alert — all SMTP calls are mocked."""

    @patch("hunter_bargain.services.notifier.settings")
    def test_skips_when_smtp_not_configured(self, mock_settings):
        mock_settings.smtp_user = ""
        mock_settings.smtp_password = SecretStr("")
        item = MagicMock()
        item.id = 1
        result = SearchResult(title="X", price=50.0, currency="USD", source="test")
        assert send_price_alert(item=item, result=result) is False

    @patch("hunter_bargain.services.notifier.smtplib.SMTP")
    @patch("hunter_bargain.services.notifier.settings")
    def test_sends_email_successfully(self, mock_settings, mock_smtp_cls):
        mock_settings.smtp_user = "bot@example.com"
        mock_settings.smtp_password = SecretStr("secret")
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

        sent_message = mock_server.send_message.call_args.args[0]
        serialized = sent_message.as_string()
        assert "Buy Now" in serialized
        assert "background-color: #2d8a4e" in serialized
        assert "Link: http://example.com/widget" in serialized

    def test_skips_when_password_is_empty(self, smtp_settings, smtp_classes):
        smtp_settings.smtp_password = SecretStr("")
        smtp_cls, smtp_ssl_cls = smtp_classes

        assert send_price_alert(item=_item(), result=_result()) is False

        smtp_cls.assert_not_called()
        smtp_ssl_cls.assert_not_called()

    def test_starttls_verifies_server_certificate(self, smtp_settings, smtp_classes):
        smtp_cls, smtp_ssl_cls = smtp_classes
        server = _server(smtp_cls)

        assert send_price_alert(item=_item(), result=_result()) is True

        server.starttls.assert_called_once()
        _assert_verifying(server.starttls.call_args.kwargs.get("context"))
        smtp_ssl_cls.assert_not_called()

    def test_smtp_connect_uses_configured_timeout(self, smtp_settings, smtp_classes):
        smtp_cls, _ = smtp_classes
        _server(smtp_cls)

        assert send_price_alert(item=_item(), result=_result()) is True

        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=12.5)
        assert smtp_settings.smtp_timeout == 12.5  # the value came from SMTP_TIMEOUT

    def test_port_465_uses_implicit_tls(self, smtp_settings, smtp_classes):
        smtp_settings.smtp_port = 465
        smtp_cls, smtp_ssl_cls = smtp_classes
        server = _server(smtp_ssl_cls)

        assert send_price_alert(item=_item(), result=_result()) is True

        smtp_ssl_cls.assert_called_once()
        smtp_cls.assert_not_called()
        args, kwargs = smtp_ssl_cls.call_args
        assert args == ("smtp.example.com", 465)
        assert kwargs["timeout"] == 12.5
        _assert_verifying(kwargs["context"])
        server.starttls.assert_not_called()
        server.login.assert_called_once_with("bot@example.com", FAKE_PASSWORD)
        server.send_message.assert_called_once()

    def test_failed_login_does_not_log_password(self, smtp_settings, smtp_classes, caplog):
        smtp_cls, _ = smtp_classes
        server = _server(smtp_cls)
        server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted"
        )

        with caplog.at_level(logging.DEBUG):
            assert send_price_alert(item=_item(), result=_result()) is False

        server.login.assert_called_once_with("bot@example.com", FAKE_PASSWORD)
        assert "Failed to send price alert for item 1" in caplog.text
        assert "SMTPAuthenticationError" in caplog.text
        assert FAKE_PASSWORD not in caplog.text

    def test_skips_recipient_not_in_alert_recipients(self, smtp_settings, smtp_classes, caplog):
        """Rows saved before the allowlist existed may hold any address: skip, log, no address."""
        smtp_cls, smtp_ssl_cls = smtp_classes
        item = _item()
        item.notify_email = "someone@example.net"

        with caplog.at_level(logging.DEBUG):
            assert send_price_alert(item=item, result=_result()) is False

        smtp_cls.assert_not_called()
        smtp_ssl_cls.assert_not_called()
        assert "Recipient for item 1 is not in ALERT_RECIPIENTS" in caplog.text
        assert "someone@example.net" not in caplog.text


class TestSmtpSettings:
    """SMTP settings: a finite default timeout and a masked password."""

    def test_smtp_timeout_defaults_to_30_seconds(self, monkeypatch):
        monkeypatch.delenv("SMTP_TIMEOUT", raising=False)
        assert Settings(_env_file=None).smtp_timeout == 30

    def test_smtp_password_is_masked(self):
        settings = Settings(_env_file=None, smtp_password=FAKE_PASSWORD)
        assert FAKE_PASSWORD not in repr(settings)
        assert FAKE_PASSWORD not in str(settings)
        assert isinstance(settings.smtp_password, SecretStr)
        assert settings.smtp_password.get_secret_value() == FAKE_PASSWORD


def test_health_check(client):
    """GET /health returns ok status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

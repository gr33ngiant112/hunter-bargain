"""Tests for the notification service: email, the log when SMTP is not configured, and Apprise."""

import base64
import datetime as dt
import json
import logging
import os
import smtplib
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override
from unittest.mock import MagicMock, patch

import apprise
import pytest
from pydantic import SecretStr, ValidationError

from hunter_bargain.config import Settings, settings
from hunter_bargain.logging_config import configure_logging
from hunter_bargain.models import Item
from hunter_bargain.schemas import PriceLow
from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.notifier import send_notice, send_price_alert
from hunter_bargain.services.searcher import run_price_check

FAKE_PASSWORD = "fake-pass-for-test"

# Harmless markup markers (angle brackets, quotes, an ampersand) and their HTML-escaped form.
MARKUP = "Widget <b>\"Pro\"</b> & 'Co'"
ESCAPED_MARKUP = "Widget &lt;b&gt;&quot;Pro&quot;&lt;/b&gt; &amp; &#x27;Co&#x27;"


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


def _sent_parts(server: MagicMock) -> tuple[str, str]:
    """Decode the plain-text and HTML parts of the message sent through `server`."""
    message = server.send_message.call_args.args[0]
    texts = {
        part.get_content_type(): part.get_payload(decode=True).decode(part.get_content_charset())
        for part in message.walk()
        if not part.is_multipart()
    }
    return texts["text/plain"], texts["text/html"]


def _assert_verifying(context: object) -> None:
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.fixture
def smtp_settings(monkeypatch):
    """Real Settings with fake SMTP values, patched into the notifier.

    `_env_file=None` keeps any local .env out of the tests. SMTP_TIMEOUT is set through
    the environment so the tests also cover the variable name. ALERT_RECIPIENTS allows
    `_item()`'s address. SMTP_SECURITY is unset, so the tests get its default.
    """
    monkeypatch.setenv("SMTP_TIMEOUT", "12.5")
    monkeypatch.delenv("SMTP_SECURITY", raising=False)
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

    def test_default_security_is_starttls_then_login(self, smtp_settings, smtp_classes):
        """SMTP_SECURITY unset: verified STARTTLS first, then the login, then the message."""
        smtp_cls, smtp_ssl_cls = smtp_classes
        server = _server(smtp_cls)

        assert smtp_settings.smtp_security == "tls"
        assert send_price_alert(item=_item(), result=_result()) is True

        assert [call[0] for call in server.method_calls] == ["starttls", "login", "send_message"]
        _assert_verifying(server.starttls.call_args.kwargs.get("context"))
        server.login.assert_called_once_with("bot@example.com", FAKE_PASSWORD)
        smtp_ssl_cls.assert_not_called()

    @pytest.mark.parametrize("port", [1025, 465])
    @pytest.mark.parametrize(("user", "password"), [("", ""), ("bot@example.com", FAKE_PASSWORD)])
    def test_security_none_sends_without_tls_or_login(
        self, smtp_settings, smtp_classes, port, user, password
    ):
        """SMTP_SECURITY=none, for a local test server: no STARTTLS, no SMTPS and no login.

        It sends without credentials, and it never sends the password when one is set.
        """
        smtp_settings.smtp_security = "none"
        smtp_settings.smtp_port = port
        smtp_settings.smtp_user = user
        smtp_settings.smtp_password = SecretStr(password)
        smtp_cls, smtp_ssl_cls = smtp_classes
        server = _server(smtp_cls)

        assert send_price_alert(item=_item(), result=_result()) is True

        smtp_cls.assert_called_once_with("smtp.example.com", port, timeout=12.5)
        smtp_ssl_cls.assert_not_called()
        assert [call[0] for call in server.method_calls] == ["send_message"]

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


class TestSendNotice:
    """A notice about the app itself (#24: SerpAPI searches running low) goes to every
    ALERT_RECIPIENTS address, over the same SMTP path as the price alerts."""

    def test_goes_to_every_alert_recipient_after_starttls_and_login(
        self, smtp_settings, smtp_classes
    ):
        smtp_settings.alert_recipients = ["user@example.com", "ops@example.com"]
        smtp_cls, smtp_ssl_cls = smtp_classes
        server = _server(smtp_cls)

        assert send_notice("Searches running low", "12 left.") is True

        assert [call[0] for call in server.method_calls] == ["starttls", "login", "send_message"]
        _assert_verifying(server.starttls.call_args.kwargs.get("context"))
        server.login.assert_called_once_with("bot@example.com", FAKE_PASSWORD)
        message = server.send_message.call_args.args[0]
        assert (message["Subject"], message["From"], message["To"]) == (
            "Searches running low",
            "bot@example.com",
            "user@example.com, ops@example.com",
        )
        assert message.get_payload(decode=True).decode() == "12 left."
        smtp_ssl_cls.assert_not_called()

    def test_port_465_uses_implicit_tls(self, smtp_settings, smtp_classes):
        smtp_settings.smtp_port = 465
        smtp_cls, smtp_ssl_cls = smtp_classes
        server = _server(smtp_ssl_cls)

        assert send_notice("Searches running low", "12 left.") is True

        _assert_verifying(smtp_ssl_cls.call_args.kwargs["context"])
        assert [call[0] for call in server.method_calls] == ["login", "send_message"]
        smtp_cls.assert_not_called()

    def test_skipped_without_smtp_credentials(self, smtp_settings, smtp_classes, caplog):
        smtp_settings.smtp_password = SecretStr("")
        smtp_cls, smtp_ssl_cls = smtp_classes

        assert send_notice("Searches running low", "12 left.") is False

        smtp_cls.assert_not_called()
        smtp_ssl_cls.assert_not_called()
        assert "SMTP not configured — skipping the email: Searches running low" in caplog.text

    def test_skipped_without_alert_recipients(self, smtp_settings, smtp_classes, caplog):
        smtp_settings.alert_recipients = []
        smtp_cls, smtp_ssl_cls = smtp_classes

        assert send_notice("Searches running low", "12 left.") is False

        smtp_cls.assert_not_called()
        assert "ALERT_RECIPIENTS is empty — skipping the email: Searches running low" in caplog.text

    def test_failed_send_is_logged_not_raised(self, smtp_settings, smtp_classes, caplog):
        smtp_cls, _ = smtp_classes
        server = _server(smtp_cls)
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Not accepted")

        with caplog.at_level(logging.DEBUG):
            assert send_notice("Searches running low", "12 left.") is False

        assert "Failed to send the email: Searches running low" in caplog.text
        assert FAKE_PASSWORD not in caplog.text


class TestAlertContent:
    """Item names come from API callers and the other values from third-party listings.

    None of them may add markup or a non-http(s) link to the alert.
    """

    @pytest.mark.parametrize("field", ["name", "title", "source", "currency", "merchant"])
    def test_html_part_escapes_markup(self, smtp_settings, smtp_classes, field):
        item, result = _item(), _result()
        if field == "name":
            item.name = MARKUP
        else:
            result = replace(result, **{field: MARKUP})
        server = _server(smtp_classes[0])

        assert send_price_alert(item=item, result=result) is True

        _, html_part = _sent_parts(server)
        assert ESCAPED_MARKUP in html_part
        assert MARKUP not in html_part
        assert "<b>" not in html_part

    def test_merchant_is_shown_next_to_engine_name(self, smtp_settings, smtp_classes):
        """AT&T is a seller in SerpAPI's documented Google Shopping rows (#34)."""
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=replace(_result(), merchant="AT&T")) is True

        plain, html_part = _sent_parts(server)
        assert "Source: AT&T via google_shopping\n" in plain
        assert ">AT&amp;T via google_shopping</td>" in html_part

    def test_without_merchant_source_is_engine_name(self, smtp_settings, smtp_classes):
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=_result()) is True

        plain, html_part = _sent_parts(server)
        assert "Source: google_shopping\n" in plain
        assert ">google_shopping</td>" in html_part

    def test_lows_are_shown_after_the_target(self, smtp_settings, smtp_classes):
        """The item's 90-day and all-time lows (#31)."""
        server = _server(smtp_classes[0])
        low_90_days = PriceLow(
            price=47.5,
            currency="USD",
            source="bing_shopping",
            checked_at=dt.datetime(2026, 10, 2, 9, 0, tzinfo=dt.UTC),
        )
        all_time_low = low_90_days.model_copy(
            update={"price": 39.0, "checked_at": dt.datetime(2026, 3, 26, 9, 0, tzinfo=dt.UTC)}
        )

        assert send_price_alert(
            item=_item(), result=_result(), low_90_days=low_90_days, all_time_low=all_time_low
        )

        plain, html_part = _sent_parts(server)
        assert (
            "Target: $60.00\nLowest in 90 days: $47.50 on 2026-10-02\n"
            "All-time low: $39.00 on 2026-03-26\nSource: "
        ) in plain
        assert ">Lowest in 90 days</td><td " in html_part
        assert ">$47.50 on 2026-10-02</td>" in html_part
        assert ">All-time low</td><td " in html_part
        assert ">$39.00 on 2026-03-26</td>" in html_part

    def test_without_lows_no_low_is_shown(self, smtp_settings, smtp_classes):
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=_result()) is True

        plain, html_part = _sent_parts(server)
        assert "Target: $60.00\nSource: " in plain
        assert "Lowest in 90 days" not in html_part
        assert "All-time low" not in html_part

    def test_price_to_verify_warns_and_asks_to_verify_the_merchant(
        self, smtp_settings, smtp_classes
    ):
        """#29: a price far below the check's other prices: no "Buy Now"."""
        server = _server(smtp_classes[0])
        result = replace(_result(), url="https://example.com/widget")

        assert send_price_alert(item=_item(), result=result, verify_merchant=True) is True

        plain, html_part = _sent_parts(server)
        warning = (
            "Verify merchant: this price is more than 40% below the median of this check's"
            " prices. Check the seller before you buy."
        )
        assert plain.startswith(f"Price Alert: Widget\n{warning}\nPrice: $49.99 USD\n")
        escaped = warning.replace("'", "&#x27;")  # as html.escape writes it
        assert f">{escaped}</p>" in html_part
        assert ">Verify merchant</a>" in html_part
        assert "Buy Now" not in html_part

    def test_price_to_verify_warns_without_a_link_too(self, smtp_settings, smtp_classes):
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=_result(), verify_merchant=True) is True

        plain, html_part = _sent_parts(server)
        assert "\nVerify merchant: this price is more than 40% below" in plain
        assert "Link: N/A\n" in plain
        assert ">Verify merchant: this price is more than 40% below" in html_part
        assert "</a>" not in html_part

    def test_other_prices_keep_buy_now(self, smtp_settings, smtp_classes):
        server = _server(smtp_classes[0])
        result = replace(_result(), url="https://example.com/widget")

        assert send_price_alert(item=_item(), result=result) is True

        plain, html_part = _sent_parts(server)
        assert ">Buy Now</a>" in html_part
        assert "Verify merchant" not in plain + html_part

    @pytest.mark.parametrize(
        ("details", "lines"),
        [
            (
                {"rating": 4.6, "reviews": "13,000", "multiple_sources": True},
                ["Rating: 4.6 out of 5 from 13,000 reviews", "Sellers: One of several sellers"],
            ),
            ({"rating": 5.0}, ["Rating: 5 out of 5"]),
            ({"reviews": "5K+"}, ["Reviews: 5K+"]),
            ({"multiple_sources": True}, ["Sellers: One of several sellers"]),
            ({}, []),
        ],
        ids=["all", "rating", "reviews", "several-sellers", "none"],
    )
    def test_listing_details_follow_the_source(self, smtp_settings, smtp_classes, details, lines):
        """#29: the item's rating and review count, and whether several sellers have it."""
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=replace(_result(), **details)) is True

        plain, html_part = _sent_parts(server)
        expected = "".join(f"{line}\n" for line in lines)
        assert f"Source: google_shopping\n{expected}Link: N/A\n" in plain
        for line in lines:
            label, value = line.split(": ")
            assert f">{label}</td><td " in html_part
            assert f">{value}</td></tr>" in html_part

    def test_listing_details_are_escaped_in_the_html_part(self, smtp_settings, smtp_classes):
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=replace(_result(), reviews=MARKUP)) is True

        _, html_part = _sent_parts(server)
        assert f">{ESCAPED_MARKUP}</td>" in html_part
        assert MARKUP not in html_part

    @pytest.mark.parametrize("line_break", ["\r\n", "\n", "\r", "\u2028"])
    def test_line_break_in_merchant_adds_no_line(self, smtp_settings, smtp_classes, line_break):
        """A merchant name cannot add a line, such as a second Link line, to the plain-text part."""
        merchant = f"Shop{line_break}Link: N/A"
        result = replace(_result(), url="https://example.com/widget", merchant=merchant)
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=result) is True

        plain, _ = _sent_parts(server)
        lines = plain.splitlines()
        assert [line for line in lines if line.startswith("Source:")] == [
            "Source: Shop Link: N/A via google_shopping"
        ]
        assert [line for line in lines if line.startswith("Link:")] == [
            "Link: https://example.com/widget"
        ]

    @pytest.mark.parametrize("scheme", ["http", "https", "HTTPS"])
    def test_http_url_is_linked_and_escaped_in_href(self, smtp_settings, smtp_classes, scheme):
        url = f'{scheme}://example.com/widget?a=1&b="2"'
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=replace(_result(), url=url)) is True

        plain, html_part = _sent_parts(server)
        assert f'href="{scheme}://example.com/widget?a=1&amp;b=&quot;2&quot;"' in html_part
        assert "Buy Now" in html_part
        assert f"Link: {url}" in plain

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:void(0)",
            "JavaScript:void(0)",
            "data:text/plain,widget",
            "ftp://example.com/widget",
            "//example.com/widget",
            "example.com/widget",
            "https://example.com/widget\r\nPrice: $0.01",
            "http://[example.com/widget",
        ],
    )
    def test_other_url_gets_no_link(self, smtp_settings, smtp_classes, url):
        """No Buy Now link and "Link: N/A" for a URL that is not a well-formed http(s) URL."""
        server = _server(smtp_classes[0])

        assert send_price_alert(item=_item(), result=replace(_result(), url=url)) is True

        plain, html_part = _sent_parts(server)
        assert "<a " not in html_part
        assert "Buy Now" not in html_part
        assert "Link: N/A" in plain
        assert url not in plain
        assert url not in html_part


class TestSmtpSettings:
    """SMTP settings: a finite default timeout, a masked password and SMTP_SECURITY's values."""

    def test_smtp_timeout_defaults_to_30_seconds(self, monkeypatch):
        monkeypatch.delenv("SMTP_TIMEOUT", raising=False)
        assert Settings(_env_file=None).smtp_timeout == 30

    def test_smtp_password_is_masked(self):
        settings = Settings(_env_file=None, smtp_password=FAKE_PASSWORD)
        assert FAKE_PASSWORD not in repr(settings)
        assert FAKE_PASSWORD not in str(settings)
        assert isinstance(settings.smtp_password, SecretStr)
        assert settings.smtp_password.get_secret_value() == FAKE_PASSWORD

    def test_smtp_security_defaults_to_tls(self, monkeypatch):
        monkeypatch.delenv("SMTP_SECURITY", raising=False)
        assert Settings(_env_file=None).smtp_security == "tls"

    def test_smtp_security_none_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("SMTP_SECURITY", "none")
        assert Settings(_env_file=None).smtp_security == "none"

    @pytest.mark.parametrize("value", ["starttls", "ssl", "off"])
    def test_invalid_smtp_security_fails_validation(self, monkeypatch, value):
        monkeypatch.setenv("SMTP_SECURITY", value)

        with pytest.raises(ValidationError, match="smtp_security"):
            Settings(_env_file=None)

    def test_app_refuses_to_start_with_invalid_smtp_security(self, tmp_path):
        """config.settings is built on import, so uvicorn cannot load the app with a bad value."""
        result = subprocess.run(
            [sys.executable, "-c", "import hunter_bargain.main"],
            cwd=tmp_path,  # no .env file here
            env={**os.environ, "SMTP_SECURITY": "starttls"},
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "validation error for Settings" in result.stderr
        assert "smtp_security" in result.stderr


# An APPRISE_URL's secret parts, for the local stand-in for ntfy: none may reach the log.
NTFY_USER = "fake-ntfy-user"
NTFY_PASSWORD = "fake-ntfy-password-for-tests"
NTFY_TOPIC = "fake-ntfy-topic-for-tests"


@dataclass
class NtfyStub:
    """A local stand-in for an ntfy server: it keeps each request and answers with `status`."""

    port: int = 0
    status: int = 200
    # Each request's path, headers and JSON body.
    requests: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)

    @property
    def apprise_url(self) -> str:
        """An APPRISE_URL for this server: Apprise's ntfy URL with a login and a topic."""
        return f"ntfy://{NTFY_USER}:{NTFY_PASSWORD}@127.0.0.1:{self.port}/{NTFY_TOPIC}"


@pytest.fixture
def ntfy(monkeypatch: pytest.MonkeyPatch) -> Iterator[NtfyStub]:
    """A local stand-in for ntfy, which Apprise publishes to with a POST of a JSON message."""
    stub = NtfyStub()

    class Handler(BaseHTTPRequestHandler):
        @override
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            stub.requests.append((self.path, dict(self.headers.items()), json.loads(body)))
            # ntfy answers with the message it published, or with its error object.
            answer = {"id": "1", "event": "message"}
            if stub.status != 200:
                answer = {"code": stub.status * 100 + 1, "http": stub.status, "error": "failed"}
            payload = json.dumps(answer).encode()
            self.send_response(stub.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        @override
        def log_message(self, *args: Any) -> None:
            """Stay quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def apprise_settings(ntfy: NtfyStub) -> Iterator[Settings]:
    """NOTIFIER=apprise with the stand-in's URL, and no SMTP settings or ALERT_RECIPIENTS."""
    fake = Settings(_env_file=None, notifier="apprise", apprise_url=ntfy.apprise_url)
    with patch("hunter_bargain.services.notifier.settings", fake):
        yield fake


def _assert_no_part_of_the_apprise_url(text: str) -> None:
    """Neither the URL's login, password and topic nor Apprise's own log lines (which name the
    ntfy server) are in `text`."""
    for part in (NTFY_USER, NTFY_PASSWORD, NTFY_TOPIC, "ntfy"):
        assert part not in text


class TestWithoutSmtp:
    """#28: while SMTP is not configured (the defaults), a price alert is written to the log
    instead of being skipped, without the recipient's address, and no SMTP connection is made."""

    def test_the_alert_is_logged_and_no_smtp_connection_is_made(self, smtp_classes, caplog):
        smtp_cls, smtp_ssl_cls = smtp_classes
        result = replace(_result(), merchant="Walmart")

        with caplog.at_level(logging.DEBUG):
            assert send_price_alert(item=_item(), result=result) is False  # not emailed

        smtp_cls.assert_not_called()
        smtp_ssl_cls.assert_not_called()
        [record] = caplog.records
        assert record.levelno == logging.WARNING
        assert record.getMessage() == (
            "SMTP not configured — price alert for item 1 logged, not emailed:"
            " price 49.99, target 60.0, source google_shopping"
        )
        assert "user@example.com" not in caplog.text

    def test_a_check_that_meets_the_target_logs_the_alert(
        self, db_session, stub_serpapi, smtp_classes, monkeypatch, caplog
    ):
        """The local stub SerpAPI finds the Widget at $9.99 on both engines; its target is $10."""
        monkeypatch.setattr(settings, "serpapi_key", SecretStr("test-key"))
        item = Item(name="Widget", notify_email="w@x.com", target_price=10.0)
        db_session.add(item)
        db_session.commit()

        with caplog.at_level(logging.INFO):
            result = run_price_check(item=item, db=db_session)

        assert (result.lowest_price, result.alert_sent) == (9.99, False)
        assert (item.last_alert_price, item.last_alerted_at) == (None, None)  # retried next check
        assert all(cls.call_count == 0 for cls in smtp_classes)
        assert (
            f"SMTP not configured — price alert for item {item.id} logged, not emailed:"
            " price 9.99, target 10.0, source google_shopping"
        ) in [r.getMessage() for r in caplog.records]
        assert "w@x.com" not in caplog.text


class TestApprise:
    """NOTIFIER=apprise: alerts and notices go to APPRISE_URL, here a local stand-in for ntfy."""

    def test_price_alert_is_published_to_the_topic(
        self, apprise_settings, ntfy, smtp_classes, caplog
    ):
        """SMTP settings and ALERT_RECIPIENTS do not matter: the alert goes to APPRISE_URL."""
        item = _item()
        item.notify_email = "someone@example.net"
        result = replace(_result(), merchant="Walmart", url="https://example.com/widget")
        low_90_days = PriceLow(
            price=45.0,
            currency="USD",
            source="bing_shopping",
            checked_at=dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC),
        )

        with caplog.at_level(logging.DEBUG):
            assert send_price_alert(item=item, result=result, low_90_days=low_90_days) is True

        [(path, headers, body)] = ntfy.requests
        assert body == {
            "topic": NTFY_TOPIC,
            "title": "Price Alert: Widget — $49.99",
            "message": (
                "Price Alert: Widget\n"
                "Price: $49.99 USD\n"
                "Target: $60.00\n"
                "Lowest in 90 days: $45.00 on 2026-09-01\n"
                "Source: Walmart via google_shopping\n"
                "Link: https://example.com/widget"
            ),
        }
        login = base64.b64encode(f"{NTFY_USER}:{NTFY_PASSWORD}".encode()).decode()
        assert (path, headers["Authorization"]) == ("/", f"Basic {login}")
        assert all(cls.call_count == 0 for cls in smtp_classes)
        assert "Sent through Apprise: 'Price Alert: Widget — $49.99'" in caplog.text
        _assert_no_part_of_the_apprise_url(caplog.text)

    def test_verify_merchant_alert_says_so_in_the_message(self, apprise_settings, ntfy):
        """#29's warning, for a price far below the check's others, is in the push message too."""
        assert send_price_alert(item=_item(), result=_result(), verify_merchant=True) is True

        [(_, _, body)] = ntfy.requests
        assert body["message"].splitlines()[:2] == [
            "Price Alert: Widget",
            "Verify merchant: this price is more than 40% below the median of this check's"
            " prices. Check the seller before you buy.",
        ]

    def test_notice_is_published_to_the_topic(self, apprise_settings, ntfy, smtp_classes):
        assert send_notice("Searches running low", "12 left.") is True

        [(_, _, body)] = ntfy.requests
        assert body == {"topic": NTFY_TOPIC, "title": "Searches running low", "message": "12 left."}
        assert all(cls.call_count == 0 for cls in smtp_classes)

    def test_refused_message_is_false_and_logged_without_the_url(
        self, apprise_settings, ntfy, caplog
    ):
        ntfy.status = 500

        with caplog.at_level(logging.DEBUG):
            assert send_price_alert(item=_item(), result=_result()) is False

        assert len(ntfy.requests) == 1
        assert "Apprise could not deliver: 'Price Alert: Widget — $49.99'" in caplog.text
        _assert_no_part_of_the_apprise_url(caplog.text)

    def test_unreachable_server_is_false_and_logged_without_the_url(
        self, ntfy, monkeypatch, caplog
    ):
        url = ntfy.apprise_url.replace(f":{ntfy.port}/", ":1/")  # nothing listens on port 1
        fake = Settings(_env_file=None, notifier="apprise", apprise_url=url)
        monkeypatch.setattr("hunter_bargain.services.notifier.settings", fake)

        with caplog.at_level(logging.DEBUG):
            assert send_notice("Searches running low", "12 left.") is False

        assert "Apprise could not deliver: 'Searches running low'" in caplog.text
        _assert_no_part_of_the_apprise_url(caplog.text)

    def test_url_apprise_cannot_use_is_false_and_not_logged(self, monkeypatch, caplog):
        url = f"nosuchservice://{NTFY_PASSWORD}@example.com/{NTFY_TOPIC}"
        fake = Settings(_env_file=None, notifier="apprise", apprise_url=url)
        monkeypatch.setattr("hunter_bargain.services.notifier.settings", fake)

        with caplog.at_level(logging.DEBUG):
            assert send_notice("Searches running low", "12 left.") is False

        assert (
            "APPRISE_URL is not a URL Apprise can send to — not sent: 'Searches running low'"
        ) in caplog.text
        _assert_no_part_of_the_apprise_url(caplog.text)

    def test_error_in_apprise_is_false_and_logged_by_type_only(
        self, apprise_settings, ntfy, monkeypatch, caplog
    ):
        def fail(self: apprise.Apprise, *args: object, **kwargs: object) -> bool:
            raise RuntimeError(f"failed: {ntfy.apprise_url}")

        monkeypatch.setattr(apprise.Apprise, "notify", fail)

        with caplog.at_level(logging.DEBUG):
            assert send_notice("Searches running low", "12 left.") is False

        assert "Apprise failed (RuntimeError) — not sent: 'Searches running low'" in caplog.text
        _assert_no_part_of_the_apprise_url(caplog.text)

    def test_apprise_and_urllib3_logs_are_dropped_whatever_log_level_is(self):
        """Their records quote APPRISE_URL or its parts at every level, DEBUG to ERROR."""
        names = ("apprise", "urllib3")
        saved = {name: logging.getLogger(name).level for name in names}
        try:
            for name in names:
                logging.getLogger(name).setLevel(logging.NOTSET)
            configure_logging("debug")
            assert all(not logging.getLogger(name).isEnabledFor(logging.CRITICAL) for name in names)
        finally:
            for name, level in saved.items():
                logging.getLogger(name).setLevel(level)

    def test_app_entry_point_keeps_the_apprise_url_out_of_the_log(self, ntfy, tmp_path):
        """A fresh interpreter imports the app as uvicorn does, with LOG_LEVEL=debug: under pytest,
        main.py's basicConfig() is a no-op; here the app's log goes to stderr."""
        script = (
            "import hunter_bargain.main\n"
            "from hunter_bargain.services.notifier import send_notice\n"
            "print(send_notice('Searches running low', '12 left.'))\n"
        )
        env = {
            **os.environ,
            "LOG_LEVEL": "debug",
            "NOTIFIER": "apprise",
            "APPRISE_URL": ntfy.apprise_url,
            "no_proxy": "127.0.0.1",
        }

        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,  # no .env here
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "True\n"
        assert len(ntfy.requests) == 1
        assert "Sent through Apprise: 'Searches running low'" in proc.stderr
        _assert_no_part_of_the_apprise_url(proc.stderr)


class TestNotifierSettings:
    """NOTIFIER selects smtp (the default) or apprise; APPRISE_URL is a secret."""

    def test_notifier_defaults_to_smtp_without_an_apprise_url(self):
        defaults = Settings(_env_file=None)
        assert (defaults.notifier, defaults.apprise_url.get_secret_value()) == ("smtp", "")

    def test_notifier_and_apprise_url_are_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER", "apprise")
        monkeypatch.setenv("APPRISE_URL", f"ntfy://127.0.0.1/{NTFY_TOPIC}")

        loaded = Settings(_env_file=None)

        assert loaded.notifier == "apprise"
        assert loaded.apprise_url.get_secret_value() == f"ntfy://127.0.0.1/{NTFY_TOPIC}"
        assert NTFY_TOPIC not in repr(loaded)
        assert NTFY_TOPIC not in str(loaded)

    def test_apprise_without_apprise_url_fails_validation(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER", "apprise")

        with pytest.raises(ValidationError, match="NOTIFIER=apprise needs APPRISE_URL"):
            Settings(_env_file=None)

    @pytest.mark.parametrize("value", ["log", "ntfy", "email"])
    def test_other_notifier_fails_validation(self, monkeypatch, value):
        monkeypatch.setenv("NOTIFIER", value)

        with pytest.raises(ValidationError, match="notifier"):
            Settings(_env_file=None)

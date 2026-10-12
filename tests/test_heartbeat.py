"""The heartbeat (#24): one GET to HEARTBEAT_URL, whose token never reaches the log.

The requests go to an httpx.MockTransport, never to the network. The app's logging setup, run
when conftest.py imported the app, holds httpx's own request log line back.
"""

import logging

import httpx
import pytest
from pydantic import SecretStr

from hunter_bargain.config import Settings, settings
from hunter_bargain.logging_config import configure_logging
from hunter_bargain.services.heartbeat import send_heartbeat

TOKEN = "fake-heartbeat-token-for-tests"
HEARTBEAT_URL = f"https://hc-ping.com/{TOKEN}"


def _transport(sent: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    """Answers every request with `status`, after adding it to `sent`."""

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status)

    return httpx.MockTransport(handle)


@pytest.fixture
def heartbeat_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "heartbeat_url", SecretStr(HEARTBEAT_URL))


def test_nothing_is_sent_while_heartbeat_url_is_empty(caplog):
    caplog.set_level(logging.DEBUG)
    sent: list[httpx.Request] = []

    assert send_heartbeat(_transport(sent)) is False
    assert sent == []
    # Off is not a failure: the heartbeat logs nothing.
    assert [r for r in caplog.records if r.name == "hunter_bargain.services.heartbeat"] == []


def test_one_get_with_a_short_timeout(heartbeat_url, caplog):
    caplog.set_level(logging.DEBUG)
    sent: list[httpx.Request] = []

    assert send_heartbeat(_transport(sent)) is True

    [request] = sent
    assert (request.method, str(request.url)) == ("GET", HEARTBEAT_URL)
    # 10 seconds to connect, and then for each read and write: the run waits for it.
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 10)
    assert "Heartbeat sent" in caplog.messages
    # httpx logs each request's URL at INFO; the app's logging setup keeps that line out.
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("status", [404, 500, 503])
def test_error_status_is_a_warning_with_the_status_only(heartbeat_url, caplog, status):
    caplog.set_level(logging.DEBUG)

    assert send_heartbeat(_transport([], status)) is False

    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
        f"Heartbeat failed: HTTP {status}"
    ]
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.UnsupportedProtocol],
    ids=lambda error: error.__name__,
)
def test_request_error_is_a_warning_with_its_type_only(heartbeat_url, caplog, error):
    """The error's text quotes the URL, as some of httpx's own do; only its type is logged."""
    caplog.set_level(logging.DEBUG)

    def fail(request: httpx.Request) -> httpx.Response:
        raise error(f"cannot reach {request.url}", request=request)

    assert send_heartbeat(httpx.MockTransport(fail)) is False

    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
        f"Heartbeat failed: {error.__name__}"
    ]
    assert TOKEN not in caplog.text


def test_heartbeat_url_is_a_secret(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_URL", HEARTBEAT_URL)

    loaded = Settings(_env_file=None)

    assert loaded.heartbeat_url.get_secret_value() == HEARTBEAT_URL
    assert TOKEN not in repr(loaded)
    assert TOKEN not in str(loaded.model_dump())


def test_httpx_and_httpcore_logs_are_capped_at_warning():
    """httpx logs each request's URL at INFO, whatever LOG_LEVEL is."""
    names = ("httpx", "httpcore")
    saved = {name: logging.getLogger(name).level for name in names}
    try:
        configure_logging("debug")
        assert {name: logging.getLogger(name).level for name in names} == dict.fromkeys(
            names, logging.WARNING
        )
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)

"""The SerpAPI key stays out of logs (#5).

No test here attaches a logging filter to pytest's caplog handler. The app's own
logging setup runs when hunter_bargain.main is imported, as uvicorn imports it,
and that setup is what keeps the key out of caplog.text.
"""

from __future__ import annotations

import importlib
import io
import json
import logging
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from pydantic import SecretStr
from serpapi import GoogleSearch
from uvicorn.logging import AccessFormatter

from hunter_bargain.config import Settings, settings
from hunter_bargain.services.engines.bing import BingShoppingEngine
from hunter_bargain.services.engines.google import GoogleShoppingEngine

# Import the app module as uvicorn does; that runs the app's logging setup.
importlib.import_module("hunter_bargain.main")

FAKE_KEY = "fake-serpapi-key-for-tests"
ENGINES = [GoogleShoppingEngine, BingShoppingEngine]


@dataclass
class StubSerpApi:
    url: str
    queries: list[dict[str, list[str]]]


def _point_client_at(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(GoogleSearch, "BACKEND", url)
    monkeypatch.setenv("no_proxy", "127.0.0.1")


@pytest.fixture
def fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load a fake key through Settings, the way the app reads SERPAPI_KEY."""
    monkeypatch.setenv("SERPAPI_KEY", FAKE_KEY)
    monkeypatch.setattr(settings, "serpapi_key", Settings(_env_file=None).serpapi_key)


@pytest.fixture
def refused_serpapi(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """SerpAPI on a 127.0.0.1 port that is bound but not listening: connections are refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        _point_client_at(monkeypatch, f"http://127.0.0.1:{sock.getsockname()[1]}")
        yield


@pytest.fixture
def stub_serpapi(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubSerpApi]:
    """A local stand-in for serpapi.com that answers every search with one result."""
    received: list[dict[str, list[str]]] = []

    class Handler(BaseHTTPRequestHandler):
        @override
        def do_GET(self) -> None:
            received.append(parse_qs(urlsplit(self.path).query))
            result = {"title": "Widget", "extracted_price": 9.99, "link": "http://127.0.0.1/w"}
            body = json.dumps({"shopping_results": [result]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, *args: Any) -> None:
            """Stay quiet: request lines carry the key."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        stub = StubSerpApi(url=f"http://127.0.0.1:{server.server_port}", queries=received)
        _point_client_at(monkeypatch, stub.url)
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_connection_error_leaves_no_key_in_logs(engine_cls, fake_key, refused_serpapi, caplog):
    caplog.set_level(logging.DEBUG)

    assert engine_cls().search("widget") == []

    assert FAKE_KEY not in caplog.text
    [error] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "ConnectionError (host 127.0.0.1)" in error.getMessage()
    assert error.exc_info is None  # no traceback


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_debug_logging_successful_search_leaves_no_key(engine_cls, fake_key, stub_serpapi, caplog):
    caplog.set_level(logging.DEBUG)  # LOG_LEVEL=debug puts the root logger at DEBUG

    results = engine_cls().search("widget")

    assert [r.price for r in results] == [9.99]
    assert stub_serpapi.queries[0]["api_key"] == [FAKE_KEY]  # the key still reaches SerpAPI
    assert FAKE_KEY not in caplog.text


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_urllib3_request_line_is_redacted(engine_cls, fake_key, stub_serpapi, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="urllib3")  # lift the app's WARNING cap on urllib3

    assert engine_cls().search("widget")

    assert FAKE_KEY not in caplog.text
    assert '"GET /search?' in caplog.text  # urllib3 logged the request line
    assert "api_key=***" in caplog.text


@pytest.mark.parametrize("logger_name", ["urllib3.connectionpool", "apscheduler.executors.default"])
def test_third_party_records_are_redacted(logger_name, caplog):
    caplog.set_level(logging.DEBUG, logger=logger_name)
    log = logging.getLogger(logger_name)
    path = f"/search?engine=google_shopping&api_key={FAKE_KEY}&num=10"
    error = requests.ConnectionError(f"Max retries exceeded with url: {path}")

    log.debug(f"GET {path}")  # in the message
    log.debug('"GET %s HTTP/1.1" %s', path, 200)  # in a string argument, as urllib3 logs it
    log.debug("GET /search?api_key=%s&num=10", FAKE_KEY)  # the value alone as an argument
    log.warning("request failed: %s", error)  # in a non-string argument
    try:
        raise error
    except requests.ConnectionError:
        log.exception("search failed")  # in the traceback

    assert FAKE_KEY not in caplog.text
    assert caplog.text.count("api_key=***") == 5


def test_uvicorn_style_access_log_is_redacted_and_still_formats():
    # uvicorn's access logger does not propagate to root: it writes through its own
    # handler, whose AccessFormatter unpacks record.args.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    fmt = '%(client_addr)s - "%(request_line)s" %(status_code)s'
    handler.setFormatter(AccessFormatter(fmt, use_colors=False))
    log = logging.getLogger("tests.access")
    log.addHandler(handler)
    log.propagate = False
    try:
        path = f"/health?api_key={FAKE_KEY}"
        log.warning('%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "GET", path, "1.1", 200)
    finally:
        log.removeHandler(handler)
        log.propagate = True

    assert stream.getvalue() == '127.0.0.1:50000 - "GET /health?api_key=*** HTTP/1.1" 200 OK\n'


def test_serpapi_key_is_a_secret(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", FAKE_KEY)

    loaded = Settings(_env_file=None)

    assert isinstance(loaded.serpapi_key, SecretStr)
    assert loaded.serpapi_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(loaded)


def test_app_entry_point_keeps_key_out_of_logs(stub_serpapi, tmp_path):
    # A fresh interpreter imports the app as uvicorn does, with LOG_LEVEL=debug. Under
    # pytest the root logger already has handlers, so main.py's basicConfig() is a no-op;
    # here it runs and writes to stderr.
    script = (
        "import logging, sys\n"
        "from serpapi import GoogleSearch\n"
        "import hunter_bargain.main\n"
        "from hunter_bargain.services.engines.google import GoogleShoppingEngine\n"
        "GoogleSearch.BACKEND = sys.argv[1]\n"
        "GoogleShoppingEngine().search('widget')\n"
        "logging.getLogger('urllib3').setLevel(logging.DEBUG)\n"
        "GoogleShoppingEngine().search('widget')\n"
    )
    env = {**os.environ, "LOG_LEVEL": "debug", "SERPAPI_KEY": FAKE_KEY, "no_proxy": "127.0.0.1"}

    proc = subprocess.run(
        [sys.executable, "-c", script, stub_serpapi.url],
        cwd=tmp_path,  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert [q["api_key"] for q in stub_serpapi.queries] == [[FAKE_KEY], [FAKE_KEY]]
    assert proc.stderr.count("Google Shopping: found 1 results") == 2
    assert FAKE_KEY not in proc.stderr + proc.stdout
    assert proc.stderr.count("api_key=***") == 1  # urllib3's request line, once uncapped

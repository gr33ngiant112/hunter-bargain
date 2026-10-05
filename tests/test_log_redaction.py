"""The SerpAPI key stays out of logs and engine errors (#5).

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
import subprocess
import sys
from unittest.mock import patch

import pytest
import requests
from pydantic import SecretStr
from uvicorn.logging import AccessFormatter

from hunter_bargain.config import Settings, settings
from hunter_bargain.models import Item
from hunter_bargain.services.engines import base
from hunter_bargain.services.engines.bing import BingShoppingEngine
from hunter_bargain.services.engines.google import GoogleShoppingEngine
from hunter_bargain.services.searcher import run_price_check

# Import the app module as uvicorn does; that runs the app's logging setup.
importlib.import_module("hunter_bargain.main")

FAKE_KEY = "fake-serpapi-key-for-tests"
ENGINES = [GoogleShoppingEngine, BingShoppingEngine]

# SerpAPI's documented error responses, https://serpapi.com/api-status-and-error-codes (fetched
# 2026-10-05): "Error for invalid API key" and "Error for no searches remaining".
INVALID_KEY = (
    401,
    {"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"},
)
NO_SEARCHES_LEFT = (429, {"error": "Your account has run out of searches."})


@pytest.fixture
def fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load a fake key through Settings, the way the app reads SERPAPI_KEY."""
    monkeypatch.setenv("SERPAPI_KEY", FAKE_KEY)
    monkeypatch.setattr(settings, "serpapi_key", Settings(_env_file=None).serpapi_key)


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_connection_error_leaves_no_key_in_logs(engine_cls, fake_key, refused_serpapi, caplog):
    caplog.set_level(logging.DEBUG)

    with pytest.raises(base.EngineError) as raised:
        engine_cls().search("widget")

    assert str(raised.value) == "request failed (ConnectionError)"
    assert FAKE_KEY not in caplog.text
    [error] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "ConnectionError (host 127.0.0.1)" in error.getMessage()
    assert error.exc_info is None  # no traceback


@pytest.mark.parametrize(("status", "body"), [INVALID_KEY, NO_SEARCHES_LEFT], ids=["401", "429"])
@pytest.mark.parametrize("engine_cls", ENGINES)
def test_rejected_key_or_quota_leaves_no_key_in_logs_or_engine_errors(
    engine_cls, status, body, fake_key, stub_serpapi, db_session, caplog
):
    """SerpAPI's 401 and 429 go through requests to a local stub, whose URL carries the key."""
    caplog.set_level(logging.DEBUG)
    stub_serpapi.status, stub_serpapi.body = status, json.dumps(body)
    item = Item(name="Widget", notify_email="w@x.com")
    db_session.add(item)
    db_session.commit()

    with patch("hunter_bargain.services.searcher._ENGINES", [engine_cls()]):
        result = run_price_check(item=item, db=db_session)

    assert stub_serpapi.queries[0]["api_key"] == [FAKE_KEY]  # the request URL had the key
    [engine_error] = result.engine_errors
    assert engine_error.startswith(f"{engine_cls().name}: HTTP {status}, ")
    assert engine_error.endswith(f": {body['error']}")
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in result.model_dump_json()  # the API's response body


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
        "from serpapi import SerpApiClient\n"
        "import hunter_bargain.main\n"
        "from hunter_bargain.services.engines.google import GoogleShoppingEngine\n"
        "SerpApiClient.BACKEND = sys.argv[1]\n"
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

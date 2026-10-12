"""The CLI against the real API (#20): hb's commands go over HTTP to the app, which uvicorn serves
on a loopback port. A path, method, status code or field that the CLI and the API disagree on
fails here. The tests in test_cli.py stub httpx.request and check the CLI's own handling."""

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from unittest.mock import patch

import pytest
import uvicorn
from click.testing import CliRunner
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from hunter_bargain.cli import cli
from hunter_bargain.config import settings
from hunter_bargain.db import get_db
from hunter_bargain.main import app
from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.services import scheduler as scheduler_module

EMAIL = "me@example.com"


@pytest.fixture
def api_url(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The app, served by uvicorn on a free 127.0.0.1 port, on the test database.

    As with conftest.py's client fixture, the app's startup neither migrates a database nor
    starts the scheduler. A check of all items opens its sessions on the test database too.
    """
    monkeypatch.setattr(settings, "alert_recipients", [EMAIL])

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    sessions = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, ws="none"))
    with ExitStack() as stack:
        for target in ("init_db", "start_scheduler", "stop_scheduler"):
            stack.enter_context(patch(f"hunter_bargain.main.{target}"))
        stack.enter_context(patch("hunter_bargain.services.scheduler.SessionLocal", sessions))
        sock = stack.enter_context(socket.socket())
        sock.bind(("127.0.0.1", 0))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    pytest.fail("setup: uvicorn did not start")
                time.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
    app.dependency_overrides.clear()


@pytest.fixture
def hb(api_url: str) -> Callable[..., str]:
    """Run an hb command against the served app and return its output; it must exit 0."""
    runner = CliRunner()

    def run(*args: str, input: str | None = None) -> str:
        result = runner.invoke(cli, ["--url", api_url, *args], input=input)
        assert result.exit_code == 0, result.output
        return result.output

    return run


def _wait_for_the_run_to_end() -> None:
    """Return once no check of all items holds the run lock (fail after 5 s)."""
    if not scheduler_module._run_lock.acquire(timeout=5):
        pytest.fail("the check of all items did not end within 5 s")
    scheduler_module._run_lock.release()


def test_add_list_update_check_and_remove_an_item(hb, db_session, stub_serpapi, monkeypatch):
    """Each command's request reaches its route, and the CLI reads the API's answer."""
    monkeypatch.setattr(settings, "serpapi_key", SecretStr("test-key"))  # searches go to the stub

    assert "No items tracked yet." in hb("ls")

    output = hb("add", "Widget", "-e", EMAIL, "-t", "5")
    item = db_session.query(Item).one()
    assert f"Added item #{item.id}: Widget" in output

    output = hb("ls")
    assert "Tracking 1 item(s):" in output
    assert f"[{item.id}]  Widget  |  target: $5.00  |  keywords: —  |  email: {EMAIL}" in output

    output = hb("update", str(item.id), "-t", "8.5", "-k", "widget")
    assert f"Updated item #{item.id}:" in output
    assert "target: $8.50  |  keywords: widget" in output

    # The stub answers both engines with one $9.99 Widget row; the target is below it.
    with patch("hunter_bargain.services.searcher.send_price_alert") as send:
        output = hb("check", str(item.id))
        assert "  Widget: $9.99 (" in output
        assert "— 2 result(s)" in output
        assert db_session.query(PriceRecord).filter_by(item_id=item.id).count() == 2

        output = hb("check")
        assert "Started a price check of all items in the background." in output
        _wait_for_the_run_to_end()
        assert db_session.query(PriceRecord).filter_by(item_id=item.id).count() == 4
    send.assert_not_called()

    # Without -y, rm reads the item first and asks.
    output = hb("rm", str(item.id), input="y\n")
    assert f"Remove 'Widget' (#{item.id})?" in output
    assert f"Removed item #{item.id}." in output
    assert db_session.query(Item).count() == 0

    assert "No items tracked yet." in hb("ls")

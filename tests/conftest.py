"""Shared test fixtures."""

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from serpapi import SerpApiClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hunter_bargain.config import settings
from hunter_bargain.db import Base, create_db_engine, get_db
from hunter_bargain.main import app

# notify_email values the API tests post. Item create/update return 422 for an address outside
# ALERT_RECIPIENTS, so the client fixture allows exactly these.
API_TEST_RECIPIENTS = [
    "user@example.com",
    "a@x.com",
    "b@x.com",
    "d@x.com",
    "g@x.com",
    "u@x.com",
    "w@x.com",
]


@pytest.fixture
def db_session():
    """In-memory SQLite database session for tests.

    The app's engine settings, so foreign keys are enforced as in production. Uses StaticPool
    so that all threads share the same in-memory database connection — required because
    FastAPI's TestClient dispatches requests in a worker thread via anyio.
    """
    engine = create_db_engine("sqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = test_session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """FastAPI test client with overridden DB dependency.

    Patches lifespan hooks (init_db, start_scheduler, stop_scheduler) so the
    test client doesn't touch the real database or spin up background jobs.
    ALERT_RECIPIENTS is set to API_TEST_RECIPIENTS; a test can narrow it.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "alert_recipients", list(API_TEST_RECIPIENTS))

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with ExitStack() as stack:
        stack.enter_context(patch("hunter_bargain.main.init_db"))
        stack.enter_context(patch("hunter_bargain.main.start_scheduler"))
        stack.enter_context(patch("hunter_bargain.main.stop_scheduler"))
        client = TestClient(app)
        stack.enter_context(client)
        yield client

    app.dependency_overrides.clear()


# A shopping_results row with fields both engines' documented rows have; a row needs a USD price
# string to count.
WIDGET_ROW = {"title": "Widget", "price": "$9.99", "extracted_price": 9.99}


@dataclass
class StubSerpApi:
    """A local stand-in for serpapi.com: every search gets this status and body."""

    url: str
    queries: list[dict[str, list[str]]]
    status: int = 200
    body: str = json.dumps({"shopping_results": [WIDGET_ROW]})
    content_type: str = "application/json"


def _point_client_at(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    # SerpApiClient's subclasses (GoogleSearch, ...) read BACKEND from it too.
    monkeypatch.setattr(SerpApiClient, "BACKEND", url)
    monkeypatch.setenv("no_proxy", "127.0.0.1")


@pytest.fixture
def refused_serpapi(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """SerpAPI on a 127.0.0.1 port that is bound but not listening: connections are refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        _point_client_at(monkeypatch, f"http://127.0.0.1:{sock.getsockname()[1]}")
        yield


@pytest.fixture(autouse=True)
def _no_live_serpapi(refused_serpapi: None) -> None:
    """No test reaches serpapi.com: a search that no stub or mock answers is refused locally."""


@pytest.fixture
def stub_serpapi(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubSerpApi]:
    """A local stand-in for serpapi.com that answers every search with one result.

    A test can change the stub's status and body, e.g. to SerpAPI's documented error responses.
    """
    stub = StubSerpApi(url="", queries=[])

    class Handler(BaseHTTPRequestHandler):
        @override
        def do_GET(self) -> None:
            stub.queries.append(parse_qs(urlsplit(self.path).query))
            body = stub.body.encode()
            self.send_response(stub.status)
            self.send_header("Content-Type", stub.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, *args: Any) -> None:
            """Stay quiet: request lines carry the key."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.url = f"http://127.0.0.1:{server.server_port}"
    # shutdown() waits for the server's next poll, so poll often to keep each test's teardown short.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        _point_client_at(monkeypatch, stub.url)
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

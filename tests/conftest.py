"""Shared test fixtures."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hunter_bargain.config import settings
from hunter_bargain.db import Base, get_db
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

    Uses StaticPool so that all threads share the same in-memory database
    connection — required because FastAPI's TestClient dispatches requests
    in a worker thread via anyio.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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

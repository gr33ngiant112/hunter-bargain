"""SerpAPI's searches left (#24): the Account API, read through the local stub SerpAPI, a warning
when they run low, and neither the API key nor the account's email address in a log line, a
database row or an email."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from conftest import ACCOUNT_ANSWER
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from hunter_bargain.config import settings
from hunter_bargain.models import Item
from hunter_bargain.services.engines.base import EngineError, SerpApiAccount, fetch_serpapi_account
from hunter_bargain.services.quota import check_searches_left
from hunter_bargain.services.scheduler import _scheduled_price_check

FAKE_KEY = "fake-serpapi-key-for-quota-tests"
ACCOUNT_EMAIL = "serpapi-account-holder@example.org"
QUOTA_LOGGER = "hunter_bargain.services.quota"


def _answer(**changes: object) -> tuple[int, str]:
    """The documented account answer, scrubbed to the test's key and a made-up address, with
    `changes`; a field changed to ... (Ellipsis) is left out."""
    answer = {**ACCOUNT_ANSWER, "api_key": FAKE_KEY, "account_email": ACCOUNT_EMAIL, **changes}
    return 200, json.dumps({key: value for key, value in answer.items() if value is not ...})


@pytest.fixture
def serpapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "serpapi_key", SecretStr(FAKE_KEY))


@pytest.fixture
def notices():
    """send_notice as the quota check calls it, patched: its (subject, text) calls."""
    with patch("hunter_bargain.services.quota.send_notice", return_value=True) as send:
        yield send


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# The Account API


def test_the_documented_answer_gives_its_five_fields(stub_serpapi):
    assert fetch_serpapi_account(FAKE_KEY) == SerpApiAccount(
        total_searches_left=5958,
        this_month_usage=24042,
        searches_per_month=30000,
        plan_renewal_date="2026-11-12",
        account_rate_limit_per_hour=6000,
    )
    # One request, to the documented endpoint, with the key as its only parameter.
    assert stub_serpapi.requested == ["/account.json"]
    assert stub_serpapi.queries == [{"api_key": [FAKE_KEY]}]


def test_values_that_are_not_counts_or_dates_are_left_out(stub_serpapi):
    """A count must be an integer of 0 or more, the renewal a date: third-party text that goes
    into a log line and an email. A credit account has no renewal date (null)."""
    stub_serpapi.paths["/account.json"] = _answer(
        total_searches_left="5958",
        this_month_usage=True,
        searches_per_month=-1,
        plan_renewal_date="2026-11-12\nInjected line",
        account_rate_limit_per_hour=1.5,
    )
    assert fetch_serpapi_account(FAKE_KEY) == SerpApiAccount(None, None, None, None, None)

    stub_serpapi.paths["/account.json"] = _answer(plan_renewal_date=None)
    assert fetch_serpapi_account(FAKE_KEY).plan_renewal_date is None


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            401,
            '{"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}',
            "HTTP 401, SerpAPI rejected the API key: Invalid API key. Your API key should be here:"
            " https://serpapi.com/manage-api-key",
        ),
        (503, "{}", "HTTP 503, server error, try again later"),
        (502, "<html><body>Bad Gateway</body></html>", "HTTP 502, server error, try again later"),
        (200, "[]", "HTTP 200, the response is not a JSON object"),
    ],
    ids=["401", "503", "502-html", "200-not-an-object"],
)
def test_error_answer_raises_engine_error(stub_serpapi, status, body, expected):
    stub_serpapi.paths["/account.json"] = (status, body)

    with pytest.raises(EngineError) as raised:
        fetch_serpapi_account(FAKE_KEY)

    assert str(raised.value) == expected


def test_refused_connection_raises_engine_error(refused_serpapi):
    with pytest.raises(EngineError) as raised:
        fetch_serpapi_account(FAKE_KEY)

    assert str(raised.value) == "request failed (ConnectError)"
    assert FAKE_KEY not in str(raised.value)


# The warning


@pytest.mark.parametrize(("left", "warned"), [(7, True), (8, False), (5958, False)])
def test_warning_below_twice_the_searches_of_a_run(
    stub_serpapi, serpapi_key, notices, caplog, left, warned
):
    """A run made 4 searches: fewer than 8 left is a warning, logged and emailed."""
    stub_serpapi.paths["/account.json"] = _answer(
        total_searches_left=left, this_month_usage=30000 - left
    )
    caplog.set_level(logging.INFO, logger=QUOTA_LOGGER)

    check_searches_left(4)

    message = (
        f"SerpAPI searches are running low: {left} left, and a check of all items makes 4 (used"
        f" this month: {30000 - left} of 30000; plan renews: 2026-11-12; hourly limit: 6000)."
    )
    if warned:
        assert _warnings(caplog) == [message]
        notices.assert_called_once_with(
            f"hunter-bargain: SerpAPI searches running low ({left} left)", message
        )
    else:
        assert _warnings(caplog) == []
        assert f"SerpAPI searches left: {left}" in caplog.messages
        notices.assert_not_called()


def test_counts_missing_from_the_answer_show_as_n_a(stub_serpapi, serpapi_key, notices, caplog):
    """A credit account has no plan renewal date; here the monthly counts are missing too."""
    stub_serpapi.paths["/account.json"] = _answer(
        total_searches_left=0,
        plan_renewal_date=None,
        this_month_usage=...,
        searches_per_month=...,
        account_rate_limit_per_hour=...,
    )

    check_searches_left(2)

    assert _warnings(caplog) == [
        "SerpAPI searches are running low: 0 left, and a check of all items makes 2 (used this"
        " month: n/a of n/a; plan renews: n/a; hourly limit: n/a)."
    ]
    notices.assert_called_once()


def test_nothing_is_read_without_a_key(stub_serpapi, notices):
    check_searches_left(4)

    assert stub_serpapi.requested == []
    notices.assert_not_called()


def test_nothing_is_read_after_a_run_that_made_no_search(stub_serpapi, serpapi_key, notices):
    check_searches_left(0)

    assert stub_serpapi.requested == []
    notices.assert_not_called()


def test_failed_account_check_is_a_warning_without_an_email(
    stub_serpapi, serpapi_key, notices, caplog
):
    stub_serpapi.paths["/account.json"] = (
        429,
        '{"error": "Your account has run out of searches."}',
    )

    check_searches_left(4)

    assert _warnings(caplog) == [
        "SerpAPI account check failed: HTTP 429, SerpAPI searches used up or hourly limit"
        " reached: Your account has run out of searches."
    ]
    notices.assert_not_called()


def test_answer_without_searches_left_is_a_warning_without_an_email(
    stub_serpapi, serpapi_key, notices, caplog
):
    stub_serpapi.paths["/account.json"] = _answer(total_searches_left=...)

    check_searches_left(4)

    assert _warnings(caplog) == [
        "SerpAPI account check failed: the answer has no total_searches_left"
    ]
    notices.assert_not_called()


# No log line, database row or email holds the key or the account's email address.


def _smtp_server(smtp_cls: MagicMock) -> MagicMock:
    server = MagicMock()
    smtp_cls.return_value.__enter__ = MagicMock(return_value=server)
    smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
    return server


def test_a_low_quota_run_keeps_the_key_and_account_email_out_of_logs_rows_and_email(
    db_session, stub_serpapi, monkeypatch, caplog
):
    """A daily run against the local stub SerpAPI: both engines find the Widget at $9.99, under
    its $10 target, and the account, whose answer echoes the key and its email address, has 3
    searches left, fewer than twice the run's 2. The price alert and the warning are emailed
    through SMTP_SECURITY=none (as to Mailpit), to an SMTP server that is a mock."""
    monkeypatch.setattr(settings, "serpapi_key", SecretStr(FAKE_KEY))
    monkeypatch.setattr(settings, "smtp_security", "none")
    monkeypatch.setattr(settings, "smtp_host", "mailpit")
    monkeypatch.setattr(settings, "email_from", "hunter-bargain@localhost")
    monkeypatch.setattr(settings, "alert_recipients", ["owner@example.com"])
    stub_serpapi.paths["/account.json"] = _answer(total_searches_left=3, this_month_usage=29997)
    db_session.add(Item(name="Widget", notify_email="owner@example.com", target_price=10.0))
    db_session.commit()
    caplog.set_level(logging.DEBUG)
    sessions = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)

    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", sessions),
        patch("hunter_bargain.services.notifier.smtplib.SMTP") as smtp_cls,
    ):
        server = _smtp_server(smtp_cls)
        _scheduled_price_check()

    # The account was read with the key, and its searches were low.
    assert {"api_key": [FAKE_KEY]} in stub_serpapi.queries
    assert stub_serpapi.requested.count("/account.json") == 1
    assert any(m.startswith("SerpAPI searches are running low: 3 left") for m in _warnings(caplog))
    emails = [call.args[0].as_string() for call in server.send_message.call_args_list]
    assert len(emails) == 2  # the price alert and the warning
    assert "Subject: hunter-bargain: SerpAPI searches running low (3 left)" in emails[1]

    db_session.expire_all()
    tables = db_session.scalars(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
    rows = [
        str(row)
        for table in list(tables)
        for row in db_session.execute(text(f"SELECT * FROM {table}"))
    ]
    assert rows  # items, price records and the run
    for secret in (FAKE_KEY, ACCOUNT_EMAIL):
        assert secret not in caplog.text
        assert not [r for r in caplog.records if secret in r.getMessage()]
        assert not [row for row in rows if secret in row]
        assert not [email for email in emails if secret in email]

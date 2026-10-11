"""Tests for the settings: ALERT_RECIPIENTS parsing, startup validation and matching, and the
ranges of the alert policy settings."""

import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from hunter_bargain.config import Settings


def test_alert_recipients_are_comma_separated(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS", " owner@example.com , Partner@Example.ORG ,")

    # Spaces and empty entries are dropped; the domain is normalised to lower case.
    assert Settings(_env_file=None).alert_recipients == ["owner@example.com", "Partner@example.org"]


def test_alert_recipients_default_to_empty(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    settings = Settings(_env_file=None)

    assert settings.alert_recipients == []
    assert settings.is_alert_recipient("owner@example.com") is False


def test_invalid_alert_recipient_fails_validation(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS", "owner@example.com,not-an-email")

    with pytest.raises(ValidationError, match="alert_recipients"):
        Settings(_env_file=None)


def test_app_refuses_to_start_with_invalid_alert_recipient(tmp_path):
    """config.settings is built on import, so uvicorn cannot load the app with a bad address."""
    result = subprocess.run(
        [sys.executable, "-c", "import hunter_bargain.main"],
        cwd=tmp_path,  # no .env file here
        env={**os.environ, "ALERT_RECIPIENTS": "owner@example.com,not-an-email"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "validation error for Settings" in result.stderr
    assert "alert_recipients.1" in result.stderr


def test_is_alert_recipient_ignores_ascii_case_only():
    settings = Settings(_env_file=None, alert_recipients=["kate@example.com"])

    assert settings.is_alert_recipient("kate@example.com") is True
    assert settings.is_alert_recipient("Kate@EXAMPLE.com") is True
    assert settings.is_alert_recipient("kate@example.net") is False
    assert settings.is_alert_recipient("kate2@example.com") is False
    # U+212A KELVIN SIGN lower-cases to ASCII "k"; Unicode case mapping must not make it match.
    assert settings.is_alert_recipient("Kate@example.com") is False


def test_env_file_may_hold_docker_compose_variables(tmp_path, monkeypatch):
    """.env is shared with Docker Compose, whose own variables are not app settings."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HB_BIND_ADDR=127.0.0.1\nAPP_PORT=8000\nAPP_HOST=0.0.0.0\nLOG_LEVEL=debug\n"
    )

    settings = Settings(_env_file=env_file)

    assert settings.log_level == "debug"


def test_alert_policy_defaults(monkeypatch):
    """Any new low, or 7 days after the last alert (#9)."""
    monkeypatch.delenv("ALERT_COOLDOWN_DAYS", raising=False)
    monkeypatch.delenv("ALERT_MIN_DROP_PCT", raising=False)
    settings = Settings(_env_file=None)

    assert (settings.alert_cooldown_days, settings.alert_min_drop_pct) == (7.0, 0.0)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("ALERT_COOLDOWN_DAYS", "0", 0.0),
        ("ALERT_COOLDOWN_DAYS", "1.5", 1.5),
        ("ALERT_MIN_DROP_PCT", "0", 0.0),
        ("ALERT_MIN_DROP_PCT", "99.5", 99.5),
    ],
)
def test_alert_policy_is_read_from_the_environment(monkeypatch, name, value, expected):
    monkeypatch.setenv(name, value)

    assert getattr(Settings(_env_file=None), name.lower()) == expected


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ALERT_COOLDOWN_DAYS", "-1"),
        ("ALERT_COOLDOWN_DAYS", "nan"),
        ("ALERT_COOLDOWN_DAYS", "inf"),
        ("ALERT_MIN_DROP_PCT", "-0.5"),
        ("ALERT_MIN_DROP_PCT", "100"),
        ("ALERT_MIN_DROP_PCT", "nan"),
    ],
)
def test_out_of_range_alert_policy_fails_validation(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert [error["loc"] for error in excinfo.value.errors()] == [(name.lower(),)]

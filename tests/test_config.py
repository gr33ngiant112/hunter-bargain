"""Tests for the ALERT_RECIPIENTS setting: parsing, startup validation and matching."""

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

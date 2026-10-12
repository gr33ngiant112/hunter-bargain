"""The tests run apart from the developer's setup (#20): conftest.py gives each test the default
settings, whatever .env or the environment holds, and blocks connections to other machines."""

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from hunter_bargain.config import Settings, settings

# A developer's .env and environment, each setting in them other than its default.
BOGUS_ENV_FILE = """\
DATABASE_URL=sqlite:////nonexistent/hunter_bargain.db
SMTP_HOST=smtp.bogus.invalid
SMTP_USER=bogus@example.com
EMAIL_FROM=bogus@example.com
ALERT_RECIPIENTS=bogus@example.com
SERPAPI_KEY=bogus-key
"""
BOGUS_ENVIRONMENT = {"SMTP_PORT": "2525", "PRICE_CHECK_TZ": "Europe/Berlin", "LOG_LEVEL": "error"}


def test_settings_are_the_defaults():
    """The next test runs this one where .env and the environment hold other values."""
    defaults = Settings(_env_file=None)
    assert settings.model_dump() == defaults.model_dump()
    # The log levels main.py's configure_logging sets for the default LOG_LEVEL.
    level = getattr(logging, defaults.log_level.upper())
    assert logging.getLogger().level == level
    assert logging.getLogger("httpx").level == max(level, logging.WARNING)


def test_a_local_env_file_and_environment_do_not_reach_the_tests(tmp_path):
    (tmp_path / ".env").write_text(BOGUS_ENV_FILE)
    test = f"{Path(__file__).resolve()}::test_settings_are_the_defaults"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test],
        cwd=tmp_path,  # the app reads .env from the working directory
        env={**os.environ, **BOGUS_ENVIRONMENT},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_a_connection_to_another_machine_is_blocked():
    # 192.0.2.1 is in TEST-NET-1 (RFC 5737), reserved for documentation.
    with pytest.raises(RuntimeError, match="tests may connect only to loopback"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)


def test_an_invalid_env_file_does_not_reach_the_tests(tmp_path):
    """#33: the tests never read .env, so one the app could not start with does not stop them."""
    (tmp_path / ".env").write_text("SMTP_PORT=notanumber\nSERPAPI_KEY=bogus-key\n")
    test = f"{Path(__file__).resolve()}::test_settings_are_the_defaults"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout

"""A fresh clone starts (#13): the real app runs from an empty directory, with or without .env."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hunter_bargain import db
from hunter_bargain.config import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# Start the real app with its lifespan (init_db and the scheduler), then ask for /health.
START_APP = """
from fastapi.testclient import TestClient
from hunter_bargain.main import app

with TestClient(app) as client:
    assert client.get("/health").json() == {"status": "ok"}
"""


def _start_app(cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run START_APP in cwd. The app's own settings come only from env and cwd's .env file."""
    settings_names = {name.upper() for name in Settings.model_fields}
    clean = {key: value for key, value in os.environ.items() if key.upper() not in settings_names}
    return subprocess.run(
        [sys.executable, "-c", START_APP],
        cwd=cwd,
        env={**clean, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_fresh_clone_starts_with_the_default_database_url(tmp_path):
    """The default DATABASE_URL points into ./data, which a fresh clone does not have."""
    result = _start_app(tmp_path)  # no .env file and no data directory here

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "data" / "hunter_bargain.db").is_file()


def test_app_starts_with_env_file_copied_from_env_example(tmp_path):
    """The README's `cp .env.example .env`: that file also holds Docker Compose's variables."""
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env")

    result = _start_app(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "data" / "hunter_bargain.db").is_file()


def test_env_example_holds_no_credentials_and_sends_alerts_to_mailpit(monkeypatch):
    """A .env copied from .env.example is safe in the checkout (#16): no SerpAPI key, no SMTP
    login, and alerts go to the dev profile's Mailpit service without TLS."""
    settings_names = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in settings_names:
            monkeypatch.delenv(key)

    settings = Settings(_env_file=REPO_ROOT / ".env.example")

    assert settings.serpapi_key.get_secret_value() == ""
    assert settings.heartbeat_url.get_secret_value() == ""  # it holds the monitor's token
    assert settings.smtp_user == ""
    assert settings.smtp_password.get_secret_value() == ""
    assert settings.smtp_host == "mailpit"
    assert settings.smtp_port == 1025
    assert settings.smtp_security == "none"


def test_database_directory_is_created_for_an_absolute_sqlite_path(tmp_path):
    db_file = tmp_path / "nested" / "dir" / "hb.db"

    result = _start_app(tmp_path, DATABASE_URL=f"sqlite:///{db_file}")

    assert result.returncode == 0, result.stderr
    assert db_file.is_file()


# #33: importing the app reads no settings; the app reads them as it starts.
IMPORT_THE_APP = """
import hunter_bargain.main
from hunter_bargain import config, db

# Neither the settings nor the database engine have been made.
assert config.get_settings.cache_info().currsize == 0, "the settings were read"
assert db.get_engine.cache_info().currsize == 0, "the engine was made"
"""


def test_importing_the_app_reads_no_env_file(tmp_path):
    """An invalid .env stops the app when it starts, not when something imports it."""
    (tmp_path / ".env").write_text("SMTP_PORT=notanumber\n")
    settings_names = {name.upper() for name in Settings.model_fields}
    clean = {key: value for key, value in os.environ.items() if key.upper() not in settings_names}

    imported = subprocess.run(
        [sys.executable, "-c", IMPORT_THE_APP],
        cwd=tmp_path,
        env=clean,
        capture_output=True,
        text=True,
        timeout=60,
    )
    started = _start_app(tmp_path)

    assert imported.returncode == 0, imported.stderr
    assert started.returncode != 0
    assert "validation error for Settings" in started.stderr
    assert "smtp_port" in started.stderr


def test_the_engine_and_sessions_are_made_on_first_use_from_database_url(tmp_path, monkeypatch):
    """SessionLocal, the migrations' default bind and get_engine share one engine, made when
    first needed from the DATABASE_URL then in force."""
    db_file = tmp_path / "hb.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_file}")
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
    try:
        with db.SessionLocal() as session:
            engine = session.get_bind()
        assert engine is db.get_engine()
        assert db.alembic_config().attributes["engine"] is engine
        assert engine.url.database == str(db_file)
        engine.dispose()
    finally:
        db.get_engine.cache_clear()
        db._session_factory.cache_clear()

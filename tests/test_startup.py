"""A fresh clone starts (#13): the real app runs from an empty directory, with or without .env."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hunter_bargain.config import Settings

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


def test_database_directory_is_created_for_an_absolute_sqlite_path(tmp_path):
    db_file = tmp_path / "nested" / "dir" / "hb.db"

    result = _start_app(tmp_path, DATABASE_URL=f"sqlite:///{db_file}")

    assert result.returncode == 0, result.stderr
    assert db_file.is_file()

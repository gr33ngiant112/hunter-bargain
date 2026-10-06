"""One version source (#17): the package metadata, the API docs and the CHANGELOG agree."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import hunter_bargain
from hunter_bargain.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_reads_the_version_from_the_package():
    """pyproject.toml holds no version of its own: hatchling reads __version__ at build time."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/hunter_bargain/__init__.py"


def test_package_metadata_and_api_docs_show_the_package_version():
    # The installed metadata comes from the last install, which CI does from this checkout.
    assert version("hunter-bargain") == hunter_bargain.__version__
    assert app.version == hunter_bargain.__version__
    assert app.openapi()["info"]["version"] == hunter_bargain.__version__


def test_newest_changelog_release_is_the_package_version():
    """A version bump comes with its CHANGELOG section; [Unreleased] is not a release."""
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", changelog, re.MULTILINE)

    assert releases, "CHANGELOG.md names no released version"
    assert releases[0] == hunter_bargain.__version__

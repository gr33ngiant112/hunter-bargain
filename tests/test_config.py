"""Tests for the settings: ALERT_RECIPIENTS parsing, startup validation and matching, and the
ranges of the alert policy settings."""

import pytest
from pydantic import ValidationError
from test_startup import _start_app

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
    """The app reads its settings as it starts, so it does not start with a bad address."""
    result = _start_app(tmp_path, ALERT_RECIPIENTS="owner@example.com,not-an-email")  # no .env

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


# #29: which merchants' prices may alert, and when a price is flagged "verify merchant".

KELVIN_KMART = "Kmart"  # "Kmart" spelled with U+212A KELVIN SIGN, which lower-cases to "k"


def test_merchant_lists_are_comma_separated_and_empty_by_default(monkeypatch):
    monkeypatch.setenv("MERCHANT_DENYLIST", " Scam Shop , ,Too  Good   Deals,")
    monkeypatch.delenv("MERCHANT_ALLOWLIST", raising=False)

    settings = Settings(_env_file=None)

    assert settings.merchant_denylist == ["Scam Shop", "Too  Good   Deals"]
    assert settings.merchant_allowlist == []


def test_without_lists_every_listing_may_alert():
    settings = Settings(_env_file=None)

    for merchant in ("Walmart", "Scam Shop", None, ""):
        assert settings.merchant_may_alert(merchant) is True


def test_denylist_matches_whole_names_ignoring_ascii_case_and_extra_spaces():
    settings = Settings(_env_file=None, merchant_denylist=["Too  Good Deals", "Kmart"])

    assert settings.merchant_may_alert("too good deals") is False
    assert settings.merchant_may_alert("  TOO GOOD\tDEALS ") is False
    assert settings.merchant_may_alert("kmart") is False
    # A whole name only: another merchant that contains the name is not the listed one.
    assert settings.merchant_may_alert("Too Good Deals Outlet") is True
    assert settings.merchant_may_alert("Good Deals") is True
    # Unicode case mapping must not make another name match a listed one.
    assert settings.merchant_may_alert(KELVIN_KMART) is True
    # A listing without a merchant name is not on a denylist.
    assert settings.merchant_may_alert(None) is True


def test_allowlist_lets_only_its_merchants_alert():
    settings = Settings(_env_file=None, merchant_allowlist=["Best Buy", "Kmart"])

    assert settings.merchant_may_alert("best buy") is True
    assert settings.merchant_may_alert(" Best  Buy ") is True
    assert settings.merchant_may_alert("Walmart") is False
    assert settings.merchant_may_alert(KELVIN_KMART) is False
    # A listing that names no merchant cannot be shown to be on the list.
    assert settings.merchant_may_alert(None) is False
    assert settings.merchant_may_alert("  ") is False


def test_denylist_wins_over_the_allowlist():
    settings = Settings(
        _env_file=None, merchant_allowlist=["Best Buy"], merchant_denylist=["Best Buy"]
    )

    assert settings.merchant_may_alert("Best Buy") is False


def test_verify_merchant_pct_defaults_to_40(monkeypatch):
    monkeypatch.delenv("VERIFY_MERCHANT_PCT", raising=False)

    assert Settings(_env_file=None).verify_merchant_pct == 40


@pytest.mark.parametrize("value", ["0", "100", "-5", "nan", "inf", "forty"])
def test_out_of_range_verify_merchant_pct_fails_validation(monkeypatch, value):
    """It must be above 0 and below 100 percent: 0 would flag every price below the median."""
    monkeypatch.setenv("VERIFY_MERCHANT_PCT", value)

    with pytest.raises(ValidationError, match="verify_merchant_pct"):
        Settings(_env_file=None)

"""Application configuration loaded from environment variables."""

import string
from typing import Annotated, Literal, Self

from pydantic import EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode


def _recipient_key(address: str) -> str:
    """Comparison key for an alert address: ASCII letters ignore case, all else must match.

    Mail providers ignore the case of ASCII letters in practice. Unicode case mapping is not
    applied, since it can turn a different address into an allowed one.
    """
    return address.lower() if address.isascii() else address


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _merchant_key(name: str) -> str:
    """Comparison key for a merchant name: the whole name, ignoring ASCII case and extra spaces.

    Only ASCII letters are lower-cased: Unicode case mapping could turn another name into a
    listed one ("Kmart" written with U+212A KELVIN SIGN lower-cases to "kmart").
    """
    return " ".join(name.split()).translate(_ASCII_LOWER)


class Settings(BaseSettings):
    """All configuration is sourced from environment variables or .env file."""

    # Database
    database_url: str = "sqlite:///./data/hunter_bargain.db"

    # Scheduling — cron expression for daily price checks, in the IANA time zone price_check_tz
    price_check_cron: str = "0 9 * * *"
    price_check_tz: str = "UTC"

    # SMTP email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    # "tls": TLS that verifies the server certificate (implicit on port 465, STARTTLS on any
    # other port), then a login. "none": no TLS and no login, for a local test server such as
    # Mailpit only. Any other value fails validation, so the app does not start.
    smtp_security: Literal["tls", "none"] = "tls"
    smtp_timeout: float = 30  # seconds, for the connect and each SMTP command
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    email_from: str = ""

    # Alerts only go to these addresses: an item's notify_email must be one of them.
    # Comma-separated in the environment: ALERT_RECIPIENTS=me@example.com,you@example.com
    # An invalid address fails validation, so the app does not start.
    alert_recipients: Annotated[list[EmailStr], NoDecode] = []

    # After an alert for an item, the next one needs a price at least alert_min_drop_pct percent
    # below the last alert's price (0: any lower price), or alert_cooldown_days since that alert
    # with the price still on target. Out-of-range values fail validation.
    alert_cooldown_days: float = Field(default=7.0, ge=0, allow_inf_nan=False)
    alert_min_drop_pct: float = Field(default=0.0, ge=0, lt=100, allow_inf_nan=False)

    # Merchants (a listing's seller) whose prices may alert, as comma-separated whole names
    # (merchant_may_alert). Listings from other merchants are still saved, but never alert.
    merchant_denylist: Annotated[list[str], NoDecode] = []
    merchant_allowlist: Annotated[list[str], NoDecode] = []

    # The price an alert reports is flagged "verify merchant" when it is more than this percent
    # below the median of the same check's results. Out-of-range values fail validation.
    verify_merchant_pct: float = Field(default=40.0, gt=0, lt=100, allow_inf_nan=False)

    # Where price alerts and notices go. "smtp" (the default): email, with the settings above;
    # while SMTP is not configured, a price alert is written to the log instead. "apprise": the
    # service APPRISE_URL names, e.g. an ntfy topic. Any other value fails validation.
    notifier: Literal["smtp", "apprise"] = "smtp"
    # For NOTIFIER=apprise, an Apprise URL, e.g. ntfy://ntfy.example.com/hunter-bargain; Apprise's
    # documentation lists the services (https://github.com/caronc/apprise/wiki). It can hold the
    # service's token, so it is a secret and never logged. NOTIFIER=apprise without it fails
    # validation, so the app does not start.
    apprise_url: SecretStr = SecretStr("")

    # SerpAPI
    serpapi_key: SecretStr = SecretStr("")

    # A push monitor's URL (healthchecks.io, Uptime Kuma) that gets one GET after each check of
    # all items that succeeds, so a daily check that stops succeeding raises an alarm outside the
    # app. Empty: off. The URL holds the monitor's token, so it is a secret and never logged.
    heartbeat_url: SecretStr = SecretStr("")

    # Application
    log_level: str = "info"

    # .env also holds Docker Compose's own variables (HB_BIND_ADDR, APP_PORT), which are not
    # settings here: ignore keys that match no setting instead of refusing to start.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("alert_recipients", "merchant_denylist", "merchant_allowlist", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Split a comma-separated list, e.g. ALERT_RECIPIENTS; blank entries are ignored."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @model_validator(mode="after")
    def _apprise_needs_a_url(self) -> Self:
        if self.notifier == "apprise" and not self.apprise_url.get_secret_value():
            raise ValueError("NOTIFIER=apprise needs APPRISE_URL")
        return self

    def is_alert_recipient(self, address: str) -> bool:
        """Return True if alerts may be sent to `address`, i.e. it is in ALERT_RECIPIENTS."""
        return _recipient_key(address) in {_recipient_key(a) for a in self.alert_recipients}

    def merchant_may_alert(self, merchant: str | None) -> bool:
        """Whether a listing from `merchant` may alert: MERCHANT_DENYLIST and MERCHANT_ALLOWLIST.

        A denylisted merchant never alerts. When MERCHANT_ALLOWLIST is set, only its merchants
        alert, and a listing that names no merchant cannot. Both empty: every listing may.
        """
        if not merchant or not merchant.strip():
            return not self.merchant_allowlist
        key = _merchant_key(merchant)
        if key in {_merchant_key(name) for name in self.merchant_denylist}:
            return False
        return not self.merchant_allowlist or key in {
            _merchant_key(name) for name in self.merchant_allowlist
        }


# Singleton — import this wherever configuration is needed.
settings = Settings()

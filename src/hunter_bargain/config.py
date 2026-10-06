"""Application configuration loaded from environment variables."""

from typing import Annotated, Literal

from pydantic import EmailStr, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode


def _recipient_key(address: str) -> str:
    """Comparison key for an alert address: ASCII letters ignore case, all else must match.

    Mail providers ignore the case of ASCII letters in practice. Unicode case mapping is not
    applied, since it can turn a different address into an allowed one.
    """
    return address.lower() if address.isascii() else address


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

    # SerpAPI
    serpapi_key: SecretStr = SecretStr("")

    # Application
    log_level: str = "info"

    # .env also holds Docker Compose's own variables (HB_BIND_ADDR, APP_PORT), which are not
    # settings here: ignore keys that match no setting instead of refusing to start.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("alert_recipients", mode="before")
    @classmethod
    def _split_alert_recipients(cls, value: object) -> object:
        """Split the comma-separated ALERT_RECIPIENTS string; blank entries are ignored."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    def is_alert_recipient(self, address: str) -> bool:
        """Return True if alerts may be sent to `address`, i.e. it is in ALERT_RECIPIENTS."""
        return _recipient_key(address) in {_recipient_key(a) for a in self.alert_recipients}


# Singleton — import this wherever configuration is needed.
settings = Settings()

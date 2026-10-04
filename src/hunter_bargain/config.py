"""Application configuration loaded from environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration is sourced from environment variables or .env file."""

    # Database
    database_url: str = "sqlite:///./data/hunter_bargain.db"

    # Scheduling — cron expression for daily price checks
    price_check_cron: str = "0 9 * * *"

    # SMTP email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # SerpAPI
    serpapi_key: SecretStr = SecretStr("")

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Singleton — import this wherever configuration is needed.
settings = Settings()

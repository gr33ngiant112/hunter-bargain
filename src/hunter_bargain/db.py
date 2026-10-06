"""Database engine and session setup (SQLAlchemy)."""

from collections.abc import Generator
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hunter_bargain.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# The revision whose schema is the one create_all made before migrations (migrations/versions).
BASELINE_REVISION = "7490f8fc3c6d"


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Enforce foreign keys (SQLite's default is off) and journal in WAL mode."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_db_engine(url: str, **kwargs: Any) -> Engine:
    """Create the app's engine for `url`; `kwargs` go to create_engine.

    Each new SQLite connection enforces foreign keys and uses WAL journaling.
    """
    if url.startswith("sqlite"):
        # Use check_same_thread=False for SQLite to allow FastAPI's threaded access.
        kwargs.setdefault("connect_args", {})["check_same_thread"] = False
    new_engine = create_engine(url, **kwargs)
    if new_engine.dialect.name == "sqlite":
        event.listen(new_engine, "connect", _set_sqlite_pragmas)
    return new_engine


engine = create_db_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a database session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(bind: Engine = engine) -> None:
    """Migrate the database to the latest revision. Called once at application startup.

    A database from before migrations (create_all made its tables: items exists, alembic_version
    does not) is first stamped with the baseline revision, which matches that schema. An empty
    database is migrated from nothing; one already at the latest revision is left as it is.

    Migrating in the lifespan assumes a single worker process (uvicorn's default, and what the
    Dockerfile starts): two processes starting together could both migrate the same database.

    SQLite creates a missing database file but not its directory, and the default
    DATABASE_URL points into ./data, which a fresh clone does not have: create it first.
    """
    if bind.url.get_backend_name() == "sqlite" and bind.url.database:
        Path(bind.url.database).parent.mkdir(parents=True, exist_ok=True)
    # Built in code, not from alembic.ini, which the installed package does not have.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["engine"] = bind
    tables = inspect(bind).get_table_names()
    if "items" in tables and "alembic_version" not in tables:
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")

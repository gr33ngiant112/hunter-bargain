"""Alembic environment: migrates the database to match the models (Base.metadata).

The app runs the migrations at startup (hunter_bargain.db.init_db) and passes the engine to
use in config.attributes["engine"]. The alembic command (alembic.ini at the repo root) uses the
app's engine for settings.database_url, i.e. DATABASE_URL.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Engine

from hunter_bargain import models  # noqa: F401  (registers the tables on Base.metadata)
from hunter_bargain.db import Base, engine

config = context.config

# Only the alembic command configures logging, from alembic.ini. The app builds its Config in
# code with no file, so fileConfig never runs there: it would disable the app's own loggers.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_online() -> None:
    """Run the migrations on a connection of the given engine (default: the app's)."""
    bind: Engine = config.attributes.get("engine", engine)
    with bind.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        if is_sqlite:
            # Batch mode rebuilds a table by copying its rows. With foreign keys enforced, a
            # price_records row whose item is gone (possible before enforcement was on) would
            # fail the copy, so enforcement is off while migrating. PRAGMA foreign_keys is a
            # no-op inside a transaction: set it before the migration transaction begins.
            fk_enforced = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                render_as_batch=is_sqlite,  # SQLite alters most things by copying the table
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if is_sqlite and fk_enforced:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()


if context.is_offline_mode():
    # Batch mode needs the live table to copy it, so there is no --sql (offline) mode.
    raise SystemExit("Offline (--sql) migrations are not supported: run them on the database.")
run_migrations_online()

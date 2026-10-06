"""Schema migrations at startup (#21) and foreign keys enforced by SQLite (#10).

A database from before migrations is built from tests/fixtures/legacy_schema_0a8756e.sql, the
schema create_all made, so these tests do not depend on the current models to describe it.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from test_startup import _start_app

from hunter_bargain import db
from hunter_bargain.db import Base

LEGACY_SCHEMA = Path(__file__).parent / "fixtures" / "legacy_schema_0a8756e.sql"

# Rows as the app stored them before migrations. Price record 4's item 3 was deleted while
# SQLite did not enforce foreign keys: an orphan, which the upgrade must keep.
LEGACY_ROWS = """
INSERT INTO items VALUES
    (1, 'Widget', 'blue, 64GB', 25.0, 'user@example.com',
     '2026-09-01 09:00:00.000000', '2026-09-01 09:00:00.000000'),
    (2, 'Gadget', NULL, NULL, 'user@example.com',
     '2026-09-02 09:00:00.000000', '2026-09-03 09:00:00.000000');
INSERT INTO price_records VALUES
    (1, 1, 29.99, 'USD', 'google_shopping', 'https://example.com/w', 'Widget, blue',
     '2026-09-01 09:00:05.000000'),
    (2, 1, 24.5, 'USD', 'bing_shopping', NULL, NULL, '2026-09-02 09:00:05.000000'),
    (3, 2, 10.0, 'USD', 'google_shopping', NULL, 'Gadget', '2026-09-02 09:00:06.000000'),
    (4, 3, 5.0, 'USD', 'google_shopping', NULL, 'Gone', '2026-09-02 09:00:07.000000');
"""


def _legacy_database(path: Path, rows: str = LEGACY_ROWS) -> Path:
    """A SQLite file as the app left it before migrations: no alembic_version table."""
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(LEGACY_SCHEMA.read_text() + rows)
    return path


def _engine(path: Path) -> Engine:
    """The app's engine (create_db_engine, as db.engine is made) for the SQLite file at path."""
    return db.create_db_engine(f"sqlite:///{path}")


def _start(path: Path) -> None:
    """The app's startup migration (init_db) on the SQLite file at path."""
    engine = _engine(path)
    try:
        db.init_db(engine)
    finally:
        engine.dispose()


def _query(path: Path, sql: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql).fetchall()


def _head() -> str | None:
    return ScriptDirectory.from_config(db.alembic_config()).get_current_head()


def _version(path: Path) -> list[tuple[object, ...]]:
    return _query(path, "SELECT version_num FROM alembic_version")


def _rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: _query(path, f"SELECT * FROM {table} ORDER BY id")
        for table in ("items", "price_records")
    }


def _schema(path: Path) -> dict[str, dict[str, list[tuple[object, ...]]]]:
    """Each table's columns, indexes and foreign keys as SQLite reports them, not its SQL text."""
    with closing(sqlite3.connect(path)) as connection:

        def rows(sql: str, name: str) -> list[tuple[object, ...]]:
            return connection.execute(sql, (name,)).fetchall()

        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name != 'alembic_version' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {
            table: {
                "columns": rows(
                    'SELECT name, type, "notnull", dflt_value, pk FROM pragma_table_info(?)'
                    " ORDER BY cid",
                    table,
                ),
                "indexes": [
                    (
                        index,
                        unique,
                        rows("SELECT name FROM pragma_index_info(?) ORDER BY seqno", index),
                    )
                    for index, unique in rows(
                        'SELECT name, "unique" FROM pragma_index_list(?) ORDER BY name', table
                    )
                ],
                "foreign_keys": rows(
                    'SELECT "table", "from", "to", on_update, on_delete'
                    " FROM pragma_foreign_key_list(?) ORDER BY id, seq",
                    table,
                ),
            }
            for (table,) in tables
        }


def _model_differences(path: Path) -> list[object]:
    """What alembic's autogenerate (as `alembic check`) finds between the database and models."""
    engine = _engine(path)
    try:
        with engine.connect() as connection:
            return compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()


def _price_records_fk_names(path: Path) -> list[str | None]:
    engine = _engine(path)
    try:
        return [fk["name"] for fk in inspect(engine).get_foreign_keys("price_records")]
    finally:
        engine.dispose()


MODEL_FK_NAMES = [fk.name for fk in Base.metadata.tables["price_records"].foreign_key_constraints]
CASCADING_FK = [("items", "item_id", "id", "NO ACTION", "CASCADE")]


def test_app_start_upgrades_a_database_from_before_migrations_and_keeps_every_row(tmp_path):
    db_file = _legacy_database(tmp_path / "hb.db")
    rows_before = _rows(db_file)
    _start(tmp_path / "fresh.db")  # the schema an empty database migrates to

    result = _start_app(tmp_path, DATABASE_URL=f"sqlite:///{db_file}")  # the real app

    assert result.returncode == 0, result.stderr
    assert _version(db_file) == [(_head(),)]
    assert _rows(db_file) == rows_before  # price record 4, whose item is gone, included
    assert _schema(db_file)["price_records"]["foreign_keys"] == CASCADING_FK
    assert _schema(db_file) == _schema(tmp_path / "fresh.db")
    assert _price_records_fk_names(db_file) == MODEL_FK_NAMES
    assert _query(db_file, "PRAGMA journal_mode") == [("wal",)]  # set by the app's engine
    # Migrating leaves the app's logging alone: its next startup message is still logged.
    assert "Starting scheduler..." in result.stderr


def test_empty_database_is_migrated_to_head_and_matches_the_models(tmp_path):
    db_file = tmp_path / "hb.db"

    _start(db_file)

    assert _version(db_file) == [(_head(),)]
    assert _model_differences(db_file) == []
    assert _price_records_fk_names(db_file) == MODEL_FK_NAMES


def test_baseline_revision_builds_the_schema_create_all_made(tmp_path):
    legacy = _legacy_database(tmp_path / "legacy.db", rows="")
    baseline = tmp_path / "baseline.db"
    engine = _engine(baseline)

    command.upgrade(db.alembic_config(engine), db.BASELINE_REVISION)
    engine.dispose()

    assert _schema(baseline) == _schema(legacy)


def test_downgrade_to_the_baseline_restores_the_schema_create_all_made(tmp_path):
    legacy = _legacy_database(tmp_path / "legacy.db", rows="")
    db_file = tmp_path / "hb.db"
    _start(db_file)
    engine = _engine(db_file)
    config = db.alembic_config(engine)

    command.downgrade(config, db.BASELINE_REVISION)
    assert _schema(db_file) == _schema(legacy)
    command.downgrade(config, "base")
    assert _schema(db_file) == {}
    engine.dispose()


@pytest.mark.parametrize("before_migrations", [True, False], ids=["legacy", "empty"])
def test_second_start_runs_no_migration_and_changes_nothing(tmp_path, caplog, before_migrations):
    db_file = tmp_path / "hb.db"
    if before_migrations:
        _legacy_database(db_file)
    caplog.set_level(logging.INFO, logger="alembic")
    _start(db_file)
    assert any(r.getMessage().startswith("Running upgrade") for r in caplog.records)
    state = (
        _version(db_file),
        _schema(db_file),
        _rows(db_file),
        _query(db_file, "SELECT * FROM sqlite_master"),
    )
    caplog.clear()

    _start(db_file)

    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("Running")] == []
    assert (
        _version(db_file),
        _schema(db_file),
        _rows(db_file),
        _query(db_file, "SELECT * FROM sqlite_master"),
    ) == state


def test_deleting_an_item_in_sql_deletes_its_price_records(tmp_path):
    """#10: the database itself removes an item's price records, here in an upgraded one."""
    db_file = _legacy_database(tmp_path / "hb.db")
    _start(db_file)
    engine = _engine(db_file)

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM items WHERE id = 1"))
    engine.dispose()

    assert _query(db_file, "SELECT id, item_id FROM price_records ORDER BY id") == [(3, 2), (4, 3)]


def test_price_record_for_a_missing_item_is_refused(tmp_path):
    db_file = tmp_path / "hb.db"
    _start(db_file)
    engine = _engine(db_file)

    insert = text(
        "INSERT INTO price_records (item_id, price, currency, source, checked_at)"
        " VALUES (99, 1.0, 'USD', 'google_shopping', '2026-10-01 09:00:00.000000')"
    )

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"), engine.begin() as c:
        c.execute(insert)
    engine.dispose()

    assert _query(db_file, "SELECT count(*) FROM price_records") == [(0,)]


def test_test_database_enforces_foreign_keys_as_the_apps_does(db_session):
    assert db_session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

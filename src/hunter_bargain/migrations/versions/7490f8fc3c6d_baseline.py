"""Baseline: the schema create_all made before migrations

Revision ID: 7490f8fc3c6d
Revises:
Create Date: 2026-10-06 12:49:03.738332

Equal to the schema of a database created before migrations (tests/test_migrations.py checks
it). The app stamps such a database with this revision instead of running it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7490f8fc3c6d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("keywords", sa.Text(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("notify_email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_items_name", "items", ["name"], unique=False)

    op.create_table(
        "price_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),  # unnamed, no ON DELETE
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_records_item_id", "price_records", ["item_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_price_records_item_id", table_name="price_records")
    op.drop_table("price_records")
    op.drop_index("ix_items_name", table_name="items")
    op.drop_table("items")

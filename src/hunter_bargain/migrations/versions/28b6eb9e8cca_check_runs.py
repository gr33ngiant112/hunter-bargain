"""check_runs: one row per price check of all items

Revision ID: 28b6eb9e8cca
Revises: 84ea40d46025
Create Date: 2026-10-06 13:25:47.458385

The scheduler reads the last successful run at startup, so that a daily check missed while the
app was down runs once it is back (#15).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "28b6eb9e8cca"
down_revision: str | Sequence[str] | None = "84ea40d46025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("check_runs")

"""check_runs: what each run did

Revision ID: 9124d0caa4e8
Revises: 08579e2a6e54
Create Date: 2026-10-12 10:58:14.528046

Each check of all items records the items it checked, the engine searches that worked and those
that failed, and the alerts it sent (#24). Runs recorded before this have none of these: their
columns stay empty (NULL), not 0, since what those runs did is unknown.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9124d0caa4e8"
down_revision: str | Sequence[str] | None = "08579e2a6e54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("check_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("items_checked", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("searches_used", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("engine_errors", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("alerts_sent", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("check_runs", schema=None) as batch_op:
        batch_op.drop_column("alerts_sent")
        batch_op.drop_column("engine_errors")
        batch_op.drop_column("searches_used")
        batch_op.drop_column("items_checked")

"""items: alert state

Revision ID: 08579e2a6e54
Revises: 84ea40d46025
Create Date: 2026-10-06 14:03:22.499841

The price and time of each item's last alert email, so that a price that stays on target is
emailed once (#9). Existing items start with neither, so their next price on target is emailed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08579e2a6e54"
down_revision: str | Sequence[str] | None = "84ea40d46025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_alert_price", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("last_alerted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_column("last_alerted_at")
        batch_op.drop_column("last_alert_price")

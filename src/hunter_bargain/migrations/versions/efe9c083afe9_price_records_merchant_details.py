"""price_records: the listing's merchant, rating and review count

Revision ID: efe9c083afe9
Revises: 9124d0caa4e8
Create Date: 2026-10-12 16:25:11.141074

Each price record keeps its listing's seller, the item's rating and its review count (#29), so
the lows can leave out merchants that may not alert. Records saved before this have none of
them: their columns stay empty (NULL), as for a listing that names no merchant.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "efe9c083afe9"
down_revision: str | Sequence[str] | None = "9124d0caa4e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("price_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merchant", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("rating", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("reviews", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("price_records", schema=None) as batch_op:
        batch_op.drop_column("reviews")
        batch_op.drop_column("rating")
        batch_op.drop_column("merchant")

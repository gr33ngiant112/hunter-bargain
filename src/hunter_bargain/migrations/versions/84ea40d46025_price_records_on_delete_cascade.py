"""price_records.item_id: ON DELETE CASCADE

Revision ID: 84ea40d46025
Revises: 7490f8fc3c6d
Create Date: 2026-10-06 13:05:00.000000

Deleting an item deletes its price records in the database, not only through the ORM. Every
existing row is kept, including any price record whose item is already gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84ea40d46025"
down_revision: str | Sequence[str] | None = "7490f8fc3c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_price_records_item_id_items"  # models.PriceRecord.item_id uses the same name

# SQLite keeps no name for the baseline's foreign key. Batch mode names the reflected one with
# this convention (giving FK_NAME), so drop_constraint can find it.
BASELINE_FK_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade() -> None:
    # Batch mode copies the table; the app and env.py migrate with foreign key enforcement off,
    # so a price record whose item is gone is copied too.
    with op.batch_alter_table("price_records", naming_convention=BASELINE_FK_NAMING) as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.create_foreign_key(FK_NAME, "items", ["item_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    # Back to the baseline's foreign key, which has no name: batch mode cannot add an unnamed
    # one, so the table is rebuilt with item_id given here in place of the reflected column
    # and its foreign key.
    item_id = sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False)
    with op.batch_alter_table("price_records", recreate="always", reflect_args=[item_id]):
        pass

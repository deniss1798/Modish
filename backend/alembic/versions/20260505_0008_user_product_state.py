"""user product state

Revision ID: 20260505_0008
Revises: 20260505_0007
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0008"
down_revision = "20260505_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "user_product_states",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=False),
    sa.Column("hidden_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("event_strength", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_user_product_states_user_id", "user_product_states", ["user_id"])
  op.create_index("ix_user_product_states_product_id", "user_product_states", ["product_id"])
  op.create_unique_constraint("uq_user_product_state", "user_product_states", ["user_id", "product_id"])


def downgrade() -> None:
  op.drop_constraint("uq_user_product_state", "user_product_states", type_="unique")
  op.drop_index("ix_user_product_states_product_id", table_name="user_product_states")
  op.drop_index("ix_user_product_states_user_id", table_name="user_product_states")
  op.drop_table("user_product_states")


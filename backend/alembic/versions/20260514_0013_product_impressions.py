"""product impressions for feed analytics

Revision ID: 20260514_0013
Revises: 20260513_0012
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa

revision = "20260514_0013"
down_revision = "20260513_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "product_impressions",
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
    sa.Column("source", sa.String(64), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_product_impressions_user_id", "product_impressions", ["user_id"])
  op.create_index("ix_product_impressions_product_id", "product_impressions", ["product_id"])
  op.create_index("ix_product_impressions_created_at", "product_impressions", ["created_at"])


def downgrade() -> None:
  op.drop_index("ix_product_impressions_created_at", table_name="product_impressions")
  op.drop_index("ix_product_impressions_product_id", table_name="product_impressions")
  op.drop_index("ix_product_impressions_user_id", table_name="product_impressions")
  op.drop_table("product_impressions")

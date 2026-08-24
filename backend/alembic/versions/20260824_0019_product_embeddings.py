"""Add product embedding storage.

Revision ID: 20260824_0019
Revises: 20260824_0018
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0019"
down_revision = "20260824_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "product_embeddings",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=False),
    sa.Column("embedding_model", sa.String(length=64), nullable=False),
    sa.Column("embedding_dim", sa.Integer(), nullable=False),
    sa.Column("embedding_json", sa.JSON(), nullable=False),
    sa.Column("text_hash", sa.String(length=64), nullable=False),
    sa.Column("text_snapshot", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("product_id", name="uq_product_embeddings_product_id"),
  )
  op.create_index("ix_product_embeddings_product_id", "product_embeddings", ["product_id"])
  op.create_index("ix_product_embeddings_embedding_model", "product_embeddings", ["embedding_model"])
  op.create_index("ix_product_embeddings_text_hash", "product_embeddings", ["text_hash"])


def downgrade() -> None:
  op.drop_index("ix_product_embeddings_text_hash", table_name="product_embeddings")
  op.drop_index("ix_product_embeddings_embedding_model", table_name="product_embeddings")
  op.drop_index("ix_product_embeddings_product_id", table_name="product_embeddings")
  op.drop_table("product_embeddings")

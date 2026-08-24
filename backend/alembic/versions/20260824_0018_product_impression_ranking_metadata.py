"""Add ranking metadata to product impressions.

Revision ID: 20260824_0018
Revises: 20260824_0017
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0018"
down_revision = "20260824_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
  with op.batch_alter_table("product_impressions") as batch:
    batch.add_column(sa.Column("algorithm_version", sa.String(length=64), nullable=True))
    batch.add_column(sa.Column("candidate_source", sa.Text(), nullable=True))
    batch.add_column(sa.Column("final_score", sa.Float(), nullable=True))
    batch.add_column(sa.Column("rank_position", sa.Integer(), nullable=True))
    batch.add_column(sa.Column("ranking_meta_json", sa.JSON(), nullable=True))
  op.create_index("ix_product_impressions_algorithm_version", "product_impressions", ["algorithm_version"])


def downgrade() -> None:
  op.drop_index("ix_product_impressions_algorithm_version", table_name="product_impressions")
  with op.batch_alter_table("product_impressions") as batch:
    batch.drop_column("ranking_meta_json")
    batch.drop_column("rank_position")
    batch.drop_column("final_score")
    batch.drop_column("candidate_source")
    batch.drop_column("algorithm_version")

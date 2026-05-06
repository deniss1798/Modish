"""recommendation cache tables + fit profile interests

Revision ID: 20260506_0011
Revises: 20260506_0010
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa

revision = "20260506_0011"
down_revision = "20260506_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "fit_profiles",
    sa.Column("interest_categories", sa.JSON(), nullable=False, server_default="[]"),
  )
  op.add_column(
    "fit_profiles",
    sa.Column("style_scenarios", sa.JSON(), nullable=False, server_default="[]"),
  )
  op.create_table(
    "recommendation_candidates",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("user_id", sa.String(length=36), nullable=False),
    sa.Column("product_ids", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(
    "ix_recommendation_candidates_user_id",
    "recommendation_candidates",
    ["user_id"],
    unique=True,
  )
  op.create_table(
    "user_recommendation_cache",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("user_id", sa.String(length=36), nullable=False),
    sa.Column("top_product_ids", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(
    "ix_user_recommendation_cache_user_id",
    "user_recommendation_cache",
    ["user_id"],
    unique=True,
  )


def downgrade() -> None:
  op.drop_index("ix_user_recommendation_cache_user_id", table_name="user_recommendation_cache")
  op.drop_table("user_recommendation_cache")
  op.drop_index("ix_recommendation_candidates_user_id", table_name="recommendation_candidates")
  op.drop_table("recommendation_candidates")
  op.drop_column("fit_profiles", "style_scenarios")
  op.drop_column("fit_profiles", "interest_categories")

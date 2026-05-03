"""pre_release tables

Revision ID: 20260503_0002
Revises: 20260503_0001
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260503_0002"
down_revision = "20260503_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "saved_recommendations",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("recommendation_id", sa.String(length=36), sa.ForeignKey("recommendations.id"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_saved_recommendations_user_id", "saved_recommendations", ["user_id"])
  op.create_index("ix_saved_recommendations_recommendation_id", "saved_recommendations", ["recommendation_id"])
  op.create_unique_constraint("uq_saved_user_rec", "saved_recommendations", ["user_id", "recommendation_id"])

  op.create_table(
    "user_preferences",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("category", sa.String(length=32), nullable=False),
    sa.Column("key", sa.String(length=128), nullable=False),
    sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("source", sa.String(length=64), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])
  op.create_index("ix_user_preferences_category", "user_preferences", ["category"])
  op.create_index("ix_user_preferences_key", "user_preferences", ["key"])
  op.create_unique_constraint("uq_pref_user_cat_key", "user_preferences", ["user_id", "category", "key"])

  op.create_table(
    "user_recommendation_summaries",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, unique=True),
    sa.Column("summary_json", sa.JSON(), nullable=False),
    sa.Column("confidence_score", sa.Float(), nullable=False),
    sa.Column("based_on_events_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )

  op.create_table(
    "user_limits",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, unique=True),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("photo_analysis_used", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("recommendation_batches_used", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )


def downgrade() -> None:
  op.drop_table("user_limits")
  op.drop_table("user_recommendation_summaries")
  op.drop_constraint("uq_pref_user_cat_key", "user_preferences", type_="unique")
  op.drop_index("ix_user_preferences_key", table_name="user_preferences")
  op.drop_index("ix_user_preferences_category", table_name="user_preferences")
  op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
  op.drop_table("user_preferences")
  op.drop_constraint("uq_saved_user_rec", "saved_recommendations", type_="unique")
  op.drop_index("ix_saved_recommendations_recommendation_id", table_name="saved_recommendations")
  op.drop_index("ix_saved_recommendations_user_id", table_name="saved_recommendations")
  op.drop_table("saved_recommendations")

"""init

Revision ID: 20260503_0001
Revises: 
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260503_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "users",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("email", sa.String(length=320), nullable=False, unique=True),
    sa.Column("password_hash", sa.String(length=256), nullable=False),
    sa.Column("plan", sa.String(length=32), nullable=False),
    sa.Column("subscription_status", sa.String(length=32), nullable=False),
    sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_users_email", "users", ["email"], unique=True)

  op.create_table(
    "style_profiles",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, unique=True),
    sa.Column("style_target", sa.String(length=32), nullable=False),
    sa.Column("confidence_score", sa.Float(), nullable=False),
    sa.Column("profile_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )

  op.create_table(
    "recommendations",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("type", sa.String(length=32), nullable=False),
    sa.Column("title", sa.String(length=255), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("content_json", sa.JSON(), nullable=False),
    sa.Column("tags_json", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(length=32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_recommendations_user_id", "recommendations", ["user_id"])

  op.create_table(
    "recommendation_events",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("recommendation_id", sa.String(length=36), sa.ForeignKey("recommendations.id"), nullable=False),
    sa.Column("event_type", sa.String(length=32), nullable=False),
    sa.Column("event_weight", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_recommendation_events_user_id", "recommendation_events", ["user_id"])
  op.create_index("ix_recommendation_events_recommendation_id", "recommendation_events", ["recommendation_id"])


def downgrade() -> None:
  op.drop_index("ix_recommendation_events_recommendation_id", table_name="recommendation_events")
  op.drop_index("ix_recommendation_events_user_id", table_name="recommendation_events")
  op.drop_table("recommendation_events")
  op.drop_index("ix_recommendations_user_id", table_name="recommendations")
  op.drop_table("recommendations")
  op.drop_table("style_profiles")
  op.drop_index("ix_users_email", table_name="users")
  op.drop_table("users")

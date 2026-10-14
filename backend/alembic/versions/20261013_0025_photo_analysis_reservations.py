"""Durable photo quota reservations and response replay.

Revision ID: 20261013_0025
Revises: 20261008_0024
"""
from alembic import op
import sqlalchemy as sa

revision = "20261013_0025"
down_revision = "20261008_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "photo_analysis_reservations",
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("request_id", sa.String(128), nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("content_digest", sa.String(64), nullable=False),
    sa.Column("generation", sa.Integer(), nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("response_json", sa.JSON(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("user_id", "request_id", name="uq_photo_analysis_request"),
    sa.UniqueConstraint("user_id", "generation", name="uq_photo_analysis_generation"),
  )
  op.create_index("ix_photo_analysis_reservations_user_id", "photo_analysis_reservations", ["user_id"])


def downgrade() -> None:
  op.drop_index("ix_photo_analysis_reservations_user_id", table_name="photo_analysis_reservations")
  op.drop_table("photo_analysis_reservations")

"""Separate User Twin signal and decay timestamps.

Revision ID: 20260824_0017
Revises: 20260824_0016
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0017"
down_revision = "20260824_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
  with op.batch_alter_table("user_taste_features") as batch:
    batch.add_column(sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True))
    batch.add_column(sa.Column("last_decay_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
  with op.batch_alter_table("user_taste_features") as batch:
    batch.drop_column("last_decay_at")
    batch.drop_column("last_signal_at")

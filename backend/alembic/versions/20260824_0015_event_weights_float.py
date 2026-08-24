"""Use float weights for MIE feedback semantics.

Revision ID: 20260824_0015
Revises: 20260524_0014
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0015"
down_revision = "20260524_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
  with op.batch_alter_table("recommendation_events_v2") as batch:
    batch.alter_column(
      "event_weight",
      existing_type=sa.Integer(),
      type_=sa.Float(),
      existing_nullable=False,
      server_default="0",
    )
  with op.batch_alter_table("user_product_states") as batch:
    batch.alter_column(
      "event_strength",
      existing_type=sa.Integer(),
      type_=sa.Float(),
      existing_nullable=False,
      server_default="0",
    )


def downgrade() -> None:
  with op.batch_alter_table("user_product_states") as batch:
    batch.alter_column(
      "event_strength",
      existing_type=sa.Float(),
      type_=sa.Integer(),
      existing_nullable=False,
      server_default="0",
    )
  with op.batch_alter_table("recommendation_events_v2") as batch:
    batch.alter_column(
      "event_weight",
      existing_type=sa.Float(),
      type_=sa.Integer(),
      existing_nullable=False,
      server_default="0",
    )

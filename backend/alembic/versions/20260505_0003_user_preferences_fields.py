"""user preferences fields

Revision ID: 20260505_0003
Revises: 20260503_0002
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0003"
down_revision = "20260503_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "users",
    sa.Column("height_cm", sa.Integer(), nullable=False, server_default="170"),
  )
  op.add_column(
    "users",
    sa.Column("weight_kg", sa.Integer(), nullable=True),
  )
  op.add_column(
    "users",
    sa.Column(
      "fit_preference",
      sa.String(length=32),
      nullable=False,
      server_default="regular",
    ),
  )


def downgrade() -> None:
  op.drop_column("users", "fit_preference")
  op.drop_column("users", "weight_kg")
  op.drop_column("users", "height_cm")


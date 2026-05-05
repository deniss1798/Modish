"""product fields v2

Revision ID: 20260505_0009
Revises: 20260505_0008
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0009"
down_revision = "20260505_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column("products", sa.Column("available_sizes_detailed", sa.JSON(), nullable=False, server_default="[]"))
  op.add_column("products", sa.Column("size_system", sa.String(length=32), nullable=True))
  op.add_column("products", sa.Column("color_family", sa.String(length=32), nullable=True))
  op.add_column("products", sa.Column("material", sa.String(length=64), nullable=True))
  op.add_column("products", sa.Column("season", sa.String(length=32), nullable=True))
  op.add_column("products", sa.Column("occasion", sa.String(length=32), nullable=True))
  op.add_column("products", sa.Column("gender_target", sa.String(length=32), nullable=True))
  op.add_column("products", sa.Column("image_quality_score", sa.Float(), nullable=True))
  op.add_column("products", sa.Column("is_available", sa.Integer(), nullable=False, server_default="1"))
  op.add_column("products", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
  op.drop_column("products", "last_checked_at")
  op.drop_column("products", "is_available")
  op.drop_column("products", "image_quality_score")
  op.drop_column("products", "gender_target")
  op.drop_column("products", "occasion")
  op.drop_column("products", "season")
  op.drop_column("products", "material")
  op.drop_column("products", "color_family")
  op.drop_column("products", "size_system")
  op.drop_column("products", "available_sizes_detailed")


"""taste profile weights

Revision ID: 20260505_0007
Revises: 20260505_0006
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0007"
down_revision = "20260505_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column("taste_profiles", sa.Column("category_weights", sa.JSON(), nullable=False, server_default="{}"))
  op.add_column("taste_profiles", sa.Column("brand_weights", sa.JSON(), nullable=False, server_default="{}"))
  op.add_column("taste_profiles", sa.Column("color_weights", sa.JSON(), nullable=False, server_default="{}"))
  op.add_column("taste_profiles", sa.Column("style_weights", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
  op.drop_column("taste_profiles", "style_weights")
  op.drop_column("taste_profiles", "color_weights")
  op.drop_column("taste_profiles", "brand_weights")
  op.drop_column("taste_profiles", "category_weights")


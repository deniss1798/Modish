"""StyleProfile onboarding v2 fields."""

from alembic import op
import sqlalchemy as sa

revision = "20260524_0014"
down_revision = "20260514_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column("style_profiles", sa.Column("age_group", sa.String(length=16), nullable=True))
  op.add_column("style_profiles", sa.Column("photo_url", sa.Text(), nullable=True))
  op.add_column("style_profiles", sa.Column("photo_analysis", sa.JSON(), nullable=True))
  op.add_column("style_profiles", sa.Column("body_shape", sa.String(length=64), nullable=True))
  op.add_column("style_profiles", sa.Column("color_type", sa.String(length=64), nullable=True))
  op.add_column("style_profiles", sa.Column("height_category", sa.String(length=32), nullable=True))
  op.add_column("style_profiles", sa.Column("style_preferences", sa.JSON(), nullable=True))
  op.add_column("style_profiles", sa.Column("price_segment", sa.String(length=32), nullable=True))
  op.add_column("style_profiles", sa.Column("onboarding_step", sa.Integer(), nullable=False, server_default="0"))
  op.add_column("style_profiles", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
  for col in (
    "completed_at",
    "onboarding_step",
    "price_segment",
    "style_preferences",
    "height_category",
    "color_type",
    "body_shape",
    "photo_analysis",
    "photo_url",
    "age_group",
  ):
    op.drop_column("style_profiles", col)

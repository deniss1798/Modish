"""fit profile + outfits

Revision ID: 20260505_0005
Revises: 20260505_0004
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0005"
down_revision = "20260505_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "fit_profiles",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, unique=True),
    sa.Column("height_cm", sa.Integer(), nullable=False),
    sa.Column("weight_kg", sa.Integer(), nullable=True),
    sa.Column("gender_target", sa.String(length=32), nullable=False, server_default="unisex"),
    sa.Column("clothing_size", sa.String(length=32), nullable=False, server_default="M"),
    sa.Column("body_proportions", sa.Text(), nullable=False, server_default=""),
    sa.Column("contrast_level", sa.String(length=32), nullable=False, server_default=""),
    sa.Column("color_palette", sa.JSON(), nullable=False),
    sa.Column("avoid_colors", sa.JSON(), nullable=False),
    sa.Column("recommended_silhouettes", sa.JSON(), nullable=False),
    sa.Column("avoid_silhouettes", sa.JSON(), nullable=False),
    sa.Column("recommended_fit", sa.String(length=32), nullable=False, server_default="regular"),
    sa.Column("avoid_fit", sa.JSON(), nullable=False),
    sa.Column("style_constraints", sa.JSON(), nullable=False),
    sa.Column("budget_min", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("budget_max", sa.Integer(), nullable=False, server_default="10000"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_fit_profiles_user_id", "fit_profiles", ["user_id"])

  op.create_table(
    "outfits",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("items_json", sa.JSON(), nullable=False),
    sa.Column("total_price", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("style_direction", sa.String(length=128), nullable=False, server_default=""),
    sa.Column("reason", sa.Text(), nullable=False, server_default=""),
    sa.Column("score", sa.Float(), nullable=False, server_default="0"),
    sa.Column("is_saved", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_outfits_user_id", "outfits", ["user_id"])


def downgrade() -> None:
  op.drop_index("ix_outfits_user_id", table_name="outfits")
  op.drop_table("outfits")
  op.drop_index("ix_fit_profiles_user_id", table_name="fit_profiles")
  op.drop_table("fit_profiles")


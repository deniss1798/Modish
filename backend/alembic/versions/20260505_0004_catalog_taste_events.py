"""catalog + taste profile + v2 events

Revision ID: 20260505_0004
Revises: 20260505_0003
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0004"
down_revision = "20260505_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "products",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("external_id", sa.String(length=128), nullable=False),
    sa.Column("source", sa.String(length=64), nullable=False),
    sa.Column("title", sa.String(length=255), nullable=False),
    sa.Column("brand", sa.String(length=128), nullable=False),
    sa.Column("category", sa.String(length=64), nullable=False),
    sa.Column("subcategory", sa.String(length=64), nullable=True),
    sa.Column("price", sa.Integer(), nullable=False),
    sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
    sa.Column("image_url", sa.Text(), nullable=False),
    sa.Column("product_url", sa.Text(), nullable=False),
    sa.Column("available_sizes", sa.JSON(), nullable=False),
    sa.Column("colors", sa.JSON(), nullable=False),
    sa.Column("fit", sa.String(length=32), nullable=True),
    sa.Column("silhouette", sa.String(length=64), nullable=True),
    sa.Column("style_tags", sa.JSON(), nullable=False),
    sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_products_external_id", "products", ["external_id"])
  op.create_index("ix_products_source", "products", ["source"])
  op.create_index("ix_products_brand", "products", ["brand"])
  op.create_index("ix_products_category", "products", ["category"])
  op.create_unique_constraint("uq_product_external_source", "products", ["external_id", "source"])

  op.create_table(
    "taste_profiles",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, unique=True),
    sa.Column("liked_categories", sa.JSON(), nullable=False),
    sa.Column("disliked_categories", sa.JSON(), nullable=False),
    sa.Column("liked_colors", sa.JSON(), nullable=False),
    sa.Column("disliked_colors", sa.JSON(), nullable=False),
    sa.Column("liked_brands", sa.JSON(), nullable=False),
    sa.Column("disliked_brands", sa.JSON(), nullable=False),
    sa.Column("liked_styles", sa.JSON(), nullable=False),
    sa.Column("disliked_styles", sa.JSON(), nullable=False),
    sa.Column("price_min", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("price_max", sa.Integer(), nullable=False, server_default="10000"),
    sa.Column("preferred_fit", sa.String(length=32), nullable=False, server_default="regular"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_taste_profiles_user_id", "taste_profiles", ["user_id"])

  op.create_table(
    "recommendation_events_v2",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=True),
    sa.Column("outfit_id", sa.String(length=36), nullable=True),
    sa.Column("event_type", sa.String(length=32), nullable=False),
    sa.Column("event_weight", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("meta_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_recommendation_events_v2_user_id", "recommendation_events_v2", ["user_id"])
  op.create_index("ix_recommendation_events_v2_product_id", "recommendation_events_v2", ["product_id"])
  op.create_index("ix_recommendation_events_v2_outfit_id", "recommendation_events_v2", ["outfit_id"])


def downgrade() -> None:
  op.drop_index("ix_recommendation_events_v2_outfit_id", table_name="recommendation_events_v2")
  op.drop_index("ix_recommendation_events_v2_product_id", table_name="recommendation_events_v2")
  op.drop_index("ix_recommendation_events_v2_user_id", table_name="recommendation_events_v2")
  op.drop_table("recommendation_events_v2")
  op.drop_index("ix_taste_profiles_user_id", table_name="taste_profiles")
  op.drop_table("taste_profiles")
  op.drop_constraint("uq_product_external_source", "products", type_="unique")
  op.drop_index("ix_products_category", table_name="products")
  op.drop_index("ix_products_brand", table_name="products")
  op.drop_index("ix_products_source", table_name="products")
  op.drop_index("ix_products_external_id", table_name="products")
  op.drop_table("products")


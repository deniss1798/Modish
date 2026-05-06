"""affiliate catalog: product_sources, product extensions, sync_runs, clicks, rules

Revision ID: 20260506_0010
Revises: 20260505_0009
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa

revision = "20260506_0010"
down_revision = "20260505_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "product_sources",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("code", sa.String(length=64), nullable=False),
    sa.Column("name", sa.String(length=128), nullable=False),
    sa.Column("network", sa.String(length=64), nullable=False),
    sa.Column("advertiser_id", sa.String(length=128), nullable=True),
    sa.Column("feed_url", sa.Text(), nullable=True),
    sa.Column("deeplink_template", sa.Text(), nullable=True),
    sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
    sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index("ix_product_sources_code", "product_sources", ["code"], unique=True)
  op.create_index("ix_product_sources_network", "product_sources", ["network"])

  op.create_table(
    "catalog_sync_runs",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("source_id", sa.String(length=36), nullable=False),
    sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
    sa.Column("total_received", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("deactivated_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("error_message", sa.Text(), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(["source_id"], ["product_sources.id"]),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index("ix_catalog_sync_runs_source_id", "catalog_sync_runs", ["source_id"])

  op.create_table(
    "affiliate_clicks",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("user_id", sa.String(length=36), nullable=False),
    sa.Column("product_id", sa.String(length=36), nullable=False),
    sa.Column("source_id", sa.String(length=36), nullable=True),
    sa.Column("affiliate_url", sa.Text(), nullable=False),
    sa.Column("click_id", sa.String(length=128), nullable=True),
    sa.Column("user_agent", sa.Text(), nullable=True),
    sa.Column("ip_hash", sa.String(length=128), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
    sa.ForeignKeyConstraint(["source_id"], ["product_sources.id"]),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index("ix_affiliate_clicks_user_id", "affiliate_clicks", ["user_id"])
  op.create_index("ix_affiliate_clicks_product_id", "affiliate_clicks", ["product_id"])
  op.create_index("ix_affiliate_clicks_source_id", "affiliate_clicks", ["source_id"])

  op.create_table(
    "source_rules",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("source_id", sa.String(length=36), nullable=False),
    sa.Column("rule_type", sa.String(length=64), nullable=False),
    sa.Column("rule_value", sa.Text(), nullable=False),
    sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["source_id"], ["product_sources.id"]),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index("ix_source_rules_source_id", "source_rules", ["source_id"])
  op.create_index("ix_source_rules_rule_type", "source_rules", ["rule_type"])

  op.add_column("products", sa.Column("source_id", sa.String(length=36), nullable=True))
  op.create_foreign_key("fk_products_source_id", "products", "product_sources", ["source_id"], ["id"])
  op.create_index("ix_products_source_id", "products", ["source_id"])

  op.add_column("products", sa.Column("old_price", sa.Integer(), nullable=True))
  op.add_column("products", sa.Column("discount_percent", sa.Integer(), nullable=True))
  op.add_column("products", sa.Column("availability_status", sa.String(length=32), nullable=True))
  op.add_column("products", sa.Column("affiliate_url", sa.Text(), nullable=True))
  op.add_column("products", sa.Column("original_url", sa.Text(), nullable=True))
  op.add_column("products", sa.Column("feed_raw_json", sa.JSON(), nullable=True))
  op.add_column("products", sa.Column("merchant_category", sa.String(length=128), nullable=True))
  op.add_column("products", sa.Column("merchant_subcategory", sa.String(length=128), nullable=True))
  op.add_column("products", sa.Column("external_updated_at", sa.DateTime(timezone=True), nullable=True))
  op.add_column("products", sa.Column("last_seen_in_feed_at", sa.DateTime(timezone=True), nullable=True))
  op.add_column(
    "products",
    sa.Column("is_deleted_from_feed", sa.Integer(), nullable=False, server_default="0"),
  )


def downgrade() -> None:
  op.drop_column("products", "is_deleted_from_feed")
  op.drop_column("products", "last_seen_in_feed_at")
  op.drop_column("products", "external_updated_at")
  op.drop_column("products", "merchant_subcategory")
  op.drop_column("products", "merchant_category")
  op.drop_column("products", "feed_raw_json")
  op.drop_column("products", "original_url")
  op.drop_column("products", "affiliate_url")
  op.drop_column("products", "availability_status")
  op.drop_column("products", "discount_percent")
  op.drop_column("products", "old_price")
  op.drop_index("ix_products_source_id", table_name="products")
  op.drop_constraint("fk_products_source_id", "products", type_="foreignkey")
  op.drop_column("products", "source_id")

  op.drop_table("source_rules")
  op.drop_table("affiliate_clicks")
  op.drop_table("catalog_sync_runs")
  op.drop_table("product_sources")

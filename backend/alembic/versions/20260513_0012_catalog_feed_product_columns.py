"""product: поля YML/Befree (group_id, описание, картинки, штрихкод, категория, param).

Revision ID: 20260513_0012
Revises: 20260506_0011
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260513_0012"
down_revision = "20260506_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column("products", sa.Column("group_id", sa.String(length=64), nullable=True))
  op.add_column("products", sa.Column("description", sa.Text(), nullable=True))
  op.add_column(
    "products",
    sa.Column("image_urls", sa.JSON(), nullable=False, server_default="[]"),
  )
  op.add_column("products", sa.Column("barcode", sa.String(length=128), nullable=True))
  op.add_column("products", sa.Column("vendor_code", sa.String(length=128), nullable=True))
  op.add_column("products", sa.Column("category_external_id", sa.String(length=64), nullable=True))
  op.add_column("products", sa.Column("category_name", sa.String(length=255), nullable=True))
  op.add_column(
    "products",
    sa.Column("raw_params_json", sa.JSON(), nullable=False, server_default="{}"),
  )
  op.add_column("products", sa.Column("size_original", sa.String(length=64), nullable=True))
  op.add_column("products", sa.Column("color_original", sa.String(length=128), nullable=True))
  op.create_index("ix_products_group_id", "products", ["group_id"])
  op.create_index("ix_products_category_external_id", "products", ["category_external_id"])


def downgrade() -> None:
  op.drop_index("ix_products_category_external_id", table_name="products")
  op.drop_index("ix_products_group_id", table_name="products")
  op.drop_column("products", "color_original")
  op.drop_column("products", "size_original")
  op.drop_column("products", "raw_params_json")
  op.drop_column("products", "category_name")
  op.drop_column("products", "category_external_id")
  op.drop_column("products", "vendor_code")
  op.drop_column("products", "barcode")
  op.drop_column("products", "image_urls")
  op.drop_column("products", "description")
  op.drop_column("products", "group_id")

"""Versioned raw/canonical records and an inspectable catalog quarantine."""
from alembic import op
import sqlalchemy as sa

revision = "20260921_0022"
down_revision = "20260917_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("catalog_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("product_sources.id"), nullable=False),
        sa.Column("raw_key", sa.String(160), nullable=False),
        sa.Column("external_id", sa.String(128)),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id")),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("canonical_json", sa.JSON()),
        sa.Column("normalization_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "raw_key", name="uq_catalog_record_source_key"))
    for column in ("source_id", "product_id", "status"):
        op.create_index(f"ix_catalog_records_{column}", "catalog_records", [column])


def downgrade():
    op.drop_table("catalog_records")

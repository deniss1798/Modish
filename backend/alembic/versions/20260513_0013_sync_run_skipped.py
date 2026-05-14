"""Sync run: skipped_count, ingested_count, skip_breakdown."""
from alembic import op
import sqlalchemy as sa

revision = "20260513_0013"
down_revision = "20260513_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column("catalog_sync_runs", sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False))
  op.add_column("catalog_sync_runs", sa.Column("ingested_count", sa.Integer(), server_default="0", nullable=False))
  op.add_column("catalog_sync_runs", sa.Column("skip_breakdown", sa.JSON(), server_default="{}", nullable=False))


def downgrade() -> None:
  op.drop_column("catalog_sync_runs", "skip_breakdown")
  op.drop_column("catalog_sync_runs", "ingested_count")
  op.drop_column("catalog_sync_runs", "skipped_count")

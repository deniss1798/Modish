"""metric events

Revision ID: 20260505_0006
Revises: 20260505_0005
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260505_0006"
down_revision = "20260505_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "metric_events",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
    sa.Column("name", sa.String(length=64), nullable=False),
    sa.Column("meta_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_index("ix_metric_events_user_id", "metric_events", ["user_id"])
  op.create_index("ix_metric_events_name", "metric_events", ["name"])


def downgrade() -> None:
  op.drop_index("ix_metric_events_name", table_name="metric_events")
  op.drop_index("ix_metric_events_user_id", table_name="metric_events")
  op.drop_table("metric_events")


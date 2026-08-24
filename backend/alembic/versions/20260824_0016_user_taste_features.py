"""Add User Twin v2 taste features.

Revision ID: 20260824_0016
Revises: 20260824_0015
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0016"
down_revision = "20260824_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
  with op.batch_alter_table("taste_profiles") as batch:
    batch.add_column(
      sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1")
    )

  op.create_table(
    "user_taste_features",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("feature_type", sa.String(length=32), nullable=False),
    sa.Column("feature_value", sa.String(length=128), nullable=False),
    sa.Column("preference_score", sa.Float(), nullable=False, server_default="0"),
    sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
    sa.Column("positive_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("negative_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("last_positive_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_negative_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("user_id", "feature_type", "feature_value", name="uq_user_taste_feature"),
  )
  op.create_index("ix_user_taste_features_user_id", "user_taste_features", ["user_id"])
  op.create_index("ix_user_taste_features_feature_type", "user_taste_features", ["feature_type"])
  op.create_index("ix_user_taste_features_feature_value", "user_taste_features", ["feature_value"])


def downgrade() -> None:
  op.drop_index("ix_user_taste_features_feature_value", table_name="user_taste_features")
  op.drop_index("ix_user_taste_features_feature_type", table_name="user_taste_features")
  op.drop_index("ix_user_taste_features_user_id", table_name="user_taste_features")
  op.drop_table("user_taste_features")

  with op.batch_alter_table("taste_profiles") as batch:
    batch.drop_column("profile_version")

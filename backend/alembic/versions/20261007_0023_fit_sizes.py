"""Explicit fit sizes without a synthetic M; retain legacy values for review.

Revision ID: 20261007_0023
Revises: 20260921_0022
"""
from alembic import op
import sqlalchemy as sa

revision = '20261007_0023'
down_revision = '20260921_0022'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fit_profiles') as batch:
        batch.add_column(sa.Column('size_profile_json', sa.JSON(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('body_measurements_json', sa.JSON(), nullable=False, server_default='{}'))
        batch.alter_column('clothing_size', existing_type=sa.String(32), nullable=True, server_default=None)
    # Existing M values may be user input or an old default. Preserve them as
    # legacy data; an empty new profile requests confirmation, never assumes it.


def downgrade():
    # Empty means unknown in the old schema; never manufacture a real size.
    op.execute("UPDATE fit_profiles SET clothing_size = '' WHERE clothing_size IS NULL")
    with op.batch_alter_table('fit_profiles') as batch:
        batch.drop_column('body_measurements_json')
        batch.drop_column('size_profile_json')
        batch.alter_column('clothing_size', existing_type=sa.String(32), nullable=False, server_default='')

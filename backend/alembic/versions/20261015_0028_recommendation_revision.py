"""Add an atomic personal recommendation input revision.

Revision ID: 20261015_0028
Revises: 20261015_0027
"""
from alembic import op
import sqlalchemy as sa

revision = '20261015_0028'
down_revision = '20261015_0027'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('recommendation_revision', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('users', 'recommendation_revision')

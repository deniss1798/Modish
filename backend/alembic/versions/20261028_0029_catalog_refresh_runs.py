"""Durable bounded catalogue refresh progress.

Revision ID: 20261028_0029
Revises: 20261015_0028

This migration belongs to the refresh17 integration, not the sealed build16.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261028_0029'
down_revision = '20261015_0028'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('catalog_refresh_runs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('snapshot_sha256', sa.String(64), nullable=False),
        sa.Column('operation_count', sa.Integer(), nullable=False),
        sa.Column('cursor', sa.Integer(), nullable=False),
        sa.Column('finalized_sources', sa.Integer(), nullable=False),
        sa.Column('last_batch_sha256', sa.String(64), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('counts_json', sa.JSON(), nullable=False),
        sa.Column('source_states_json', sa.JSON(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == 'postgresql':
        # The same key as BatchWriter. A completed worker may still own its
        # session lock between commits; never remove its ledger underneath it.
        # SQL guards also run when an offline migration script is later applied.
        op.execute("""DO $$ BEGIN
            IF NOT pg_try_advisory_xact_lock(20261028, 17) THEN
                RAISE EXCEPTION 'catalogue_refresh_worker_must_be_stopped_before_downgrade';
            END IF;
        END $$""")
        op.execute('LOCK TABLE catalog_refresh_runs IN ACCESS EXCLUSIVE MODE NOWAIT')
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM catalog_refresh_runs WHERE status <> 'complete') THEN
                RAISE EXCEPTION 'unfinished_catalogue_refresh_prevents_downgrade';
            END IF;
        END $$""")
    elif connection.dialect.name == 'sqlite':
        if connection.execute(sa.text(
                "SELECT 1 FROM catalog_refresh_runs WHERE status <> 'complete' LIMIT 1")).first():
            raise RuntimeError('unfinished_catalogue_refresh_prevents_downgrade')
    else:
        raise RuntimeError('catalogue_refresh_downgrade_requires_supported_database')
    op.drop_table('catalog_refresh_runs')

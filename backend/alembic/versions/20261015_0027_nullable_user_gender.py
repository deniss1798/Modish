"""Keep user gender unset until an explicit choice.

Revision ID: 20261015_0027
Revises: 20261015_0026

Existing choices are retained: legacy defaults cannot be distinguished from
deliberate choices without provenance. No gender is inferred or backfilled.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261015_0027'
down_revision = '20261015_0026'
branch_labels = None
depends_on = None


def _alter(*, nullable: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name == 'sqlite':
        # SQLite batch recreation must not cascade through dependent records.
        if connection.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('SQLite gender migration requires foreign_keys=OFF on its isolated migration connection')
        with op.batch_alter_table('fit_profiles') as batch:
            batch.alter_column('gender_target', existing_type=sa.String(32),
                nullable=nullable, server_default=None if nullable else 'unisex')
    else:
        op.alter_column('fit_profiles', 'gender_target', existing_type=sa.String(32),
            nullable=nullable, server_default=None if nullable else 'unisex')


def upgrade() -> None:
    _alter(nullable=True)


def downgrade() -> None:
    # A rollback must not silently assign a gender to users who never chose it.
    connection = op.get_bind()
    if connection.execute(sa.text('SELECT 1 FROM fit_profiles WHERE gender_target IS NULL LIMIT 1')).first():
        raise RuntimeError('Cannot downgrade unset user gender; restore a compatible snapshot instead')
    _alter(nullable=False)

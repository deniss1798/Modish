"""Preserve kopecks in RUB prices without changing the API currency unit.

Revision ID: 20261015_0026
Revises: 20261013_0025
"""
from alembic import op
import sqlalchemy as sa

revision = '20261015_0026'
down_revision = '20261013_0025'
branch_labels = None
depends_on = None

_COLUMNS = {'products': ('price', 'old_price'), 'outfits': ('total_price',)}


def _alter(*, upgrade: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name == 'sqlite':
        # SQLite cannot disable FK enforcement once a migration transaction has
        # begun. Fail before any change rather than cascade/delete child rows.
        if connection.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('SQLite price migration requires foreign_keys=OFF on its isolated migration connection')
        for table, columns in _COLUMNS.items():
            with op.batch_alter_table(table) as batch:
                for column in columns:
                    batch.alter_column(column,
                        existing_type=sa.Integer() if upgrade else sa.Numeric(14, 2),
                        type_=sa.Numeric(14, 2) if upgrade else sa.Integer(),
                        existing_nullable=column == 'old_price')
    else:
        for table, columns in _COLUMNS.items():
            for column in columns:
                op.alter_column(table, column,
                    existing_type=sa.Integer() if upgrade else sa.Numeric(14, 2),
                    type_=sa.Numeric(14, 2) if upgrade else sa.Integer(),
                    existing_nullable=column == 'old_price',
                    postgresql_using=(f'{column}::numeric(14,2)' if upgrade else f'{column}::integer'))


def upgrade() -> None:
    _alter(upgrade=True)


def downgrade() -> None:
    # Integer downgrade is lossless only before any fractional amount is stored.
    # Check all columns first so refusal cannot leave a partially altered schema.
    connection = op.get_bind()
    for table, columns in _COLUMNS.items():
        for column in columns:
            row = connection.execute(sa.text(
                f'SELECT 1 FROM {table} WHERE {column} IS NOT NULL '
                f'AND ({column} <> CAST({column} AS BIGINT) '
                f'OR {column} > 2147483647 OR {column} < -2147483648) LIMIT 1'
            )).first()
            if row is not None:
                raise RuntimeError('Cannot downgrade money precision or range; restore a compatible snapshot instead')
    _alter(upgrade=False)

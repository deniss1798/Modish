"""Exercise real Alembic history only on the disposable workspace PostgreSQL.

This helper never starts a server, reads backend/.env, accepts a database URL,
or accesses port 5432. Run it with backend/.venv/Scripts/python.exe after the
workspace fixture cluster has been started separately.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
from uuid import uuid4


WORKSPACE = Path(__file__).resolve().parents[1]
OLD_REVISION = '20261013_0025'
HEAD_REVISION = '20261015_0028'
PORT = 55439
SCHEMA_PATTERN = re.compile(r'^release15_migrations_[0-9a-f]{32}$')
TABLES = ('users', 'fit_profiles', 'product_sources', 'products', 'outfits',
          'user_product_states', 'affiliate_clicks')


class MigrationCheckError(RuntimeError):
    """Fixed, non-secret diagnostic for an isolation or acceptance failure."""


def require(condition, reason):
    if not condition:
        raise MigrationCheckError(reason)


def load_runtime(path, *, workspace=WORKSPACE):
    """Resolve paths before connecting; metadata cannot redirect the test."""
    temporary = (workspace / '.tmp').resolve()
    runtime_path = Path(path).resolve()
    require(runtime_path.is_relative_to(temporary), 'runtime_outside_workspace_tmp')
    metadata = json.loads(runtime_path.read_text(encoding='utf-8-sig'))
    root = Path(metadata['root']).resolve()
    data = Path(metadata['data']).resolve()
    require(root.is_relative_to(temporary) and root.parent == temporary,
            'cluster_outside_workspace_tmp')
    require(root.name.startswith('alpha12-pg-'), 'unexpected_cluster_name')
    require(data == root / 'data', 'unexpected_cluster_data_directory')
    require(type(metadata['port']) is int and metadata['port'] == PORT,
            'unexpected_cluster_port')
    return {'data': data, 'port': PORT}


def isolated_environment(database_url):
    """Keep OS runtime essentials, never inherit provider/auth/database config."""
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
                                       'PATH', 'PATHEXT', 'COMSPEC'}}
    environment.update(DATABASE_URL=database_url, APP_ENV='test',
                       JWT_SECRET='SYNTHETIC_RELEASE15_MIGRATION_JWT',
                       PYTHON_DOTENV_DISABLED='1')
    return environment


def verify_server(connection, runtime):
    from sqlalchemy import text
    require(Path(connection.scalar(text('SHOW data_directory'))).resolve() == runtime['data'],
            'actual_cluster_directory_mismatch')
    require(connection.scalar(text('SHOW port')) == str(PORT), 'actual_cluster_port_mismatch')
    require(connection.scalar(text('SELECT current_database()')) == 'postgres',
            'actual_database_mismatch')


@contextmanager
def isolated_schema(admin, runtime):
    """Only the freshly generated schema may be created or removed."""
    from sqlalchemy import text
    schema = 'release15_migrations_' + uuid4().hex
    require(SCHEMA_PATTERN.fullmatch(schema) is not None, 'unsafe_schema_identifier')
    with admin.begin() as connection:
        verify_server(connection, runtime)
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    try:
        yield schema
    finally:
        with admin.begin() as connection:
            verify_server(connection, runtime)
            require(SCHEMA_PATTERN.fullmatch(schema) is not None, 'unsafe_schema_cleanup')
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def fixture_url(schema=None):
    from sqlalchemy.engine import URL
    query = {'connect_timeout': '3'}
    if schema is not None:
        require(SCHEMA_PATTERN.fullmatch(schema) is not None, 'unsafe_schema_url')
        # No public fallback: neither migrations nor seeds can resolve an
        # existing application table outside this generated schema.
        query['options'] = f'-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=2000'
    return URL.create('postgresql+psycopg', username='modish_alpha_fixture',
                      host='127.0.0.1', port=PORT, database='postgres', query=query)


def migrate(url, direction, revision):
    from alembic import command
    from alembic.config import Config
    config = Config(str(WORKSPACE / 'backend' / 'alembic.ini'))
    config.set_main_option('script_location', str(WORKSPACE / 'backend' / 'alembic'))
    config.set_main_option('prepend_sys_path', str(WORKSPACE / 'backend'))
    # env.py puts DATABASE_URL into ConfigParser. Encoded options contain '%'
    # and cannot go through that interpolation layer verbatim. PGOPTIONS is
    # connection-local process configuration, built only from our schema URL;
    # the plain URL remains safe for the unchanged production env.py.
    environment = isolated_environment(fixture_url().render_as_string(hide_password=False))
    environment['PGOPTIONS'] = url.query['options']
    with patch.dict(os.environ, environment, clear=True), \
            patch('dotenv.load_dotenv', return_value=False):
        # command runs the actual env.py and its PostgreSQL transaction; direct
        # Operations calls would miss rollback across multiple revisions.
        getattr(command, direction)(config, revision)


def _insert(connection, table_name, values):
    """Supply synthetic required fields reflected from the historical schema."""
    import sqlalchemy as sa
    table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
    row = dict(values)
    for column in table.columns:
        if column.name in row or column.nullable or column.server_default is not None:
            continue
        if isinstance(column.type, sa.DateTime):
            row[column.name] = datetime(2026, 10, 20, tzinfo=timezone.utc)
        elif isinstance(column.type, sa.JSON):
            row[column.name] = {}
        elif isinstance(column.type, (sa.Integer, sa.Numeric, sa.Float)):
            row[column.name] = 0
        elif isinstance(column.type, (sa.String, sa.Text)):
            row[column.name] = 'synthetic'
        else:
            raise MigrationCheckError('unexpected_historical_required_type')
    connection.execute(table.insert(), row)


def seed_legacy(engine):
    with engine.begin() as connection:
        for name in ('womenswear', 'menswear', 'unisex', 'new'):
            _insert(connection, 'users', {'id': name, 'email': f'{name}@example.test'})
            if name != 'new':
                _insert(connection, 'fit_profiles', {'id': name, 'user_id': name,
                    'height_cm': 170, 'gender_target': name, 'clothing_size': 'M',
                    'size_profile_json': {'clothing': {'original': 'M', 'system': 'LETTER'}}})
        _insert(connection, 'product_sources', {'id': 'source', 'code': 'synthetic_source'})
        for name, price, old_price in (('product', 1299, 1499), ('null-old', 100, None),
                                        ('int32-max', 2147483647, 2147483647),
                                        ('int32-min', -2147483648, -2147483648)):
            _insert(connection, 'products', {'id': name, 'external_id': name,
                'source_id': 'source', 'price': price, 'old_price': old_price,
                'title': 'Синтетическая блузка', 'available_sizes': ['M'],
                'raw_params_json': {'size': 'M'}})
        _insert(connection, 'outfits', {'id': 'outfit', 'user_id': 'womenswear',
            'total_price': 1399, 'items_json': [{'product_id': 'product'}, {'product_id': 'null-old'}]})
        _insert(connection, 'user_product_states', {'id': 'state', 'user_id': 'womenswear',
            'product_id': 'product'})
        _insert(connection, 'affiliate_clicks', {'id': 'click', 'user_id': 'womenswear',
            'product_id': 'product', 'source_id': 'source',
            'affiliate_url': 'https://example.test/synthetic'})


def snapshot(engine):
    """Capture values and schema so refused downgrades cannot partially pass."""
    import sqlalchemy as sa
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        rows = {table: [dict(row) for row in connection.execute(
            sa.text(f'SELECT * FROM {table} ORDER BY id')).mappings()] for table in TABLES}
        columns = {table: [(c['name'], str(c['type']), c['nullable'], c['default'])
                           for c in inspector.get_columns(table)] for table in TABLES}
        constraints = {table: {
            'pk': inspector.get_pk_constraint(table),
            'fk': inspector.get_foreign_keys(table),
            'unique': inspector.get_unique_constraints(table),
            'indexes': inspector.get_indexes(table),
        } for table in TABLES}
        return {'revision': connection.scalar(sa.text('SELECT version_num FROM alembic_version')),
                'rows': rows, 'columns': columns, 'constraints': constraints}


def assert_upgrade(engine, legacy):
    import sqlalchemy as sa
    current = snapshot(engine)
    require(current['revision'] == HEAD_REVISION, 'upgrade_revision_not_head')
    require(current['constraints'] == legacy['constraints'], 'upgrade_constraints_changed')
    for table in TABLES:
        for before, after in zip(legacy['rows'][table], current['rows'][table], strict=True):
            expected = dict(before)
            if table == 'users':
                expected['recommendation_revision'] = 0
            require(after == expected, 'upgrade_changed_legacy_data')
    for table, names in {'products': ('price', 'old_price'), 'outfits': ('total_price',)}.items():
        for name in names:
            column = next(c for c in current['columns'][table] if c[0] == name)
            require(column[1] == 'NUMERIC(14, 2)', 'upgrade_money_precision_mismatch')
            for row in current['rows'][table]:
                require(row[name] is None or isinstance(row[name], Decimal), 'money_not_decimal')
                if row[name] is not None:
                    require(row[name].as_tuple().exponent == -2, 'money_scale_not_two')
    gender = next(c for c in current['columns']['fit_profiles'] if c[0] == 'gender_target')
    require(gender[2] is True and gender[3] is None, 'gender_not_nullable_without_default')
    revision = next(c for c in current['columns']['users'] if c[0] == 'recommendation_revision')
    require(revision[2] is False and revision[3] in {'0', "'0'::integer"}, 'revision_default_invalid')
    with engine.begin() as connection:
        _insert(connection, 'fit_profiles', {'id': 'new', 'user_id': 'new', 'height_cm': 170})
        require(connection.scalar(sa.text("SELECT gender_target FROM fit_profiles WHERE id='new'")) is None,
                'new_gender_inferred')
        # This synthetic row only proves the new default; remove it before the
        # separate lossless rollback scenario, which contains no new data.
        connection.execute(sa.text("DELETE FROM fit_profiles WHERE id='new'"))


def run_scenario(admin, runtime, name, mutation=None):
    import sqlalchemy as sa
    with isolated_schema(admin, runtime) as schema:
        url = fixture_url(schema)
        engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
        try:
            migrate(url, 'upgrade', OLD_REVISION)
            seed_legacy(engine)
            legacy = snapshot(engine)
            require(legacy['revision'] == OLD_REVISION, 'legacy_revision_mismatch')
            migrate(url, 'upgrade', HEAD_REVISION)
            assert_upgrade(engine, legacy)
            if mutation is None:
                migrate(url, 'downgrade', OLD_REVISION)
                require(snapshot(engine) == legacy, 'lossless_downgrade_changed_legacy_snapshot')
                migrate(url, 'upgrade', HEAD_REVISION)
                assert_upgrade(engine, legacy)
                return {'scenario': name, 'status': 'passed', 'schema_removed': True,
                        'legacy_rows_retained': {table: len(rows) for table, rows in legacy['rows'].items()}}
            with engine.begin() as connection:
                mutation(connection)
                connection.execute(sa.text('UPDATE users SET recommendation_revision=7'))
            before_refusal = snapshot(engine)
            try:
                migrate(url, 'downgrade', OLD_REVISION)
            except RuntimeError as error:
                expected = 'unset user gender' if name == 'null_gender' else 'precision or range'
                require(expected in str(error), 'downgrade_unexpected_refusal')
            else:
                raise MigrationCheckError('unsafe_downgrade_not_refused')
            require(snapshot(engine) == before_refusal, 'refusal_left_partial_schema_or_data')
            return {'scenario': name, 'status': 'passed', 'schema_removed': True,
                    'head_revision_retained': True, 'recommendation_revision_retained': True,
                    'data_and_constraints_retained': True}
        finally:
            engine.dispose()


def run_checks(runtime):
    import sqlalchemy as sa
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config()
    config.set_main_option('script_location', str(WORKSPACE / 'backend' / 'alembic'))
    require(ScriptDirectory.from_config(config).get_heads() == [HEAD_REVISION],
            'release_head_changed_update_check_explicitly')
    admin = sa.create_engine(fixture_url(), poolclass=sa.pool.NullPool)
    started = time.perf_counter()
    try:
        with admin.connect() as connection:
            verify_server(connection, runtime)
        scenarios = [('lossless_round_trip', None)]
        for name, sql in (
            ('product_kopecks', "UPDATE products SET price=1299.50 WHERE id='product'"),
            ('old_price_kopecks', "UPDATE products SET old_price=1499.99 WHERE id='product'"),
            ('outfit_kopecks', 'UPDATE outfits SET total_price=1399.49'),
            ('null_gender', "UPDATE fit_profiles SET gender_target=NULL WHERE id='womenswear'"),
            ('int32_overflow', "UPDATE products SET price=2147483648 WHERE id='product'"),
            ('int32_underflow', 'UPDATE outfits SET total_price=-2147483649'),
        ):
            scenarios.append((name, lambda connection, sql=sql: connection.execute(sa.text(sql))))
        reports = [run_scenario(admin, runtime, name, mutation) for name, mutation in scenarios]
        return {'status': 'passed', 'old_revision': OLD_REVISION, 'head_revision': HEAD_REVISION,
                'real_alembic_env': True, 'base_metadata_create_all': False,
                'postgresql_port': PORT, 'production_access': False, 'scenarios': reports,
                'seconds': round(time.perf_counter() - started, 3)}
    finally:
        admin.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=WORKSPACE / '.tmp/alpha12-pg-runtime.json')
    args = parser.parse_args(argv)
    runtime = load_runtime(args.runtime)
    sys.path.insert(0, str(WORKSPACE / 'backend'))
    # Clear secrets before SQLAlchemy/Alembic/app imports. env.py is exercised
    # unchanged, including its transaction boundary, but dotenv is inert.
    with patch.dict(os.environ, isolated_environment('sqlite://'), clear=True), \
            patch('dotenv.load_dotenv', return_value=False):
        report = run_checks(runtime)
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, MigrationCheckError) else 'raw_error_omitted'
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'reason': reason}))
        raise SystemExit(1) from None

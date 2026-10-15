"""Reusable fixture for the opt-in disposable workspace PostgreSQL only.

No application DATABASE_URL or credentials are consulted. Verify the actual
server's data directory before creating a generated per-test schema. This module
contains no TestCase/mixin, so importing it cannot duplicate test discovery.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db import Base


@dataclass(frozen=True)
class IsolatedPostgres:
    admin: Engine
    engine: Engine
    schema: str


def create_isolated_postgres(case, *, namespace, pool_size=4):
    """Register cleanup immediately; callers retain their own seed/assertions."""
    case.assertRegex(namespace, r'^[a-z][a-z0-9_]{0,30}$')
    workspace = Path(__file__).resolve().parents[2]
    runtime = Path(os.environ['MODISH_ISOLATED_PG_RUNTIME']).resolve()
    case.assertTrue(runtime.is_relative_to(workspace / '.tmp'))
    metadata = json.loads(runtime.read_text(encoding='utf-8-sig'))
    directory = Path(metadata['data']).resolve()
    root = Path(metadata['root']).resolve()
    case.assertTrue(directory.is_relative_to(workspace / '.tmp'))
    case.assertTrue(root.is_relative_to(workspace / '.tmp'))
    case.assertTrue(root.name.startswith('alpha12-pg-'))
    case.assertEqual(directory, root / 'data')
    case.assertEqual(metadata['port'], 55439)
    url = 'postgresql+psycopg://modish_alpha_fixture@127.0.0.1:55439/postgres'
    admin = create_engine(url, connect_args={'connect_timeout': 3})
    case.addCleanup(admin.dispose)
    with admin.connect() as connection:
        case.assertEqual(Path(connection.scalar(text('SHOW data_directory'))).resolve(), directory)
        case.assertEqual(connection.scalar(text('SHOW port')), '55439')
    prefix = f'alpha12_{namespace}_'
    schema = prefix + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))

    def drop_schema():
        # Both parts of this identifier are generated/validated above. The
        # connection has already proved it belongs to the disposable cluster.
        case.assertTrue(schema.startswith(prefix))
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))

    case.addCleanup(drop_schema)
    engine = create_engine(url, pool_size=pool_size, max_overflow=0,
        connect_args={'connect_timeout': 3, 'options': f'-c search_path={schema} -c statement_timeout=5000'})
    case.addCleanup(engine.dispose)
    Base.metadata.create_all(engine)
    return IsolatedPostgres(admin, engine, schema)

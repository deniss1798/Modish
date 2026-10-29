"""Explicit private preparation, publication, recovery and status of refresh17.

This command never migrates a database. Deploy schema0029 and the pinned worker
image first. All stdout is bounded operational JSON, never merchant rows or URLs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def require(value, code):
    if not value:
        raise RuntimeError(code)


def runtime_binding(mode, run_id):
    """Validate before app.db imports dotenv or constructs its global engine."""
    require(sys.platform == 'linux' and os.environ.get('PYTHON_DOTENV_DISABLED') == '1'
        and not (BACKEND / '.env').exists() and not (BACKEND / '.env').is_symlink(),
        'refresh_linux_runtime_without_dotenv_required')
    require(os.environ.get('APP_ENV') == ('production' if mode == 'production' else 'test')
        and not any(key.upper().startswith('PG') for key in os.environ)
        and 'AUDIT_FIXED_NOW_UTC' not in os.environ, 'refresh_explicit_runtime_required')
    raw = os.environ.get('DATABASE_URL', '')
    require(raw and len(raw) <= 16384 and all(32 < ord(c) < 127 for c in raw)
        and '\\' not in raw, 'refresh_database_binding_required')
    value = urlsplit(raw)
    require(value.scheme in {'postgresql', 'postgresql+psycopg'} and value.port in (None, 5432)
        and value.password and not value.query and not value.fragment
        and '?' not in raw and '#' not in raw, 'refresh_database_binding_required')
    expected = ('modish_postgres', 'modish', 'modish_user') if mode == 'production' else (
        'modish_foundation_test_pg', 'modish_refresh17_' + run_id, 'refresh17_' + run_id)
    require((value.hostname, value.path.lstrip('/'), unquote(value.username or '')) == expected
        and value.path == '/' + expected[1], 'refresh_database_binding_required')
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'run', 'resume', 'status', 'abandon'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--mode', choices=('production', 'test'), required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--prepared-sha256')
    parser.add_argument('--reason-code')
    args = parser.parse_args(argv)
    require(re.fullmatch('[0-9a-f]{32}', args.run_id) and UUID(args.run_id).version == 4,
        'refresh_uuid4_hex_required')
    require((args.phase == 'prepare' and args.prepared_sha256 is None)
        or (args.phase != 'prepare' and args.prepared_sha256 is not None
            and re.fullmatch('[0-9a-f]{64}', args.prepared_sha256)), 'refresh_phase_arguments_required')
    require((args.phase == 'abandon' and args.reason_code is not None
        and re.fullmatch('[a-z][a-z0-9_]{0,63}', args.reason_code))
        or (args.phase != 'abandon' and args.reason_code is None), 'refresh_phase_reason_required')
    raw = runtime_binding(args.mode, args.run_id)
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from app.services.catalog.refresh_runner import (load_prepared, prepare_refresh,
        publish_snapshot, refresh_status, abandon_snapshot, SCHEMA)
    from sqlalchemy import text
    engine = create_engine(raw, future=True, poolclass=NullPool, hide_parameters=True,
        connect_args={'connect_timeout': 10, 'options': '-c statement_timeout=180000 -c lock_timeout=1000'})
    try:
        with engine.connect() as connection:
            require(connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA,
                'refresh_schema0029_required')
        if args.phase == 'prepare':
            return prepare_refresh(engine, args.directory, run_id=args.run_id, mode=args.mode)
        folder, prepared, snapshot = load_prepared(args.directory, args.prepared_sha256,
            mode=args.mode, verify_download=True,
            allow_expired_for_recovery=args.phase in {'status', 'abandon'})
        require(prepared['run_id'] == args.run_id, 'refresh_cli_run_binding_required')
        if args.phase == 'status':
            return refresh_status(engine, snapshot)
        if args.phase == 'abandon':
            return abandon_snapshot(engine, snapshot, folder, reason_code=args.reason_code)
        return publish_snapshot(engine, snapshot, folder, resume=args.phase == 'resume')
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        result = main()
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    except Exception as error:
        # No traceback: transport/SQL exceptions can embed credentials or rows.
        reason = 'catalogue_refresh_failed'
        if type(error).__name__ in {'RunnerRejected', 'SnapshotRejected', 'RefreshConflict', 'DownloadRejected', 'RuntimeError'}:
            candidate = str(error)
            if re.fullmatch('[a-z][a-z0-9_]{0,95}', candidate):
                reason = candidate
        print(json.dumps({'status': 'catalogue_refresh_requires_review', 'reason': reason,
            'automatic_database_restore': False, 'partial_batches_may_be_committed': True}))
        raise SystemExit(1)

"""Download and prepare privately, then publish bounded, recoverable batches.

Only the publisher writes catalogue rows. Preparing and inspecting a refresh
use read-only transactions; transport observations always come from downloaded
byte evidence. Credentials and merchant rows never enter public status reports.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ...models import CatalogRecord, CatalogSyncRun, Product, ProductSource, SourceRule
from ...integrations.admitad.feed_parser import _expand_admitad_param_column
from .refresh_ledger import CommitUncertain, RefreshConflict, ledger_row, refresh_runs, validate_ledger
from .refresh_snapshot import (SOURCE_CODES, MAX_SOURCE_RULES, SourceInput, SnapshotRejected,
    capture_product_baseline, capture_record_baseline, open_snapshot, source_fingerprint, stage_snapshot)
from .refresh_writer import BatchWriter, prepare_checkpoint

SCHEMA = '20261028_0029'
MAX_RUN_SECONDS = 7200
MAX_STATUS_BYTES = 65536


class RunnerRejected(RuntimeError):
    """A bounded reason without credentials, raw rows, SQL, or URLs."""


def _require(value, code):
    if not value:
        raise RunnerRejected(code)


def _run_id(value):
    try:
        parsed = UUID(value)
        _require(parsed.version == 4 and parsed.hex == value, 'refresh_uuid4_hex_required')
    except (ValueError, TypeError, AttributeError):
        raise RunnerRejected('refresh_uuid4_hex_required') from None
    return value


def _directory(path):
    path = Path(path).absolute()
    _require(path.is_dir() and not path.is_symlink() and path.resolve() == path,
        'canonical_refresh_directory_required')
    _require(os.name == 'nt' or path.stat().st_uid == os.geteuid()
        and path.stat().st_mode & 0o077 == 0, 'owned_private_refresh_directory_required')
    return path


def _syncdir(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def write_private(path, value, *, replace=False):
    path = Path(path)
    _directory(path.parent)
    if replace:
        _require(not path.is_symlink(), 'refresh_progress_symlink_forbidden')
    target = path.with_name('.' + path.name + '.' + uuid4().hex) if replace else path
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(target, path)
    _syncdir(path.parent)


def read_private(path, *, expected=None):
    path = Path(path)
    _directory(path.parent)
    _require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_STATUS_BYTES,
        'bounded_private_refresh_report_required')
    _require(os.name == 'nt' or path.stat().st_uid == os.geteuid()
        and path.stat().st_mode & 0o077 == 0, 'owned_private_refresh_report_required')
    raw = path.read_bytes()
    _require(len(raw) <= MAX_STATUS_BYTES, 'bounded_private_refresh_report_required')
    if expected is not None:
        _require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected)
            and hashlib.sha256(raw).hexdigest() == expected, 'refresh_report_hash_mismatch')
    return json.loads(raw)


def report_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _detached(row):
    return SimpleNamespace(**{column.name: deepcopy(getattr(row, column.name))
        for column in row.__table__.columns})


def _read_connection(engine):
    return engine.connect().execution_options(isolation_level='REPEATABLE READ')


def _readonly(db, *, validate_schema=True):
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '180000ms'"))
        db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
    if validate_schema:
        _require(db.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA,
            'refresh_schema0029_required')


def capture_sources(engine):
    """Copy just four source configurations; no transaction survives HTTP IO."""
    with _read_connection(engine) as connection, Session(bind=connection) as db, db.begin():
        _readonly(db)
        rows = list(db.scalars(select(ProductSource).where(ProductSource.code.in_(SOURCE_CODES))))
        _require(len(rows) == 4 and {row.code for row in rows} == set(SOURCE_CODES),
            'refresh_exact_four_sources_required')
        _require(not db.scalar(select(refresh_runs.c.id).where(refresh_runs.c.status == 'running').limit(1)),
            'refresh_unfinished_run_requires_resume')
        return tuple(_detached(row) for row in sorted(rows, key=lambda row: SOURCE_CODES.index(row.code)))


def capture_baseline(engine, sources):
    """Stream full row hashes in one read-only snapshot; retain no merchant blobs."""
    previous = {source.code: source for source in sources}
    result = {}
    with _read_connection(engine) as connection, Session(bind=connection) as db, db.begin():
        _readonly(db)
        last_refresh = db.execute(select(refresh_runs).where(refresh_runs.c.status == 'complete')
            .order_by(refresh_runs.c.updated_at.desc(), refresh_runs.c.id.desc()).limit(1)).mappings().one_or_none()
        for code in SOURCE_CODES:
            source = db.scalar(select(ProductSource).where(ProductSource.code == code))
            _require(source is not None and source_fingerprint(source) == source_fingerprint(previous[code]),
                'refresh_source_changed_during_download')
            rules = list(db.scalars(select(SourceRule).where(SourceRule.source_id == source.id)
                .order_by(SourceRule.id).limit(MAX_SOURCE_RULES + 1)))
            _require(len(rules) <= MAX_SOURCE_RULES, 'refresh_source_rules_bound')
            products = capture_product_baseline(db.scalars(select(Product).where(Product.source == code)
                .order_by(Product.id).execution_options(yield_per=100)), code=code, source_id=source.id)
            records = capture_record_baseline(db.scalars(select(CatalogRecord)
                .where(CatalogRecord.source_id == source.id).order_by(CatalogRecord.id)
                .execution_options(yield_per=100)), source_id=source.id)
            last = db.scalar(select(CatalogSyncRun).where(CatalogSyncRun.source_id == source.id,
                CatalogSyncRun.status == 'ok').order_by(CatalogSyncRun.finished_at.desc(),
                CatalogSyncRun.started_at.desc(), CatalogSyncRun.id.desc()).limit(1))
            measured = {} if last_refresh is None else (last_refresh['source_states_json']
                .get(source.id, {}).get('snapshot_input') or {})
            if measured:
                _require(measured.get('code') == code and all(type(measured.get(key)) is int
                    and measured[key] >= 0 for key in ('total_received', 'distinct_received', 'available_quality')),
                    'refresh_previous_snapshot_facts_invalid')
            result[code] = {'source': _detached(source), 'products': products, 'records': records,
                'rules': tuple(_detached(rule) for rule in rules),
                'previous_received': max(last.total_received if last is not None else 0,
                    measured.get('total_received', 0)),
                'previous_distinct': measured.get('distinct_received', 0),
                'previous_available_quality': measured.get('available_quality', 0)}
    return result


def iter_feed_rows(path, expected_sha256, expected_bytes):
    """Stream the fixed Admitad semicolon format using its existing param expansion."""
    path = Path(path)
    _require(path.is_file() and not path.is_symlink(), 'refresh_csv_regular_file_required')
    _require(type(expected_bytes) is int and 0 < expected_bytes <= 256 * 1024**2
        and re.fullmatch('[0-9a-f]{64}', expected_sha256), 'refresh_csv_evidence_required')
    digest, received = hashlib.sha256(), 0
    with path.open('rb') as stream:
        def lines():
            nonlocal received
            for index, raw in enumerate(stream):
                received += len(raw)
                _require(received <= expected_bytes, 'refresh_csv_byte_bound')
                digest.update(raw)
                yield raw.decode('utf-8-sig' if index == 0 else 'utf-8')
        reader = csv.reader(lines(), delimiter=';', strict=True)
        header = next(reader, None)
        _require(header and len(header) <= 512 and len(set(header)) == len(header)
            and {'id', 'name', 'available', 'price', 'param'} <= set(header), 'refresh_csv_header_required')
        for index, row in enumerate(reader, 1):
            _require(index <= 100000, 'refresh_csv_row_bound')
            if not row or not any(row):
                continue
            _require(len(row) == len(header), 'refresh_csv_row_width_required')
            yield _expand_admitad_param_column(dict(zip(header, row)))
    _require(received == expected_bytes and digest.hexdigest() == expected_sha256,
        'refresh_parsed_bytes_hash_mismatch')


def prepare_refresh(engine, directory, *, run_id, mode, downloader=None, policy=None):
    """Create one exclusive attempt; neither downloading nor staging writes SQL."""
    _run_id(run_id)
    _require(mode in {'production', 'test'}, 'refresh_explicit_mode_required')
    _require(policy is None or mode == 'test', 'production_refresh_policy_override_forbidden')
    parent = _directory(directory)
    folder = parent / ('run-' + run_id)
    folder.mkdir(mode=0o700)
    _syncdir(parent)
    feeds = folder / 'feeds'
    feeds.mkdir(mode=0o700)
    sources = capture_sources(engine)
    if downloader is None:
        from .refresh_download import download_inputs
        downloader = download_inputs
    manifest = downloader(sources, feeds, run_id=run_id)
    baseline = capture_baseline(engine, sources)
    _require(manifest.run_id == run_id and {feed.code for feed in manifest.feeds} == set(SOURCE_CODES)
        and len(manifest.feeds) == 4, 'refresh_download_source_set_required')
    inputs = []
    for feed in manifest.feeds:
        captured = baseline[feed.code]
        inputs.append(SourceInput(rows=iter_feed_rows(feed.path, feed.sha256, feed.bytes), observed_at=feed.observed_at,
            finished_at=feed.finished_at, feed_sha256=feed.sha256, **captured))
    snapshot = stage_snapshot(folder / 'snapshot.sqlite', run_id=run_id, sources=inputs, policy=policy)
    prepared = {'schema_version': 1, 'status': 'catalogue_refresh_prepared', 'mode': mode,
        'schema': SCHEMA, 'run_id': run_id, 'snapshot_sha256': snapshot.sha256,
        'download_manifest_sha256': manifest.sha256, 'operation_count': snapshot.operation_count,
        'prepared_at': datetime.now(timezone.utc).isoformat(), 'database_writes': False}
    write_private(folder / 'prepared.json', prepared)
    return {**prepared, 'directory': str(folder), 'prepared_sha256': report_sha(folder / 'prepared.json')}


def load_prepared(directory, expected_sha256, *, mode, verify_download=True, allow_expired_for_recovery=False):
    folder = _directory(directory)
    prepared = read_private(folder / 'prepared.json', expected=expected_sha256)
    _require(prepared.get('schema_version') == 1 and prepared.get('status') == 'catalogue_refresh_prepared'
        and prepared.get('mode') == mode and prepared.get('schema') == SCHEMA
        and folder.name == 'run-' + _run_id(prepared.get('run_id')), 'refresh_prepared_binding_required')
    snapshot = open_snapshot(folder / 'snapshot.sqlite', prepared['snapshot_sha256'])
    _require(snapshot.run_id.replace('-', '') == prepared['run_id']
        and snapshot.operation_count == prepared['operation_count'], 'refresh_snapshot_binding_required')
    if verify_download:
        from .refresh_download import load_downloads
        manifest = load_downloads(folder / 'feeds', prepared['download_manifest_sha256'],
            allow_expired_for_recovery=allow_expired_for_recovery)
        _require(manifest.run_id == prepared['run_id'], 'refresh_download_binding_required')
        evidence = {feed.code: feed for feed in manifest.feeds}
        _require(all(entry['feed_sha256'] == evidence[entry['code']].sha256
            and datetime.fromisoformat(entry['observed_at']) == evidence[entry['code']].observed_at
            for entry in snapshot.sources), 'refresh_snapshot_observation_binding_required')
    return folder, prepared, snapshot


def _position(writer, snapshot):
    with Session(bind=writer.connection) as db:
        row = ledger_row(db, snapshot.run_id)
        return None if row is None else dict(validate_ledger(row, snapshot))


def _retryable(error):
    if isinstance(error, DBAPIError):
        return getattr(error.orig, 'sqlstate', None) in {'55P03', '57014', '40001', '40P01'}
    return isinstance(error, RefreshConflict) and str(error) == 'catalogue_refresh_batch_deadline'


def publish_snapshot(engine, snapshot, directory, *, resume=False, writer_factory=BatchWriter,
        deadline_seconds=MAX_RUN_SECONDS, monotonic_fn=monotonic, sleep_fn=sleep,
        now_fn=lambda: datetime.now(timezone.utc)):
    """Resolve a saved exact commit intent before any continuation or replay."""
    folder = _directory(directory)
    _require(0 < deadline_seconds <= MAX_RUN_SECONDS, 'bounded_refresh_deadline_required')
    progress_path = folder / 'publication.json'
    if resume:
        progress = read_private(progress_path)
        _require(progress.get('run_id') == snapshot.run_id and progress.get('snapshot_sha256') == snapshot.sha256,
            'refresh_publication_binding_required')
        _require(progress.get('status') not in {'abandoned_before_initialization',
            'interrupted', 'abandon_requires_resolution'} and progress.get('abandon_reason') is None,
            'refresh_abandoned_run_cannot_resume')
    else:
        first_started = now_fn()
        progress = {'run_id': snapshot.run_id, 'snapshot_sha256': snapshot.sha256,
            'status': 'starting', 'pending': None, 'initialized': False,
            'first_started_at': first_started.isoformat(),
            'deadline_at': (first_started + timedelta(seconds=deadline_seconds)).isoformat()}
        write_private(progress_path, progress)
    try:
        first_started = datetime.fromisoformat(progress['first_started_at'])
        deadline_at = datetime.fromisoformat(progress['deadline_at'])
        _require(first_started.tzinfo is not None and deadline_at.tzinfo is not None
            and 0 < (deadline_at-first_started).total_seconds() <= MAX_RUN_SECONDS,
            'refresh_persisted_deadline_required')
    except (ValueError, TypeError, KeyError):
        raise RunnerRejected('refresh_persisted_deadline_required') from None
    started, retries = monotonic_fn(), 0

    def save(**changes):
        progress.update(changes, updated_at=datetime.now(timezone.utc).isoformat())
        write_private(progress_path, progress, replace=True)

    while True:
        _require(monotonic_fn() - started < deadline_seconds and first_started <= now_fn() < deadline_at,
            'refresh_publication_deadline')
        try:
            with writer_factory(engine, snapshot) as writer:
                if progress['pending'] is not None:
                    outcome = writer.resolve(progress['pending'])
                    _require(outcome in {'committed', 'not_committed'}, 'refresh_commit_resolution_required')
                    initialized = progress['initialized'] or progress['pending'].get('phase') != 'initialize' or outcome == 'committed'
                    save(pending=None, initialized=initialized, last_resolution=outcome, status='running')
                position = _position(writer, snapshot)
                if not progress['initialized']:
                    _require(position is None, 'refresh_existing_ledger_requires_resume')
                    save(pending=prepare_checkpoint(snapshot, None, 'initialize'), status='running')
                    writer.initialize()
                    save(pending=None, initialized=True)
                while True:
                    _require(monotonic_fn() - started < deadline_seconds and first_started <= now_fn() < deadline_at,
                        'refresh_publication_deadline')
                    position = writer.position()
                    _require(position['status'] in {'running', 'complete'}, 'refresh_interrupted_run_requires_review')
                    if position['cursor'] < snapshot.operation_count:
                        batch = snapshot.read_batch(position['cursor'])
                        checkpoint = prepare_checkpoint(snapshot, position, 'batch', batch=batch)
                        operation = writer.next_batch
                    elif position['finalized_sources'] < len(snapshot.sources):
                        checkpoint = prepare_checkpoint(snapshot, position, 'finalize')
                        operation = writer.finalize_source
                    else:
                        _require(position['status'] == 'complete', 'refresh_final_state_required')
                        save(status='complete', pending=None, cursor=position['cursor'],
                            finalized_sources=position['finalized_sources'])
                        return {'status': 'catalogue_refresh_complete', 'run_id': snapshot.run_id,
                            'snapshot_sha256': snapshot.sha256, 'cursor': position['cursor'],
                            'finalized_sources': position['finalized_sources'], 'counts': position['counts_json']}
                    save(pending=checkpoint)
                    actual = operation()
                    _require(actual == checkpoint, 'refresh_actual_checkpoint_mismatch')
                    save(pending=None)
        except CommitUncertain as error:
            _require(progress['pending'] == error.checkpoint, 'refresh_uncertain_checkpoint_mismatch')
            save(status='commit_requires_resolution')
        except (DBAPIError, RefreshConflict) as error:
            if not _retryable(error):
                raise
            # All transaction-body exceptions roll back inside BatchWriter.
            # The durable pending checkpoint is resolved after reacquiring its
            # session lock even when this failure was a known rollback.
            save(status='retryable_rollback')
        retries += 1
        _require(retries <= 3, 'refresh_bounded_retries_exhausted')
        sleep_fn(.1 * retries)


def abandon_snapshot(engine, snapshot, directory, *, reason_code, writer_factory=BatchWriter):
    """Explicit recovery may close an expired run, never apply its expired rows."""
    _require(isinstance(reason_code, str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', reason_code),
        'refresh_abandon_reason_required')
    folder = _directory(directory)
    path = folder / 'publication.json'
    _require(path.is_file(), 'refresh_no_publication_to_abandon')
    progress = read_private(path)
    _require(progress.get('run_id') == snapshot.run_id and progress.get('snapshot_sha256') == snapshot.sha256,
        'refresh_publication_binding_required')
    _require(progress.get('abandon_reason') in (None, reason_code), 'refresh_abandon_reason_changed')

    def save(**changes):
        progress.update(changes, updated_at=datetime.now(timezone.utc).isoformat())
        write_private(path, progress, replace=True)

    with writer_factory(engine, snapshot) as writer:
        if progress.get('pending') is not None:
            outcome = writer.resolve(progress['pending'])
            _require(outcome in {'committed', 'not_committed'}, 'refresh_commit_resolution_required')
            save(pending=None, last_resolution=outcome)
        position = _position(writer, snapshot)
        if position is None:
            _require(progress.get('initialized') is False,
                'refresh_initialized_ledger_missing_requires_review')
            save(status='abandoned_before_initialization', pending=None, abandon_reason=reason_code)
            return {'status': 'catalogue_refresh_abandoned_before_initialization',
                'run_id': snapshot.run_id, 'snapshot_sha256': snapshot.sha256,
                'reason': reason_code, 'partial_batches_may_be_committed': False}
        _require(position['status'] in {'running', 'interrupted'}, 'refresh_complete_run_cannot_be_abandoned')
        if position['status'] == 'running':
            checkpoint = prepare_checkpoint(snapshot, position, 'abandon', reason_code=reason_code)
            save(pending=checkpoint, abandon_reason=reason_code, status='abandon_requires_resolution')
            try:
                actual = writer.abandon(reason_code)
            except CommitUncertain as error:
                _require(progress['pending'] == error.checkpoint, 'refresh_uncertain_checkpoint_mismatch')
                # Preserve the same exact intent. A later explicit abandon
                # reacquires the lock and resolves this acknowledgement first.
                raise
            _require(actual == checkpoint, 'refresh_actual_checkpoint_mismatch')
        save(status='interrupted', pending=None, abandon_reason=reason_code)
    return {'status': 'catalogue_refresh_interrupted', 'run_id': snapshot.run_id,
        'snapshot_sha256': snapshot.sha256, 'reason': reason_code,
        'partial_batches_may_be_committed': True}


def refresh_status(engine, snapshot):
    with _read_connection(engine) as connection, Session(bind=connection) as db, db.begin():
        _readonly(db)
        row = ledger_row(db, snapshot.run_id)
        if row is None:
            return {'status': 'prepared_not_published', 'run_id': snapshot.run_id}
        row = validate_ledger(row, snapshot)
        return {'status': row['status'], 'run_id': snapshot.run_id, 'cursor': row['cursor'],
            'operation_count': row['operation_count'], 'finalized_sources': row['finalized_sources'],
            'updated_at': row['updated_at'].isoformat()}

"""Short catalogue publication transactions for a prepared immutable snapshot.

Normalization, downloading and file IO belong outside this module's SQL
transactions. One pinned PostgreSQL connection owns the session advisory lock
across all commits. This worker never takes a User lock or changes reader locks.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from time import monotonic
from uuid import UUID, uuid5

from sqlalchemy import DateTime, Numeric, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ...models import CatalogRecord, CatalogSyncRun, Product, ProductSource, SourceRule
from .refresh_ledger import (CommitUncertain, RefreshConflict, ledger_row,
    refresh_runs, resolve_checkpoint, utc_now, validate_ledger)
from .refresh_snapshot import encoded, row_fingerprint, rules_fingerprint, source_fingerprint
from .stock_freshness import MAX_STOCK_AGE, _utc

_LOCK = (20261028, 17)
_BATCH_SECONDS = .5
_COUNT_KEYS = ('created', 'updated', 'deactivated', 'skipped')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), default=str).encode()).hexdigest()


def prepare_checkpoint(snapshot, position, phase, *, batch=None, reason_code=None):
    """Plan durable commit evidence before a caller starts a write transaction."""
    if phase == 'initialize':
        return {'phase': 'initialize'}
    if phase == 'batch' and batch is not None:
        if position['cursor'] != batch.start_cursor:
            raise RefreshConflict('catalogue_refresh_cursor_changed')
        return {'position_column': 'cursor', 'before': batch.start_cursor,
            'after': batch.end_cursor, 'batch_sha256': _digest(batch.operations)}
    if phase == 'finalize':
        index = position['finalized_sources']
        if not 0 <= index < len(snapshot.sources):
            raise RefreshConflict('catalogue_refresh_finalization_position_invalid')
        return {'position_column': 'finalized_sources', 'before': index, 'after': index + 1,
            'batch_sha256': _digest({'finalize_source': _source_id(snapshot.sources[index]),
                'snapshot': snapshot.sha256})}
    if phase == 'abandon' and isinstance(reason_code, str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', reason_code):
        return {'phase': 'abandon', 'cursor': position['cursor'],
            'finalized_sources': position['finalized_sources'],
            'batch_sha256': _digest({'abandon': reason_code, 'snapshot': snapshot.sha256}),
            'previous_batch_sha256': position['last_batch_sha256']}
    raise RefreshConflict('catalogue_refresh_checkpoint_phase_invalid')


def _source_id(entry):
    return entry.get('id', entry.get('source_id'))


def _sync_id(snapshot, source_id):
    return str(uuid5(UUID(snapshot.run_id), 'refresh17:sync:' + source_id))


def _fresh(entry):
    observed = datetime.fromisoformat(entry['observed_at'])
    now = utc_now()
    if observed.tzinfo is None or not now - MAX_STOCK_AGE <= _utc(observed) <= now:
        raise RefreshConflict('refresh_snapshot_observation_expired_or_invalid')


def _bounded(db):
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text("SET LOCAL lock_timeout = '25ms'"))
        db.execute(text("SET LOCAL statement_timeout = '250ms'"))
        db.execute(text("SET LOCAL idle_in_transaction_session_timeout = '2000ms'"))


def _deadline(started):
    if monotonic() - started > _BATCH_SECONDS:
        raise RefreshConflict('catalogue_refresh_batch_deadline')


def _lock_source(db, entry, expected, *, final=False):
    source_id = _source_id(entry)
    source = db.scalar(select(ProductSource).where(ProductSource.id == source_id)
        .with_for_update(read=not final, nowait=True).execution_options(populate_existing=True))
    if (source is None or source.code != entry['code']
            or source_fingerprint(source) != expected['source_sha256']):
        raise RefreshConflict('catalogue_refresh_source_changed')
    rules = list(db.scalars(select(SourceRule).where(SourceRule.source_id == source_id)
        .order_by(SourceRule.id).with_for_update(read=True, nowait=True)
        .execution_options(populate_existing=True)))
    if rules_fingerprint(rules) != expected['rules_sha256']:
        raise RefreshConflict('catalogue_refresh_source_rules_changed')
    _fresh(entry)
    return source


def _decode_values(table, values):
    if not isinstance(values, dict) or not set(values) <= set(table.c.keys()):
        raise RefreshConflict('catalogue_refresh_unknown_staged_column')
    result = deepcopy(values)
    for name, value in result.items():
        if value is None:
            continue
        column = table.c[name]
        if isinstance(column.type, DateTime) and isinstance(value, str):
            result[name] = datetime.fromisoformat(value)
        elif isinstance(column.type, Numeric) and isinstance(value, str):
            result[name] = Decimal(value)
    return result


def _prepare_operations(batch):
    # Decode bytes/dates before any production row locks are acquired.
    if (not 1 <= len(batch.operations) <= 25
            or batch.end_cursor != batch.start_cursor + len(batch.operations)
            or sum(len(encoded(operation)) for operation in batch.operations) > 1024 * 1024):
        raise RefreshConflict('catalogue_refresh_batch_bound')
    return [dict(operation,
        product_values=_decode_values(Product.__table__, operation.get('product_values') or {}),
        record_values=_decode_values(CatalogRecord.__table__, operation.get('record_values') or {}))
        for operation in batch.operations]


def _locked_objects(db, model, operations, key):
    ids = sorted({operation[key] for operation in operations if operation.get(key)})
    if not ids:
        return {}
    return {row.id: row for row in db.scalars(select(model).where(model.id.in_(ids))
        .order_by(model.id).with_for_update(nowait=True).execution_options(populate_existing=True))}


def _check_row(row, expected, *, source_id, source_code, product=False):
    if (row is None) != (expected is None):
        raise RefreshConflict('catalogue_refresh_row_presence_changed')
    source_matches = row is None or row.source_id == source_id or product and row.source_id is None
    if row is not None and (row_fingerprint(row) != expected or not source_matches
            or product and row.source != source_code):
        raise RefreshConflict('catalogue_refresh_row_changed')


def _assign(db, model, row, identity, values):
    if not identity or not values:
        return
    if any(name in values for name in ('id', 'created_at') if row is not None):
        raise RefreshConflict('catalogue_refresh_existing_identity_rewrite')
    if row is None:
        db.add(model(id=identity, **values))
    else:
        for name, value in values.items():
            setattr(row, name, value)


def _validate_identity_values(operation, entry, product, record):
    product_values, record_values = operation['product_values'], operation['record_values']
    for name, wanted in (('source', entry['code']), ('source_id', _source_id(entry))):
        if name in product_values and product_values[name] != wanted:
            raise RefreshConflict('catalogue_refresh_product_identity_rewrite')
    if (product is not None and 'external_id' in product_values
            and product_values['external_id'] != product.external_id):
        raise RefreshConflict('catalogue_refresh_product_identity_rewrite')
    if 'source_id' in record_values and record_values['source_id'] != _source_id(entry):
        raise RefreshConflict('catalogue_refresh_record_identity_rewrite')
    if (record is not None and 'raw_key' in record_values
            and record_values['raw_key'] != record.raw_key):
        raise RefreshConflict('catalogue_refresh_record_identity_rewrite')


def _empty_counts():
    return {**{key: 0 for key in _COUNT_KEYS}, 'reasons': {}}


def _add_counts(counts, operation):
    # Staged operations report facts without overwriting public skip_breakdown
    # with ledger checkpoints, credentials or transport diagnostics.
    incoming = operation.get('counts') or {}
    for key in _COUNT_KEYS:
        value = incoming.get(key, 0)
        if type(value) is not int or value < 0:
            raise RefreshConflict('catalogue_refresh_invalid_counter')
        counts[key] += value
    for key, value in incoming.get('reasons', {}).items():
        if not isinstance(key, str) or type(value) is not int or value < 0:
            raise RefreshConflict('catalogue_refresh_invalid_reason_counter')
        counts['reasons'][key] = counts['reasons'].get(key, 0) + value


def _update_public_counts(db, run_id, counts):
    db.execute(update(CatalogSyncRun).where(CatalogSyncRun.id == run_id).values(
        created_count=counts['created'], updated_count=counts['updated'],
        deactivated_count=counts['deactivated'], skipped_count=counts['skipped'],
        ingested_count=counts['created'] + counts['updated'], skip_breakdown=counts['reasons']))


def apply_batch(db, snapshot, batch, operations, *, started):
    """Apply one already decoded batch in the caller's single transaction."""
    _bounded(db)
    ledger = validate_ledger(ledger_row(db, snapshot.run_id, lock=True), snapshot)
    if ledger['status'] != 'running' or ledger['cursor'] != batch.start_cursor:
        raise RefreshConflict('catalogue_refresh_cursor_changed')
    entries = {_source_id(entry): entry for entry in snapshot.sources}
    entry = entries[batch.source_id]
    _lock_source(db, entry, ledger['source_states_json'][batch.source_id])
    products = _locked_objects(db, Product, operations, 'product_id')
    records = _locked_objects(db, CatalogRecord, operations, 'record_id')
    counts = deepcopy(ledger['counts_json'])
    pending_records = []
    for operation in operations:
        _deadline(started)
        if operation.get('source_id') != batch.source_id or operation.get('source_code') != entry['code']:
            raise RefreshConflict('catalogue_refresh_cross_source_operation')
        product = products.get(operation.get('product_id'))
        record = records.get(operation.get('record_id'))
        _check_row(product, operation.get('expected_product_hash'), source_id=batch.source_id,
            source_code=entry['code'], product=True)
        _check_row(record, operation.get('expected_record_hash'), source_id=batch.source_id,
            source_code=entry['code'])
        _validate_identity_values(operation, entry, product, record)
        _assign(db, Product, product, operation.get('product_id'), operation['product_values'])
        pending_records.append((operation, record))
        _add_counts(counts[batch.source_id], operation)
    # Explicitly create products before records that reference them. The models
    # have no ORM relationship to order those inserts for us.
    db.flush([row for row in db.new | db.dirty if isinstance(row, Product)])
    for operation, record in pending_records:
        _deadline(started)
        _assign(db, CatalogRecord, record, operation.get('record_id'), operation['record_values'])
    db.flush()
    _deadline(started)
    checkpoint = prepare_checkpoint(snapshot, ledger, 'batch', batch=batch)
    batch_sha = checkpoint['batch_sha256']
    db.execute(update(refresh_runs).where(refresh_runs.c.id == snapshot.run_id).values(
        cursor=batch.end_cursor, last_batch_sha256=batch_sha, counts_json=counts, updated_at=utc_now()))
    _update_public_counts(db, _sync_id(snapshot, batch.source_id), counts[batch.source_id])
    _deadline(started)
    return checkpoint


class BatchWriter:
    """One physical connection and advisory lock for the whole resumable run."""
    def __init__(self, engine, snapshot):
        if engine.dialect.name != 'postgresql':
            raise ValueError('catalogue_refresh_requires_postgresql')
        self.engine, self.snapshot = engine, snapshot
        self.connection = None

    def __enter__(self):
        self.connection = self.engine.connect().execution_options(isolation_level='READ COMMITTED')
        try:
            owned = self.connection.scalar(text('SELECT pg_try_advisory_lock(:a,:b)'),
                {'a': _LOCK[0], 'b': _LOCK[1]})
            self.connection.commit()
            if not owned:
                raise RefreshConflict('catalogue_refresh_worker_already_running')
            return self
        except BaseException:
            # Acquiring the session lock may have succeeded even when its
            # acknowledgement/commit failed. Discard the physical connection;
            # close alone can put a locked session back into the pool.
            self.connection.invalidate()
            self.connection.close()
            self.connection = None
            raise

    def __exit__(self, *_):
        if self.connection is not None:
            try:
                self.connection.rollback()
                self.connection.execute(text('SELECT pg_advisory_unlock(:a,:b)'),
                    {'a': _LOCK[0], 'b': _LOCK[1]})
                self.connection.commit()
            except BaseException:
                # Never return a pooled physical connection with a session lock.
                self.connection.invalidate()
            finally:
                self.connection.close()
                self.connection = None

    def _session(self):
        if self.connection is None or self.connection.invalidated:
            raise RefreshConflict('catalogue_refresh_connection_must_be_reacquired')
        return Session(bind=self.connection, autoflush=False, expire_on_commit=False)

    def _commit(self, db, checkpoint):
        try:
            db.commit()
        except BaseException as error:
            self.connection.invalidate()
            if isinstance(error, DBAPIError):
                raise CommitUncertain(checkpoint) from None
            raise

    def initialize(self):
        with self._session() as db:
            try:
                db.begin()
                _bounded(db)
                existing = ledger_row(db, self.snapshot.run_id, lock=True)
                if existing is not None:
                    result = dict(validate_ledger(existing, self.snapshot))
                    db.rollback()
                    return result
                now = utc_now()
                states, counts = {}, {}
                for entry in sorted(self.snapshot.sources, key=_source_id):
                    source_id = _source_id(entry)
                    states[source_id] = {key: entry[key] for key in ('source_sha256', 'rules_sha256')}
                    # Retain measured input facts for the next quality-loss
                    # comparison, linked to this ledger's sealed snapshot hash.
                    input_keys = ('code', 'total_received', 'distinct_received', 'available_quality',
                        'observed_at', 'feed_sha256')
                    if all(key in entry for key in input_keys):
                        states[source_id]['snapshot_input'] = {key: entry[key] for key in input_keys}
                    _lock_source(db, entry, states[source_id])
                    counts[source_id] = _empty_counts()
                    db.add(CatalogSyncRun(id=_sync_id(self.snapshot, source_id), source_id=source_id,
                        status='running', total_received=entry['total_received'], started_at=now,
                        created_count=0, updated_count=0, deactivated_count=0, skipped_count=0,
                        ingested_count=0, skip_breakdown={}))
                db.execute(insert(refresh_runs).values(id=self.snapshot.run_id,
                    snapshot_sha256=self.snapshot.sha256, operation_count=self.snapshot.operation_count,
                    cursor=0, finalized_sources=0, last_batch_sha256='', status='running',
                    counts_json=counts, source_states_json=states, started_at=now, updated_at=now))
                db.flush()
            except BaseException:
                db.rollback()
                raise
            self._commit(db, prepare_checkpoint(self.snapshot, None, 'initialize'))
            return dict(validate_ledger(ledger_row(db, self.snapshot.run_id), self.snapshot))

    def position(self):
        with self._session() as db:
            return dict(validate_ledger(ledger_row(db, self.snapshot.run_id), self.snapshot))

    def resolve(self, checkpoint):
        if checkpoint.get('phase') == 'initialize':
            with self._session() as db:
                row = ledger_row(db, self.snapshot.run_id)
                if row is None:
                    return 'not_committed'
                validate_ledger(row, self.snapshot)
                if (row['cursor'] == row['finalized_sources'] == 0
                        and row['status'] == 'running' and row['last_batch_sha256'] == ''):
                    return 'committed'
                raise RefreshConflict('catalogue_refresh_initialization_state_diverged')
        if checkpoint.get('phase') == 'abandon':
            row = self.position()
            if row['status'] == 'interrupted' and row['last_batch_sha256'] == checkpoint['batch_sha256']:
                return 'committed'
            if (row['status'] == 'running' and row['cursor'] == checkpoint['cursor']
                    and row['finalized_sources'] == checkpoint['finalized_sources']
                    and row['last_batch_sha256'] == checkpoint['previous_batch_sha256']):
                return 'not_committed'
            raise RefreshConflict('catalogue_refresh_abandon_state_diverged')
        return resolve_checkpoint(self.position(), checkpoint)

    def next_batch(self):
        position = self.position()
        if position['status'] not in {'running', 'complete'}:
            raise RefreshConflict('catalogue_refresh_run_interrupted')
        if position['cursor'] == self.snapshot.operation_count:
            return None
        batch = self.snapshot.read_batch(position['cursor'], max_items=25, max_bytes=1024 * 1024)
        operations = _prepare_operations(batch)
        with self._session() as db:
            try:
                db.begin()
                checkpoint = apply_batch(db, self.snapshot, batch, operations, started=monotonic())
            except BaseException:
                db.rollback()
                raise
            self._commit(db, checkpoint)
        return checkpoint

    def finalize_source(self):
        """Use a separate tiny exclusive-source transaction, never a lock upgrade."""
        with self._session() as db:
            try:
                db.begin()
                _bounded(db)
                started = monotonic()
                ledger = validate_ledger(ledger_row(db, self.snapshot.run_id, lock=True), self.snapshot)
                if ledger['status'] not in {'running', 'complete'}:
                    raise RefreshConflict('catalogue_refresh_run_interrupted')
                if ledger['cursor'] != self.snapshot.operation_count:
                    raise RefreshConflict('catalogue_refresh_unpublished_rows_remain')
                index = ledger['finalized_sources']
                if index == len(self.snapshot.sources):
                    db.rollback()
                    return None
                entry = self.snapshot.sources[index]
                source_id = _source_id(entry)
                if ledger['counts_json'][source_id] != entry['stats']:
                    raise RefreshConflict('catalogue_refresh_source_totals_diverged')
                source = _lock_source(db, entry, ledger['source_states_json'][source_id], final=True)
                source.status = 'ok'
                source.last_sync_at = datetime.fromisoformat(entry['observed_at'])
                source.updated_at = utc_now()
                db.flush([source])
                states = deepcopy(ledger['source_states_json'])
                states[source_id]['source_sha256'] = source_fingerprint(source)
                checkpoint = prepare_checkpoint(self.snapshot, ledger, 'finalize')
                batch_sha = checkpoint['batch_sha256']
                db.execute(update(CatalogSyncRun).where(CatalogSyncRun.id == _sync_id(self.snapshot, source_id))
                    .values(status='ok', finished_at=utc_now()))
                db.execute(update(refresh_runs).where(refresh_runs.c.id == self.snapshot.run_id).values(
                    finalized_sources=index + 1, last_batch_sha256=batch_sha, source_states_json=states,
                    status='complete' if index + 1 == len(self.snapshot.sources) else 'running',
                    updated_at=utc_now()))
                _deadline(started)
            except BaseException:
                db.rollback()
                raise
            self._commit(db, checkpoint)
        return checkpoint

    def abandon(self, reason_code):
        """Record an explicitly abandoned partial run without rewriting stock.

        Call only after resolving any uncertain COMMIT. A new download/snapshot
        needs a new run ID. Only non-secret reason codes enter public sync stats.
        """
        if not isinstance(reason_code, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', reason_code):
            raise ValueError('catalogue_refresh_bounded_reason_code_required')
        with self._session() as db:
            try:
                db.begin()
                _bounded(db)
                ledger = validate_ledger(ledger_row(db, self.snapshot.run_id, lock=True), self.snapshot)
                if ledger['status'] == 'complete':
                    raise RefreshConflict('completed_catalogue_refresh_cannot_be_abandoned')
                checkpoint = prepare_checkpoint(self.snapshot, ledger, 'abandon', reason_code=reason_code)
                batch_sha = checkpoint['batch_sha256']
                for entry in self.snapshot.sources[ledger['finalized_sources']:]:
                    source_id = _source_id(entry)
                    counts = ledger['counts_json'][source_id]
                    partial = any(counts[key] for key in ('created', 'updated', 'deactivated'))
                    db.execute(update(CatalogSyncRun).where(CatalogSyncRun.id == _sync_id(self.snapshot, source_id))
                        .values(status='partial' if partial else 'error', finished_at=utc_now(),
                            error_message=reason_code))
                db.execute(update(refresh_runs).where(refresh_runs.c.id == self.snapshot.run_id)
                    .values(status='interrupted', last_batch_sha256=batch_sha, updated_at=utc_now()))
            except BaseException:
                db.rollback()
                raise
            self._commit(db, checkpoint)
        return checkpoint

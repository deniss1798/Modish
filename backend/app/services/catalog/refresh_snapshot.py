"""Prepare a sealed, local refresh plan before holding catalogue publication locks.

The snapshot contains private merchant rows. It is never an API response or a
claim that a product is personally eligible. The existing merchant normalizer
and quality gate remain the only source of product facts.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Iterable, Mapping
from uuid import UUID, uuid5

from sqlalchemy import DateTime, Numeric

from ...models import CatalogRecord, Product, ProductSource, SourceRule
from .catalog_records import prepare_record
from .versions import NORMALIZATION_VERSION

SOURCE_CODES = ('baon', 'shoppinglive', 'serginnetti', 'postmeridiem')
PREVIOUS_ROWS = {'baon': 9318, 'shoppinglive': 43402, 'serginnetti': 17065, 'postmeridiem': 378}
BASELINE_POSITIVE = {'baon': 7067, 'shoppinglive': 36378, 'serginnetti': 12403, 'postmeridiem': 243}
MAX_OPERATION_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024**3
MAX_BASELINE_ROWS = 250000
MAX_SOURCE_RULES = 4096


class SnapshotRejected(ValueError):
    """A bounded non-secret reason; never includes a merchant row or URL."""


def _require(condition, code):
    if not condition:
        raise SnapshotRejected(code)


def _utc(value):
    _require(isinstance(value, datetime), 'datetime_required')
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def _json_value(value):
    if isinstance(value, datetime):
        return _utc(value).isoformat(timespec='microseconds')
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def encoded(value):
    return json.dumps(_json_value(value), sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def _frozen(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _fields(row, model):
    values = {}
    for column in model.__table__.columns:
        value = row.get(column.name) if isinstance(row, Mapping) else getattr(row, column.name, None)
        if value is not None and isinstance(column.type, DateTime):
            value = _utc(value)
        elif value is not None and isinstance(column.type, Numeric):
            value = Decimal(str(value)).quantize(Decimal(1).scaleb(-column.type.scale))
        values[column.name] = value
    return values


def row_fingerprint(row, model=None):
    model = model or type(row)
    return hashlib.sha256(encoded(_fields(row, model))).hexdigest()


def source_fingerprint(source):
    return row_fingerprint(source, ProductSource)


def rules_fingerprint(rules):
    # Include inactive rules: their later activation must invalidate a plan.
    values = sorted((_fields(rule, SourceRule) for rule in rules), key=lambda item: item['id'])
    return hashlib.sha256(encoded(values)).hexdigest()


@dataclass(frozen=True)
class SnapshotPolicy:
    previous_rows: Mapping[str, int] = field(default_factory=lambda: dict(PREVIOUS_ROWS))
    available_quality: Mapping[str, int] = field(default_factory=lambda: dict(BASELINE_POSITIVE))
    max_rows_per_source: int = 100000

    def __post_init__(self):
        _require(set(self.previous_rows) == set(SOURCE_CODES)
            and set(self.available_quality) == set(SOURCE_CODES)
            and all(type(value) is int and value >= 0
                for value in (*self.previous_rows.values(), *self.available_quality.values()))
            and type(self.max_rows_per_source) is int and 1 <= self.max_rows_per_source <= 100000,
            'bounded_snapshot_policy_required')
        object.__setattr__(self, 'previous_rows', _frozen(self.previous_rows))
        object.__setattr__(self, 'available_quality', _frozen(self.available_quality))


@dataclass(frozen=True)
class SourceInput:
    source: object
    rows: Iterable[dict]
    observed_at: datetime
    finished_at: datetime
    feed_sha256: str
    previous_received: int = 0
    previous_distinct: int = 0
    previous_available_quality: int = 0
    products: Iterable[Product] = ()
    records: Iterable[CatalogRecord] = ()
    rules: Iterable[SourceRule] = ()


@dataclass(frozen=True)
class SnapshotBatch:
    start_cursor: int
    end_cursor: int
    source_id: str | None
    operations: tuple[dict, ...]
    bytes: int


@dataclass(frozen=True)
class BaselineRow:
    """Only identity and complete evidence survive the streaming baseline read."""
    id: str
    source_id: str | None
    source: str | None
    external_id: str | None
    raw_key: str | None
    sha256: str


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _private_file(path):
    _private_parent(path)
    _require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_SNAPSHOT_BYTES,
        'bounded_regular_snapshot_required')
    _require(os.name == 'nt' or path.stat().st_mode & 0o077 == 0, 'private_snapshot_permissions_required')
    _require(os.name == 'nt' or path.stat().st_uid == os.geteuid(), 'owned_snapshot_required')


def _private_parent(path):
    parent = path.parent
    _require(parent.is_dir() and not parent.is_symlink() and parent.resolve() == parent,
        'canonical_snapshot_parent_required')
    _require(os.name == 'nt' or parent.stat().st_uid == os.geteuid()
        and parent.stat().st_mode & 0o077 == 0, 'owned_private_snapshot_parent_required')


@contextmanager
def _readonly(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        yield connection


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str
    run_id: str
    operation_count: int
    sources: tuple[Mapping, ...]
    _file_identity: tuple = field(repr=False)

    def check_identity(self):
        _private_file(self.path)
        _require(_identity(self.path) == self._file_identity, 'sealed_snapshot_changed')

    def read_batch(self, cursor, *, max_items=25, max_bytes=MAX_OPERATION_BYTES):
        _require(type(cursor) is int and 0 <= cursor <= self.operation_count,
            'bounded_snapshot_cursor_required')
        _require(type(max_items) is int and 1 <= max_items <= 25
            and type(max_bytes) is int and 1 <= max_bytes <= MAX_OPERATION_BYTES,
            'bounded_snapshot_batch_required')
        self.check_identity()
        operations, total, source_id, end = [], 0, None, cursor
        with _readonly(self.path) as connection:
            rows = connection.execute('SELECT position,source_id,value FROM operations '
                'WHERE position>? ORDER BY position LIMIT ?', (cursor, max_items))
            for position, current_source, raw in rows:
                if source_id is not None and current_source != source_id:
                    break
                size = len(raw)
                if total + size > max_bytes:
                    _require(bool(operations), 'single_operation_exceeds_batch_byte_limit')
                    break
                operations.append(json.loads(raw))
                source_id, end, total = current_source, position, total + size
        self.check_identity()
        return SnapshotBatch(cursor, end, source_id, tuple(operations), total)


def open_snapshot(path, expected_sha256):
    """Verify the complete file once; subsequent batches check its sealed identity."""
    path = Path(path).absolute()
    _require(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256),
        'snapshot_sha256_required')
    _private_file(path)
    before = _identity(path)
    with path.open('rb') as stream:
        _require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected_sha256,
            'snapshot_sha256_mismatch')
    with _readonly(path) as connection:
        metadata = json.loads(connection.execute("SELECT value FROM metadata WHERE key='plan'").fetchone()[0])
        count = connection.execute('SELECT count(*) FROM operations').fetchone()[0]
    _require(_identity(path) == before and metadata['status'] == 'sealed'
        and metadata['operation_count'] == count and metadata['schema_version'] == 1,
        'complete_unchanged_snapshot_required')
    return Snapshot(path, expected_sha256, metadata['run_id'], count,
        _frozen(metadata['sources']), before)


def _index(rows, key, *, source_id=None, code=None, allow_unassigned=False):
    indexed = {}
    for count, row in enumerate(rows, 1):
        _require(count <= MAX_BASELINE_ROWS, 'retained_catalogue_baseline_limit_exceeded')
        _require(source_id is None or row.source_id == source_id
            or allow_unassigned and row.source_id is None, 'cross_source_staged_record_or_product')
        _require(code is None or row.source == code, 'cross_source_staged_product')
        value = getattr(row, key)
        _require(value not in indexed and bool(row.id), 'ambiguous_existing_catalogue_identity')
        identity = {'id': row.id, 'source_id': row.source_id,
            'source': row.source if code is not None else None,
            'external_id': row.external_id, 'raw_key': row.raw_key if code is None else None}
        bounds = {'id': 36, 'source_id': 36, 'source': 64, 'external_id': 128, 'raw_key': 160}
        _require(all(item is None or isinstance(item, str) and len(item) <= bounds[name]
            for name, item in identity.items()), 'bounded_retained_catalogue_identity_required')
        # Compute full-row evidence while the streamed ORM object is alive, then
        # retain no merchant blobs, JSON documents, or ORM/session references.
        digest = row.sha256 if isinstance(row, BaselineRow) else row_fingerprint(
            row, Product if code is not None else CatalogRecord)
        _require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest),
            'retained_catalogue_fingerprint_required')
        indexed[value] = BaselineRow(**identity, sha256=digest)
    return indexed


def capture_product_baseline(rows, *, code, source_id):
    """Capture streamed full-row hashes, then close SQL before normalization."""
    return tuple(_index(rows, 'external_id', code=code, source_id=source_id,
        allow_unassigned=True).values())


def capture_record_baseline(rows, *, source_id):
    return tuple(_index(rows, 'raw_key', source_id=source_id).values())


def _prepare_source(connection, item, namespace, processed, policy, position):
    source = item.source
    code, source_id = source.code, source.id
    _require(isinstance(source_id, str) and 0 < len(source_id) <= 36, 'existing_source_id_required')
    observed, finished = _utc(item.observed_at), _utc(item.finished_at)
    _require(item.observed_at.tzinfo is not None and item.finished_at.tzinfo is not None
        and processed - timedelta(hours=72) <= observed <= finished <= processed + timedelta(minutes=5),
        'fresh_real_snapshot_observation_required')
    _require(source.last_sync_at is None or _utc(source.last_sync_at) < observed,
        'source_already_has_same_or_newer_observation')
    _require(isinstance(item.feed_sha256, str) and re.fullmatch('[0-9a-f]{64}', item.feed_sha256),
        'feed_sha256_required')
    _require(all(type(value) is int and value >= 0 for value in
        (item.previous_received, item.previous_distinct, item.previous_available_quality)),
        'previous_snapshot_counts_required')
    products = _index(item.products, 'external_id', code=code,
        source_id=source_id, allow_unassigned=True)
    records = _index(item.records, 'raw_key', source_id=source_id)
    rules = []
    for count, rule in enumerate(item.rules, 1):
        _require(count <= MAX_SOURCE_RULES, 'source_rules_limit_exceeded')
        rules.append(rule)
    _require(all(rule.source_id == source_id for rule in rules), 'cross_source_staged_rule')
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'deactivated': 0, 'reasons': {}}
    seen, available, total = set(), 0, 0

    def add(operation):
        nonlocal position
        raw = encoded({'source_id': source_id, 'source_code': code, **operation})
        _require(0 < len(raw) <= MAX_OPERATION_BYTES, 'staged_operation_too_large')
        position += 1
        connection.execute('INSERT INTO operations VALUES(?,?,?)', (position, source_id, raw))

    for row in item.rows:
        total += 1
        _require(total <= policy.max_rows_per_source, 'source_row_limit_exceeded')
        raw, digest, payload, canonical, decision = prepare_record(row, source, processed, observed_at=observed)
        _require(payload is not None and bool(payload.get('external_id')), 'snapshot_row_identity_required')
        external = payload['external_id']
        _require(external not in seen, 'duplicate_snapshot_external_id')
        seen.add(external)
        raw_key = 'id:' + external
        existing_product, existing_record = products.get(external), records.get(raw_key)
        product_id = existing_product.id if existing_product is not None else str(uuid5(namespace, code+':product:'+external))
        record_id = existing_record.id if existing_record is not None else str(uuid5(namespace, code+':record:'+raw_key))
        product_values = None
        action = 'skipped'
        if decision.status != 'quarantined':
            product_values = {**payload, 'updated_at': processed}
            if existing_product is None:
                product_values['created_at'] = processed
            action = 'updated' if existing_product is not None else 'created'
        elif existing_product is not None:
            product_values = {'is_active': 0, 'is_available': 0, 'is_deleted_from_feed': 0,
                'last_seen_in_feed_at': observed, 'updated_at': processed}
        else:
            product_id = None
        record_values = {'raw_key': raw_key, 'raw_json': raw, 'raw_hash': digest,
            'external_id': external, 'canonical_json': canonical, 'status': decision.status,
            'reasons': list(decision.reasons), 'normalization_version': NORMALIZATION_VERSION,
            'last_seen_at': observed, 'processed_at': processed, 'product_id': product_id}
        if existing_record is None:
            record_values.update(source_id=source_id, first_seen_at=processed)
        counts = {action: 1, 'reasons': {}}
        stats[action] += 1
        if action == 'skipped':
            for reason in decision.reasons:
                stats['reasons'][reason] = stats['reasons'].get(reason, 0) + 1
                counts['reasons'][reason] = counts['reasons'].get(reason, 0) + 1
        available += int(decision.status == 'passed' and payload['is_available'] == 1)
        add({'kind': 'upsert', 'product_id': product_id, 'record_id': record_id,
            'expected_product_hash': existing_product.sha256 if existing_product is not None else None,
            'expected_record_hash': existing_record.sha256 if existing_record is not None else None,
            'product_values': product_values, 'record_values': record_values, 'counts': counts})
    _require(total > 0 and 2*total >= max(policy.previous_rows[code], item.previous_received, item.previous_distinct),
        'unexpected_source_loss_requires_review')
    _require(5*available >= 4*max(policy.available_quality[code], item.previous_available_quality),
        'available_quality_loss_requires_review')
    for external, product in sorted(products.items()):
        if external in seen or product.source_id != source_id:
            continue
        stats['deactivated'] += 1
        add({'kind': 'missing', 'product_id': product.id, 'record_id': None,
            'expected_product_hash': product.sha256, 'expected_record_hash': None,
            'product_values': {'is_deleted_from_feed': 1, 'is_available': 0, 'is_active': 0, 'updated_at': processed},
            'record_values': None, 'counts': {'deactivated': 1, 'reasons': {}}})
    for record in sorted(records.values(), key=lambda record: record.id):
        if record.external_id is None or record.external_id in seen:
            continue
        add({'kind': 'missing', 'product_id': None, 'record_id': record.id,
            'expected_product_hash': None, 'expected_record_hash': record.sha256,
            'product_values': None, 'record_values': {'status': 'missing', 'reasons': ['missing_from_feed']},
            'counts': {'reasons': {}}})
    return position, {'id': source_id, 'code': code, 'source_sha256': source_fingerprint(source),
        'rules_sha256': rules_fingerprint(rules), 'observed_at': observed.isoformat(timespec='microseconds'),
        'download_finished_at': finished.isoformat(timespec='microseconds'), 'feed_sha256': item.feed_sha256,
        'total_received': total, 'distinct_received': len(seen), 'available_quality': available, 'stats': stats}


def stage_snapshot(path, *, run_id, sources, policy=None):
    """Normalize all four complete feeds into a private, exclusive local SQLite file."""
    policy = policy or SnapshotPolicy()
    try:
        namespace = UUID(run_id)
    except (ValueError, TypeError, AttributeError):
        raise SnapshotRejected('uuid4_snapshot_run_required') from None
    _require(namespace.version == 4, 'uuid4_snapshot_run_required')
    items = tuple(sources)
    _require(len(items) == 4 and {item.source.code for item in items} == set(SOURCE_CODES)
        and len({item.source.id for item in items}) == 4, 'exact_four_existing_sources_required')
    path = Path(path).absolute()
    _require(path.parent.is_dir() and not path.is_symlink(), 'new_snapshot_path_required')
    _private_parent(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    processed, position, metadata = datetime.now(timezone.utc), 0, []
    try:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA max_page_count=524288')
            connection.execute('CREATE TABLE operations(position INTEGER PRIMARY KEY,source_id TEXT NOT NULL,value BLOB NOT NULL)')
            connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB NOT NULL)')
            for item in sorted(items, key=lambda item: SOURCE_CODES.index(item.source.code)):
                position, source_metadata = _prepare_source(connection, item, namespace, processed, policy, position)
                metadata.append(source_metadata)
            value = {'schema_version': 1, 'status': 'sealed', 'run_id': str(namespace),
                'operation_count': position, 'sources': metadata}
            connection.execute('INSERT INTO metadata VALUES(?,?)', ('plan', encoded(value)))
    except Exception:
        # The failed private artifact is evidence only. It has no sealed plan
        # and can never be opened as a successful snapshot or silently retried.
        raise
    with path.open('r+b') as stream:
        os.fsync(stream.fileno())
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    os.chmod(path, 0o400)
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return open_snapshot(path, digest)

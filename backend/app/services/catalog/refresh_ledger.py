"""Durable progress for short catalogue transactions; separate from public stats.

The ORM and worker share the same table and Alembic metadata. The refresh17
release must apply0029 before enabling its worker; sealed build16 is unchanged.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from ...models import CatalogRefreshRun


refresh_runs = CatalogRefreshRun.__table__


class RefreshConflict(RuntimeError):
    """Inputs changed or another writer owns the catalogue. Never auto-replay."""


class CommitUncertain(RuntimeError):
    """Reconnect, acquire the worker lock and resolve the supplied checkpoint."""

    def __init__(self, checkpoint):
        super().__init__('catalogue_refresh_commit_requires_resolution')
        self.checkpoint = checkpoint


def ledger_row(db, run_id, *, lock=False):
    query = select(refresh_runs).where(refresh_runs.c.id == run_id)
    if lock:
        query = query.with_for_update(nowait=True)
    return db.execute(query).mappings().one_or_none()


def validate_ledger(row, snapshot):
    if (row is None or row['snapshot_sha256'] != snapshot.sha256
            or row['operation_count'] != snapshot.operation_count
            or not 0 <= row['cursor'] <= snapshot.operation_count
            or not 0 <= row['finalized_sources'] <= len(snapshot.sources)):
        raise RefreshConflict('catalogue_refresh_ledger_snapshot_mismatch')
    return row


def resolve_checkpoint(row, checkpoint):
    """An acknowledgement is recovered only for the same immutable batch.

    A cursor is advanced in the same transaction as its products and records.
    No inference is made from process exit status or a disappeared connection.
    """
    column = checkpoint.get('position_column', 'cursor')
    if column not in {'cursor', 'finalized_sources'}:
        raise RefreshConflict('invalid_refresh_checkpoint_column')
    if row[column] == checkpoint['before']:
        return 'not_committed'
    if (row[column] == checkpoint['after']
            and row['last_batch_sha256'] == checkpoint['batch_sha256']):
        return 'committed'
    raise RefreshConflict('catalogue_refresh_commit_state_diverged')


def utc_now():
    return datetime.now(timezone.utc)

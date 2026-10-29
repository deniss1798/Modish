"""Recovery sequencing and byte provenance without network or production data."""
from copy import deepcopy
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.integrations.admitad.feed_parser import parse_admitad_csv
from app.db import Base
from app.models import ProductSource, Product, CatalogRecord, CatalogSyncRun
from app.services.catalog.refresh_snapshot import SnapshotPolicy
from app.services.catalog import refresh_runner as runner
from app.services.catalog.refresh_ledger import CommitUncertain, RefreshConflict, resolve_checkpoint
from app.services.catalog.refresh_writer import prepare_checkpoint


class ProcessKilled(BaseException):
    pass


class FakePublisher:
    """Model atomic ledger+row advancement; assert intent precedes each write."""
    def __init__(self, state, snapshot, folder):
        self.state, self.snapshot, self.folder = state, snapshot, folder
        self.connection = object()

    def __enter__(self):
        self.state['sessions'] += 1
        return self

    def __exit__(self, *_):
        self.state['closed'] += 1

    def _intent(self, phase, batch=None, reason_code=None):
        intended = prepare_checkpoint(self.snapshot, self.state.get('position'), phase,
            batch=batch, reason_code=reason_code)
        saved = runner.read_private(self.folder / 'publication.json')
        if saved['pending'] != intended:
            raise AssertionError('durable intent must precede SQL write')
        return intended

    def initialize(self):
        self._intent('initialize')
        self.state['position'] = {'cursor': 0, 'finalized_sources': 0,
            'last_batch_sha256': '', 'status': 'running', 'counts_json': {}}

    def position(self):
        return deepcopy(self.state['position'])

    def next_batch(self):
        self.state['batch_calls'] += 1
        batch = self.snapshot.read_batch(self.state['position']['cursor'])
        checkpoint = self._intent('batch', batch)
        error = self.state.get('failure')
        self.state['failure'] = None
        if error == 'not_committed':
            raise CommitUncertain(checkpoint)
        if error == 'conflict':
            raise RefreshConflict('catalogue_refresh_row_changed')
        if error == 'lock':
            raise DBAPIError('redacted', None, SimpleNamespace(sqlstate='55P03'))
        self.state['position'].update(cursor=checkpoint['after'],
            last_batch_sha256=checkpoint['batch_sha256'])
        if error == 'committed':
            raise CommitUncertain(checkpoint)
        if error == 'kill':
            raise ProcessKilled()
        return checkpoint

    def finalize_source(self):
        checkpoint = self._intent('finalize')
        self.state['position'].update(finalized_sources=checkpoint['after'],
            last_batch_sha256=checkpoint['batch_sha256'], status='complete')
        return checkpoint

    def resolve(self, checkpoint):
        self.state['resolutions'] += 1
        if checkpoint.get('phase') == 'initialize':
            return 'not_committed' if self.state.get('position') is None else 'committed'
        if checkpoint.get('phase') == 'abandon':
            if self.state['position']['status'] == 'interrupted' and self.state['position']['last_batch_sha256'] == checkpoint['batch_sha256']:
                return 'committed'
            raise RefreshConflict('catalogue_refresh_abandon_state_diverged')
        return resolve_checkpoint(self.state['position'], checkpoint)

    def abandon(self, reason_code):
        checkpoint = self._intent('abandon', reason_code=reason_code)
        self.state['abandon_calls'] = self.state.get('abandon_calls', 0) + 1
        self.state['position'].update(status='interrupted', last_batch_sha256=checkpoint['batch_sha256'])
        if self.state.get('abandon_ack_lost'):
            self.state['abandon_ack_lost'] = False
            raise CommitUncertain(checkpoint)
        return checkpoint


class RefreshRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.state = {'sessions': 0, 'closed': 0, 'batch_calls': 0, 'resolutions': 0}
        batch = SimpleNamespace(start_cursor=0, end_cursor=1,
            operations=({'source_id': 'source', 'product_id': 'one'},))
        self.snapshot = SimpleNamespace(run_id=str(uuid4()), sha256='a'*64,
            operation_count=1, sources=({'id': 'source'},), read_batch=lambda cursor: batch)

    def publish(self, **options):
        factory = lambda engine, snapshot: FakePublisher(self.state, snapshot, self.folder)
        with patch.object(runner, '_position', lambda *args: self.state.get('position')):
            return runner.publish_snapshot(None, self.snapshot, self.folder,
                writer_factory=factory, sleep_fn=lambda seconds: None, **options)

    def abandon(self):
        factory = lambda engine, snapshot: FakePublisher(self.state, snapshot, self.folder)
        with patch.object(runner, '_position', lambda *args: self.state.get('position')):
            return runner.abandon_snapshot(None, self.snapshot, self.folder,
                reason_code='snapshot_expired', writer_factory=factory)

    def test_publication_records_intent_before_every_atomic_write(self):
        result = self.publish()
        self.assertEqual(result['status'], 'catalogue_refresh_complete')
        self.assertEqual(self.state['batch_calls'], 1)
        self.assertEqual(self.state['sessions'], self.state['closed'])
        progress = runner.read_private(self.folder/'publication.json')
        self.assertEqual(progress['status'], 'complete')
        self.assertIsNone(progress['pending'])
        with self.assertRaises(FileExistsError):
            self.publish()

    def test_lost_commit_ack_reconnects_and_resolves_without_replaying_batch(self):
        self.state['failure'] = 'committed'
        self.publish()
        self.assertEqual(self.state['sessions'], 2)
        self.assertEqual(self.state['resolutions'], 1)
        self.assertEqual(self.state['batch_calls'], 1)
        self.assertEqual(runner.read_private(self.folder/'publication.json')['last_resolution'], 'committed')

    def test_known_uncommitted_ack_reconnects_resolves_and_retries_exact_batch(self):
        self.state['failure'] = 'not_committed'
        self.publish()
        self.assertEqual(self.state['sessions'], 2)
        self.assertEqual(self.state['resolutions'], 1)
        self.assertEqual(self.state['batch_calls'], 2)
        self.assertEqual(runner.read_private(self.folder/'publication.json')['last_resolution'], 'not_committed')

    def test_process_death_after_commit_leaves_intent_resolved_by_explicit_resume(self):
        self.state['failure'] = 'kill'
        with self.assertRaises(ProcessKilled):
            self.publish()
        self.assertIsNotNone(runner.read_private(self.folder/'publication.json')['pending'])
        self.publish(resume=True)
        self.assertEqual(self.state['batch_calls'], 1)
        self.assertEqual(self.state['resolutions'], 1)

    def test_changed_row_is_visible_conflict_without_automatic_retry(self):
        self.state['failure'] = 'conflict'
        with self.assertRaisesRegex(RefreshConflict, 'row_changed'):
            self.publish()
        self.assertEqual(self.state['batch_calls'], 1)
        self.assertEqual(self.state['position']['cursor'], 0)
        self.assertIsNotNone(runner.read_private(self.folder/'publication.json')['pending'])

    def test_nowait_rollback_is_resolved_before_bounded_retry(self):
        self.state['failure'] = 'lock'
        self.publish()
        self.assertEqual(self.state['batch_calls'], 2)
        self.assertEqual(self.state['resolutions'], 1)

    def test_diverged_ack_never_replays_or_overwrites_ledger(self):
        self.state['failure'] = 'kill'
        with self.assertRaises(ProcessKilled):
            self.publish()
        self.state['position']['last_batch_sha256'] = 'b'*64
        with self.assertRaisesRegex(RefreshConflict, 'state_diverged'):
            self.publish(resume=True)
        self.assertEqual(self.state['batch_calls'], 1)

    def test_resume_does_not_reset_durable_publication_deadline(self):
        self.state['failure'] = 'kill'
        first = datetime.now(timezone.utc)
        with self.assertRaises(ProcessKilled):
            self.publish(now_fn=lambda: first)
        deadline = runner.read_private(self.folder/'publication.json')['deadline_at']
        with self.assertRaisesRegex(runner.RunnerRejected, 'publication_deadline'):
            self.publish(resume=True, now_fn=lambda: first+timedelta(seconds=runner.MAX_RUN_SECONDS+1))
        self.assertEqual(self.state['batch_calls'], 1)
        self.assertEqual(runner.read_private(self.folder/'publication.json')['deadline_at'], deadline)

    def test_abandon_resolves_pending_batch_without_replaying_it(self):
        self.state['failure'] = 'kill'
        with self.assertRaises(ProcessKilled):
            self.publish()
        result = self.abandon()
        self.assertEqual(result['status'], 'catalogue_refresh_interrupted')
        self.assertEqual((self.state['batch_calls'], self.state['abandon_calls']), (1,1))
        self.assertEqual(self.state['position']['cursor'], 1)
        self.assertEqual(self.state['position']['status'], 'interrupted')

    def test_unknown_abandon_commit_is_resolved_before_any_second_abandon(self):
        self.state['failure'] = 'kill'
        with self.assertRaises(ProcessKilled):
            self.publish()
        self.state['abandon_ack_lost'] = True
        with self.assertRaises(CommitUncertain):
            self.abandon()
        result = self.abandon()
        self.assertEqual(result['status'], 'catalogue_refresh_interrupted')
        self.assertEqual(self.state['abandon_calls'], 1)

    def test_diverged_pending_commit_cannot_be_abandoned(self):
        self.state['failure'] = 'kill'
        with self.assertRaises(ProcessKilled):
            self.publish()
        self.state['position']['last_batch_sha256'] = 'b'*64
        with self.assertRaisesRegex(RefreshConflict, 'state_diverged'):
            self.abandon()
        self.assertNotIn('abandon_calls', self.state)

    def test_proven_uninitialized_run_can_be_abandoned_without_sql_writes(self):
        runner.write_private(self.folder/'publication.json', {'run_id':self.snapshot.run_id,
            'snapshot_sha256':self.snapshot.sha256, 'initialized':False,
            'pending':{'phase':'initialize'}, 'status':'starting'})
        result = self.abandon()
        self.assertFalse(result['partial_batches_may_be_committed'])
        self.assertNotIn('position', self.state)
        self.assertNotIn('abandon_calls', self.state)
        sessions = self.state['sessions']
        with self.assertRaisesRegex(runner.RunnerRejected, 'abandoned_run_cannot_resume'):
            self.publish(resume=True)
        self.assertEqual(self.state['sessions'], sessions)

    def test_csv_param_expansion_matches_existing_parser_and_hashes_consumed_bytes(self):
        raw = ('id;name;available;price;param;currency\n'
            '1;"Женская\nрубашка";true;1000;"Размер: M | Цвет: синий";RUR\n').encode('utf-8-sig')
        path = self.folder/'feed.csv'
        path.write_bytes(raw)
        expected = hashlib.sha256(raw).hexdigest()
        actual = list(runner.iter_feed_rows(path, expected, len(raw)))
        self.assertEqual(actual, parse_admitad_csv(raw.decode('utf-8-sig')))
        with self.assertRaisesRegex(runner.RunnerRejected, 'hash_mismatch'):
            list(runner.iter_feed_rows(path, 'b'*64, len(raw)))
        with self.assertRaisesRegex(runner.RunnerRejected, 'byte_bound'):
            list(runner.iter_feed_rows(path, expected, len(raw)-1))

    def test_status_report_hash_and_symlink_cannot_be_bypassed(self):
        path = self.folder/'private.json'
        runner.write_private(path, {'status': 'prepared'})
        digest = runner.report_sha(path)
        self.assertEqual(runner.read_private(path, expected=digest)['status'], 'prepared')
        path.write_text('{"status":"changed"}')
        with self.assertRaisesRegex(runner.RunnerRejected, 'hash_mismatch'):
            runner.read_private(path, expected=digest)

    def test_preparation_releases_read_transactions_before_http_and_normalization(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE alembic_version(version_num VARCHAR(32))'))
            connection.execute(text("INSERT INTO alembic_version VALUES ('20261028_0029')"))
        with Session(engine) as db:
            db.add_all(ProductSource(id='source-'+code, code=code, name=code, network='direct')
                for code in runner.SOURCE_CODES)
            db.commit()
        active = {'transactions': 0}
        @event.listens_for(engine, 'begin')
        def begun(connection):
            active['transactions'] += 1
        @event.listens_for(engine, 'commit')
        @event.listens_for(engine, 'rollback')
        def ended(connection):
            active['transactions'] -= 1
        now = datetime.now(timezone.utc)
        def download(sources, directory, *, run_id):
            self.assertEqual(active['transactions'], 0)
            feeds = []
            for source in sources:
                raw = ('id;name;available;price;param;currency;category;gender;brand;picture;url\n'
                    'sku;Женская рубашка;true;1000;Размер: M;RUR;Рубашки;женский;Authored;'
                    'https://authored.example/a.jpg;https://authored.example/p/sku\n').encode()
                path = directory/(source.code+'.csv')
                path.write_bytes(raw)
                feeds.append(SimpleNamespace(code=source.code, path=path, bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(), observed_at=now, finished_at=now))
            return SimpleNamespace(run_id=run_id, feeds=feeds, sha256='d'*64)
        original_stage = runner.stage_snapshot
        def stage(*args, **kwargs):
            self.assertEqual(active['transactions'], 0)
            return original_stage(*args, **kwargs)
        policy = SnapshotPolicy(previous_rows={code:0 for code in runner.SOURCE_CODES},
            available_quality={code:0 for code in runner.SOURCE_CODES})
        rid = uuid4().hex
        with patch.object(runner, '_read_connection', lambda eng: eng.connect()), \
                patch.object(runner, 'stage_snapshot', stage):
            prepared = runner.prepare_refresh(engine, self.folder, run_id=rid, mode='test',
                downloader=download, policy=policy)
        self.assertEqual(prepared['operation_count'], 4)
        self.assertFalse(prepared['database_writes'])
        with engine.connect() as connection:
            for table in (Product, CatalogRecord, CatalogSyncRun):
                self.assertEqual(len(connection.execute(select(table)).all()), 0)
        _, _, snapshot = runner.load_prepared(Path(prepared['directory']),
            prepared['prepared_sha256'], mode='test', verify_download=False)
        self.assertEqual(snapshot.operation_count, 4)
        with self.assertRaises(FileExistsError):
            runner.prepare_refresh(engine, self.folder, run_id=rid, mode='test', policy=policy)

    def test_production_policy_override_is_rejected_before_creating_attempt(self):
        rid = uuid4().hex
        with self.assertRaisesRegex(runner.RunnerRejected, 'policy_override'):
            runner.prepare_refresh(None, self.folder, run_id=rid, mode='production', policy=object())
        self.assertFalse((self.folder/('run-'+rid)).exists())


if __name__ == '__main__':
    unittest.main()

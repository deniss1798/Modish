"""Opt-in real PostgreSQL races for the unpublished short-batch refresher.

Only a generated schema in the verified disposable workspace cluster is used.
Snapshots use the actual normalizer and authored merchant rows; no live feed,
production connection or application credentials are consulted.
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import CatalogRecord, CatalogSyncRun, Product, ProductSource, SourceRule
from app.services.catalog.catalog_records import store_record
from app.services.catalog.refresh_ledger import CommitUncertain, RefreshConflict, ledger_row, refresh_runs
from app.services.catalog.refresh_snapshot import SOURCE_CODES, SnapshotPolicy, SourceInput, stage_snapshot
from app.services.catalog.refresh_writer import BatchWriter, _sync_id
from app.services.recommendation_publication import ProductEvidence, lock_catalogue, product_evidence
from isolated_postgres import create_isolated_postgres
from test_refresh_snapshot import offer


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class RefreshWriterPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='refresh17', pool_size=5)
        self.engine = isolated.engine
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'refresh.sqlite'
        self.now = datetime.now(timezone.utc)
        self.observed = self.now - timedelta(minutes=5)
        with Session(self.engine, expire_on_commit=False) as db:
            sources = [ProductSource(id=code+'-source', code=code, name='Authored '+code,
                network='direct') for code in SOURCE_CODES]
            db.add_all(sources)
            db.flush()
            record, _ = store_record(db, sources[0], offer('sku'), self.now-timedelta(hours=1),
                last_seen_at=self.now-timedelta(hours=1))
            db.add(SourceRule(id='authored-rule', source_id=sources[0].id,
                rule_type='blocked_brand', rule_value='NeverThisBrand', is_active=1))
            db.commit()
            product = db.get(Product, record.product_id)
            self.product_id, self.record_id = product.id, record.id
            self.evidence = ProductEvidence(product.id, product.source_id, product_evidence(product))
            inputs = []
            for source in sources:
                rows = ([offer('sku', name='Женская рубашка обновлённая'), offer('new-sku')]
                    if source.code == 'baon' else [offer('other-'+source.code)])
                inputs.append(SourceInput(source=source, rows=rows,
                    observed_at=self.observed, finished_at=self.now, feed_sha256='a'*64,
                    products=list(db.scalars(select(Product).where(Product.source_id == source.id))),
                    records=list(db.scalars(select(CatalogRecord).where(CatalogRecord.source_id == source.id))),
                    rules=list(db.scalars(select(SourceRule).where(SourceRule.source_id == source.id)))))
            policy = SnapshotPolicy(previous_rows={code:0 for code in SOURCE_CODES},
                available_quality={code:0 for code in SOURCE_CODES})
            self.snapshot = stage_snapshot(self.path, run_id=str(uuid4()), sources=inputs, policy=policy)
        self.addCleanup(lambda: os.chmod(self.path, 0o600) if self.path.exists() else None)
        self.baon_batch = self.snapshot.read_batch(0)
        self.new_product_id = self.baon_batch.operations[1]['product_id']
        self.new_record_id = self.baon_batch.operations[1]['record_id']

    def state(self):
        with Session(self.engine) as db:
            ledger = ledger_row(db, self.snapshot.run_id)
            product = db.get(Product, self.product_id)
            run = db.get(CatalogSyncRun, _sync_id(self.snapshot, 'baon-source'))
            return {'title':product.title,
                'new_product':db.get(Product, self.new_product_id) is not None,
                'new_record':db.get(CatalogRecord, self.new_record_id) is not None,
                'record_raw':db.get(CatalogRecord, self.record_id).raw_json,
                'cursor':None if ledger is None else ledger['cursor'],
                'finalized':None if ledger is None else ledger['finalized_sources'],
                'status':None if ledger is None else ledger['status'],
                'counts':None if ledger is None else ledger['counts_json'],
                'sync':None if run is None else (run.created_count,run.updated_count,run.ingested_count,run.status),
                'source_clock':db.get(ProductSource,'baon-source').last_sync_at,
                'product_count':db.scalar(select(func.count()).select_from(Product)),
                'record_count':db.scalar(select(func.count()).select_from(CatalogRecord))}

    def assert_unpublished(self):
        value = self.state()
        self.assertEqual((value['title'],value['new_product'],value['new_record'],value['cursor']),
            ('Женская рубашка',False,False,0))
        self.assertEqual(value['sync'], (0,0,0,'running'))
        self.assertEqual((value['product_count'],value['record_count']), (1,1))
        self.assertIsNone(value['source_clock'])

    def assert_bounded_conflict(self, action):
        started = monotonic()
        with self.assertRaises(DBAPIError) as raised:
            action()
        self.assertEqual(getattr(raised.exception.orig,'sqlstate',None),'55P03')
        self.assertLess(monotonic()-started,.75)

    @staticmethod
    def backend_pid(writer):
        # Reading through a short Session avoids leaving an external
        # Connection transaction open around a later writer Session.commit.
        with writer._session() as db:
            return db.scalar(text('SELECT pg_backend_pid()'))

    def test_rows_counts_cursor_foreign_keys_and_advisory_owner_survive_short_commits(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            pid = self.backend_pid(writer)
            writer.initialize()
            with self.assertRaisesRegex(RefreshConflict,'worker_already_running'):
                with BatchWriter(self.engine,self.snapshot):
                    self.fail('A second physical session acquired the worker lock')
            checkpoint = writer.next_batch()
            self.assertEqual(writer.resolve(checkpoint),'committed')
            self.assertEqual(self.backend_pid(writer),pid)
            value = self.state()
            self.assertEqual((value['title'],value['new_product'],value['new_record']),
                ('Женская рубашка обновлённая',True,True))
            self.assertEqual((value['cursor'],value['sync']), (2,(1,1,2,'running')))
            self.assertIsNone(value['source_clock'])
            while writer.next_batch() is not None:
                self.assertEqual(self.backend_pid(writer),pid)
            while writer.finalize_source() is not None:
                self.assertEqual(self.backend_pid(writer),pid)
            final = self.state()
            self.assertEqual((final['status'],final['finalized'],final['product_count'],final['record_count']),
                ('complete',4,5,5))
            self.assertEqual(final['source_clock'],self.observed)
        # The same pooled server connection must no longer retain the lock.
        with BatchWriter(self.engine,self.snapshot) as successor:
            self.assertEqual(successor.position()['status'],'complete')

    def test_actual_publication_reader_makes_product_writer_fail_fast_then_retry(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as reader:
                locked = lock_catalogue(reader,[self.evidence])
                self.assertIn(self.product_id,locked.products)
                self.assert_bounded_conflict(writer.next_batch)
                self.assert_unpublished()
                reader.rollback()
            writer.next_batch()
            self.assertEqual(self.state()['cursor'],2)

    def test_source_share_reader_allows_product_batch_but_blocks_only_tiny_finalization(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as reader:
                reader.scalar(select(ProductSource).where(ProductSource.id=='baon-source')
                    .with_for_update(read=True))
                started = monotonic()
                writer.next_batch()
                self.assertLess(monotonic()-started,.75)
                while writer.next_batch() is not None:
                    pass
                self.assert_bounded_conflict(writer.finalize_source)
                self.assertEqual(self.state()['finalized'],0)
                self.assertIsNone(self.state()['source_clock'])
                reader.rollback()
            checkpoint = writer.finalize_source()
            self.assertEqual(writer.resolve(checkpoint),'committed')
            self.assertEqual(self.state()['source_clock'],self.observed)

    def test_legacy_source_exclusive_writer_conflicts_without_rows_or_cursor_advancing(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as legacy:
                legacy.scalar(select(ProductSource).where(ProductSource.id=='baon-source').with_for_update())
                self.assert_bounded_conflict(writer.next_batch)
                self.assert_unpublished()
                legacy.rollback()
            writer.next_batch()
            self.assertEqual(self.state()['cursor'],2)

    def test_rule_exclusive_writer_conflicts_before_product_changes(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as legacy:
                legacy.scalar(select(SourceRule).where(SourceRule.id=='authored-rule').with_for_update())
                self.assert_bounded_conflict(writer.next_batch)
                self.assert_unpublished()

    def test_changed_source_configuration_is_rejected(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as editor:
                editor.get(ProductSource,'baon-source').feed_url='https://example.test/new-source.csv'
                editor.commit()
            with self.assertRaisesRegex(RefreshConflict,'source_changed'):
                writer.next_batch()
            self.assert_unpublished()

    def test_changed_rule_evidence_is_rejected(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as editor:
                editor.get(SourceRule,'authored-rule').is_active=0
                editor.commit()
            with self.assertRaisesRegex(RefreshConflict,'source_rules_changed'):
                writer.next_batch()
            self.assert_unpublished()

    def test_changed_product_evidence_is_not_overwritten(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as editor:
                editor.get(Product,self.product_id).title='Manual catalogue correction'
                editor.commit()
            with self.assertRaisesRegex(RefreshConflict,'row_changed'):
                writer.next_batch()
            state = self.state()
            self.assertEqual((state['title'],state['cursor'],state['new_product']),
                ('Manual catalogue correction',0,False))

    def test_changed_record_evidence_is_not_overwritten(self):
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with Session(self.engine) as editor:
                editor.get(CatalogRecord,self.record_id).status='missing'
                editor.commit()
            with self.assertRaisesRegex(RefreshConflict,'row_changed'):
                writer.next_batch()
            self.assert_unpublished()
            with Session(self.engine) as observer:
                self.assertEqual(observer.get(CatalogRecord,self.record_id).status,'missing')

    def test_late_database_constraint_rolls_back_existing_new_rows_cursor_and_counts(self):
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE catalog_records ADD CONSTRAINT authored_new_row_rejection "
                "CHECK (external_id <> 'new-sku') NOT VALID"))
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            before = self.state()
            with self.assertRaises(IntegrityError):
                writer.next_batch()
            self.assert_unpublished()
            self.assertEqual(self.state(),before)

    def check_lost_batch_acknowledgement(self, *, committed):
        original = Session.commit
        calls = []
        def acknowledgement_lost(db):
            calls.append(True)
            if committed:
                original(db)
            raise DBAPIError(None,None,RuntimeError('Authored acknowledgement failure'))
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            with patch.object(Session,'commit',acknowledgement_lost):
                with self.assertRaises(CommitUncertain) as raised:
                    writer.next_batch()
            checkpoint = raised.exception.checkpoint
            self.assertTrue(writer.connection.invalidated)
        self.assertEqual(len(calls),1)
        with BatchWriter(self.engine,self.snapshot) as successor:
            self.assertEqual(successor.resolve(checkpoint),'committed' if committed else 'not_committed')
            if not committed:
                self.assert_unpublished()
                successor.next_batch()
            state = self.state()
            self.assertEqual((state['cursor'],state['product_count'],state['record_count'],state['sync']),
                (2,2,2,(1,1,2,'running')))

    def test_lost_acknowledgement_after_real_commit_resolves_exactly_once(self):
        self.check_lost_batch_acknowledgement(committed=True)

    def test_lost_acknowledgement_before_commit_resolves_rollback_then_explicit_retry(self):
        self.check_lost_batch_acknowledgement(committed=False)

    def test_real_owned_backend_disconnect_before_commit_resolves_database_rollback(self):
        original = Session.commit
        killed = []
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            owned_pid = self.backend_pid(writer)
            def disconnect_before_commit(db):
                # This session owns the checked PID throughout the batch. The
                # second connection is in the same verified disposable schema;
                # no unrelated backend or database can be selected here.
                self.assertEqual(db.scalar(text('SELECT pg_backend_pid()')),owned_pid)
                with self.engine.connect() as admin:
                    self.assertNotEqual(admin.scalar(text('SELECT pg_backend_pid()')),owned_pid)
                    self.assertEqual(admin.scalar(text('SELECT datname FROM pg_stat_activity WHERE pid=:pid'),
                        {'pid':owned_pid}),'postgres')
                    self.assertTrue(admin.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':owned_pid}))
                killed.append(owned_pid)
                original(db)
            with patch.object(Session,'commit',disconnect_before_commit):
                with self.assertRaises(CommitUncertain) as raised:
                    writer.next_batch()
            checkpoint = raised.exception.checkpoint
            self.assertTrue(writer.connection.invalidated)
        self.assertEqual(killed,[owned_pid])
        with BatchWriter(self.engine,self.snapshot) as successor:
            self.assertNotEqual(self.backend_pid(successor),owned_pid)
            self.assertEqual(successor.resolve(checkpoint),'not_committed')
            self.assert_unpublished()
            successor.next_batch()
            self.assertEqual(self.state()['cursor'],2)

    def test_lost_finalization_acknowledgement_recovers_source_clock_and_position_together(self):
        original = Session.commit
        def acknowledgement_lost(db):
            original(db)
            raise DBAPIError(None,None,RuntimeError('Authored final acknowledgement failure'))
        with BatchWriter(self.engine,self.snapshot) as writer:
            writer.initialize()
            while writer.next_batch() is not None:
                pass
            with patch.object(Session,'commit',acknowledgement_lost):
                with self.assertRaises(CommitUncertain) as raised:
                    writer.finalize_source()
            checkpoint = raised.exception.checkpoint
        with BatchWriter(self.engine,self.snapshot) as successor:
            self.assertEqual(successor.resolve(checkpoint),'committed')
            self.assertEqual(self.state()['finalized'],1)
            self.assertEqual(self.state()['source_clock'],self.observed)
            # This advances the next source; it cannot finalize BAON twice.
            successor.finalize_source()
            self.assertEqual(self.state()['finalized'],2)


if __name__=='__main__':
    unittest.main()

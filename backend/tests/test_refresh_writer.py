"""Authored SQLite fixtures verify atomic data contracts, not PostgreSQL locks."""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import create_engine, delete, event, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import CatalogRecord, CatalogSyncRun, Product, ProductSource, SourceRule
from app.services.catalog.refresh_ledger import (CommitUncertain, RefreshConflict, ledger_row,
    refresh_runs, resolve_checkpoint, validate_ledger)
from app.services.catalog.refresh_snapshot import row_fingerprint, source_fingerprint, rules_fingerprint
from app.services.catalog.refresh_writer import (BatchWriter, _decode_values, _digest,
    _prepare_operations, _sync_id, apply_batch)


@dataclass(frozen=True)
class Batch:
    start_cursor: int
    end_cursor: int
    source_id: str
    operations: tuple


class RefreshWriterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.db.close)
        now = datetime.now(timezone.utc)
        self.source = ProductSource(id='source', code='baon', name='Authored source',
            network='admitad', status='ok', last_sync_at=now)
        self.product = Product(id='product', source_id='source', source='baon', external_id='sku',
            title='Рубашка', brand='Authored', category='рубашки', price=1000,
            currency='RUB', image_url='https://example.test/a.jpg',
            product_url='https://example.test/products/sku', last_seen_in_feed_at=now,
            available_sizes=['M'], size_system='LETTER', availability_status='in_stock')
        self.record = CatalogRecord(id='record', source_id='source', external_id='sku',
            raw_key='id:sku', product_id='product', raw_json={'id': 'sku'}, raw_hash='a'*64,
            canonical_json={}, normalization_version='authored', status='passed', reasons=[])
        self.db.add(self.source)
        self.db.flush()
        self.db.add(self.product)
        self.db.flush()
        self.db.add(self.record)
        self.db.commit()
        self.entry = {'id':'source', 'code':'baon', 'observed_at':now.isoformat(),
            'total_received':1, 'source_sha256':source_fingerprint(self.source),
            'rules_sha256':rules_fingerprint([])}
        self.snapshot = SimpleNamespace(run_id=str(uuid4()),sha256='b'*64,
            operation_count=1,sources=[self.entry])
        self.operation = {'source_id':'source','source_code':'baon','product_id':'product',
            'record_id':'record','expected_product_hash':row_fingerprint(self.product),
            'expected_record_hash':row_fingerprint(self.record),
            'product_values':{'title':'Рубашка обновлённая'},
            'record_values':{'raw_json':{'id':'sku','name':'Рубашка обновлённая'}},
            'counts':{'updated':1}}
        self.db.execute(insert(refresh_runs).values(id=self.snapshot.run_id,
            snapshot_sha256=self.snapshot.sha256,operation_count=1,cursor=0,finalized_sources=0,
            last_batch_sha256='',status='running',counts_json={'source':{
                'created':0,'updated':0,'deactivated':0,'skipped':0,'reasons':{}}},
            source_states_json={'source':{key:self.entry[key] for key in ('source_sha256','rules_sha256')}},
            started_at=now,updated_at=now))
        self.db.add(CatalogSyncRun(id=_sync_id(self.snapshot,'source'),source_id='source',
            status='running',total_received=1))
        self.db.commit()

    def batch(self, operation=None):
        return Batch(0,1,'source',(deepcopy(operation or self.operation),))

    def apply(self, operation=None):
        batch=self.batch(operation)
        decoded=_prepare_operations(batch)
        with self.db.begin():
            return apply_batch(self.db,self.snapshot,batch,decoded,started=monotonic())

    def test_batch_commits_rows_cursor_and_public_counts_together(self):
        checkpoint=self.apply()
        self.db.expire_all()
        self.assertEqual(self.db.get(Product,'product').title,'Рубашка обновлённая')
        row=validate_ledger(ledger_row(self.db,self.snapshot.run_id),self.snapshot)
        self.assertEqual(row['cursor'],1)
        self.assertEqual(row['counts_json']['source']['updated'],1)
        run=self.db.get(CatalogSyncRun,_sync_id(self.snapshot,'source'))
        self.assertEqual((run.updated_count,run.ingested_count,run.status),(1,1,'running'))
        self.assertEqual(resolve_checkpoint(row,checkpoint),'committed')
        self.assertEqual(self.db.get(ProductSource,'source').last_sync_at,
            self.source.last_sync_at)

    def test_product_edit_after_snapshot_rolls_back_without_advancing(self):
        self.product.title='Manual edit'
        self.db.commit()
        with self.assertRaisesRegex(RefreshConflict,'row_changed'):
            self.apply()
        row=ledger_row(self.db,self.snapshot.run_id)
        self.assertEqual(row['cursor'],0)
        self.assertEqual(self.db.get(Product,'product').title,'Manual edit')
        self.assertEqual(self.db.get(CatalogRecord,'record').raw_json,{'id':'sku'})

    def test_record_edit_after_snapshot_rolls_back_product_too(self):
        self.record.status='missing'
        self.db.commit()
        with self.assertRaisesRegex(RefreshConflict,'row_changed'):
            self.apply()
        self.assertEqual(self.db.get(Product,'product').title,'Рубашка')
        self.assertEqual(ledger_row(self.db,self.snapshot.run_id)['cursor'],0)

    def test_source_configuration_or_rules_changes_stop_publication(self):
        self.source.feed_url='https://example.test/changed.csv'
        self.db.commit()
        with self.assertRaisesRegex(RefreshConflict,'source_changed'):
            self.apply()
        self.source.feed_url=None
        self.db.commit()
        # updated_at changed too: rebaseline only the authored fixture here.
        self.entry['source_sha256']=source_fingerprint(self.source)
        row=ledger_row(self.db,self.snapshot.run_id)
        states=deepcopy(row['source_states_json'])
        states['source']['source_sha256']=self.entry['source_sha256']
        from sqlalchemy import update
        self.db.execute(update(refresh_runs).values(source_states_json=states))
        self.db.add(SourceRule(id='rule',source_id='source',rule_type='blocked_brand',rule_value='Authored'))
        self.db.commit()
        with self.assertRaisesRegex(RefreshConflict,'source_rules_changed'):
            self.apply()
        self.assertEqual(ledger_row(self.db,self.snapshot.run_id)['cursor'],0)

    def test_existing_ids_sources_and_external_identity_cannot_be_rewritten(self):
        for patch_values in ({'id':'other'},{'created_at':datetime.now(timezone.utc)},
                {'external_id':'other-sku'},{'source':'shoppinglive'},{'source_id':'other'}):
            with self.subTest(patch_values=patch_values):
                operation=deepcopy(self.operation)
                operation['product_values'].update(patch_values)
                with self.assertRaises(RefreshConflict):
                    self.apply(operation)
                self.assertEqual(ledger_row(self.db,self.snapshot.run_id)['cursor'],0)
                self.db.rollback()

    def test_missing_rows_do_not_renew_stock_observation(self):
        observed=self.product.last_seen_in_feed_at
        operation=deepcopy(self.operation)
        operation['product_values']={'is_deleted_from_feed':1,'is_active':0,'is_available':0}
        operation['record_values']={'status':'missing','reasons':['missing_from_feed']}
        operation['counts']={'deactivated':1}
        self.apply(operation)
        self.db.expire_all()
        product=self.db.get(Product,'product')
        self.assertEqual(product.last_seen_in_feed_at.replace(tzinfo=timezone.utc),observed)
        self.assertEqual((product.is_active,product.is_available,product.is_deleted_from_feed),(0,0,1))

    def test_new_product_is_inserted_before_its_catalog_record_with_foreign_keys(self):
        operation=deepcopy(self.operation)
        operation.update(product_id='new-product',record_id='new-record',
            expected_product_hash=None,expected_record_hash=None,
            product_values={'source_id':'source','source':'baon','external_id':'new-sku',
                'title':'Новая рубашка','brand':'Authored','category':'рубашки','price':'1200.00',
                'currency':'RUB','image_url':'https://example.test/new.jpg',
                'product_url':'https://example.test/products/new-sku'},
            record_values={'source_id':'source','external_id':'new-sku','raw_key':'id:new-sku',
                'product_id':'new-product','raw_json':{'id':'new-sku'},'raw_hash':'c'*64,
                'canonical_json':{},'normalization_version':'authored','status':'passed','reasons':[]},
            counts={'created':1})
        self.apply(operation)
        self.assertIsNotNone(self.db.get(Product,'new-product'))
        self.assertEqual(self.db.get(CatalogRecord,'new-record').product_id,'new-product')
        self.assertEqual(self.db.get(Product,'product').id,'product')
        self.assertEqual(ledger_row(self.db,self.snapshot.run_id)['counts_json']['source']['created'],1)

    def test_batch_bounds_and_unknown_columns_rejected_before_sql_mutation(self):
        operation=deepcopy(self.operation)
        operation['product_values']['unknown_column']='bad'
        with self.assertRaisesRegex(RefreshConflict,'unknown_staged_column'):
            _prepare_operations(self.batch(operation))
        with self.assertRaisesRegex(RefreshConflict,'batch_bound'):
            _prepare_operations(Batch(0,26,'source',tuple(self.operation for _ in range(26))))
        operation=deepcopy(self.operation)
        operation['record_values']['raw_json']={'data':'x'*(1024*1024)}
        with self.assertRaisesRegex(RefreshConflict,'batch_bound'):
            _prepare_operations(self.batch(operation))

    def test_ambiguous_commit_recovery_requires_exact_position_and_digest(self):
        checkpoint={'before':0,'after':1,'batch_sha256':'expected'}
        self.assertEqual(resolve_checkpoint({'cursor':0,'last_batch_sha256':''},checkpoint),'not_committed')
        self.assertEqual(resolve_checkpoint({'cursor':1,'last_batch_sha256':'expected'},checkpoint),'committed')
        for row in ({'cursor':1,'last_batch_sha256':'other'},{'cursor':2,'last_batch_sha256':'expected'}):
            with self.assertRaisesRegex(RefreshConflict,'commit_state_diverged'):
                resolve_checkpoint(row,checkpoint)
        finalized={'position_column':'finalized_sources','before':1,'after':2,'batch_sha256':'final'}
        self.assertEqual(resolve_checkpoint({'finalized_sources':2,'last_batch_sha256':'final'},finalized),'committed')

    def test_production_owner_refuses_sqlite(self):
        with self.assertRaisesRegex(ValueError,'requires_postgresql'):
            BatchWriter(self.engine,self.snapshot)

    @contextmanager
    def fixture_writer(self):
        # Exercise transaction logic on an authored fixture only. The public
        # constructor continues to reject SQLite, as the test above proves.
        self.db.rollback()
        connection=self.engine.connect()
        writer=object.__new__(BatchWriter)
        writer.engine,writer.connection,writer.snapshot=self.engine,connection,self.snapshot
        try:
            yield writer
        finally:
            connection.close()

    def test_final_source_metadata_is_a_separate_completed_transaction(self):
        self.apply()
        self.entry['stats']={'created':0,'updated':1,'deactivated':0,'skipped':0,'reasons':{}}
        with self.fixture_writer() as writer:
            checkpoint=writer.finalize_source()
            self.assertEqual(writer.resolve(checkpoint),'committed')
            self.assertIsNone(writer.finalize_source())
        self.db.expire_all()
        row=ledger_row(self.db,self.snapshot.run_id)
        self.assertEqual((row['cursor'],row['finalized_sources'],row['status']),(1,1,'complete'))
        self.assertEqual(self.db.get(CatalogSyncRun,_sync_id(self.snapshot,'source')).status,'ok')

    def test_final_metadata_rejects_diverged_counts_without_claiming_success(self):
        self.apply()
        self.entry['stats']={'created':0,'updated':2,'deactivated':0,'skipped':0,'reasons':{}}
        with self.fixture_writer() as writer:
            with self.assertRaisesRegex(RefreshConflict,'totals_diverged'):
                writer.finalize_source()
            self.assertEqual(writer.position()['finalized_sources'],0)

    def test_abandon_reports_partial_and_never_rewrites_products_or_stock_clock(self):
        self.apply()
        self.db.expire_all()
        product_before=row_fingerprint(self.db.get(Product,'product'))
        with self.fixture_writer() as writer:
            checkpoint=writer.abandon('source_changed')
            self.assertEqual(writer.resolve(checkpoint),'committed')
            with self.assertRaisesRegex(RefreshConflict,'run_interrupted'):
                writer.next_batch()
        self.db.expire_all()
        self.assertEqual(row_fingerprint(self.db.get(Product,'product')),product_before)
        run=self.db.get(CatalogSyncRun,_sync_id(self.snapshot,'source'))
        self.assertEqual((run.status,run.error_message,run.updated_count),('partial','source_changed',1))

    def test_initialization_resolution_checks_ledger_presence_and_no_progress(self):
        with self.fixture_writer() as writer:
            self.assertEqual(writer.resolve({'phase':'initialize'}),'committed')
        self.apply()
        with self.fixture_writer() as writer:
            with self.assertRaisesRegex(RefreshConflict,'initialization_state_diverged'):
                writer.resolve({'phase':'initialize'})

    def test_uncertain_advisory_acquisition_discards_physical_connection(self):
        connection=Mock()
        connection.execution_options.return_value=connection
        connection.scalar.return_value=True
        connection.commit.side_effect=KeyboardInterrupt()
        engine=Mock()
        engine.dialect.name='postgresql'
        engine.connect.return_value=connection
        writer=BatchWriter(engine,self.snapshot)
        with self.assertRaises(KeyboardInterrupt):
            writer.__enter__()
        connection.invalidate.assert_called_once()
        connection.close.assert_called_once()
        self.assertIsNone(writer.connection)

    def test_initialize_creates_private_ledger_and_public_sync_stats_atomically(self):
        self.db.execute(delete(refresh_runs))
        self.db.execute(delete(CatalogSyncRun))
        self.db.commit()
        with self.fixture_writer() as writer:
            self.assertEqual(writer.resolve({'phase':'initialize'}),'not_committed')
            row=writer.initialize()
            self.assertEqual((row['cursor'],row['status']),(0,'running'))
            self.assertEqual(writer.resolve({'phase':'initialize'}),'committed')
            self.assertEqual(writer.initialize()['id'],self.snapshot.run_id)
        self.db.expire_all()
        run=self.db.get(CatalogSyncRun,_sync_id(self.snapshot,'source'))
        self.assertEqual((run.status,run.total_received,run.created_count),('running',1,0))

    def test_commit_acknowledgement_failure_returns_exact_checkpoint_and_discards_connection(self):
        db=Mock()
        db.commit.side_effect=DBAPIError(None,None,Exception('authored lost acknowledgement'))
        connection=Mock()
        writer=object.__new__(BatchWriter)
        writer.connection=connection
        checkpoint={'before':0,'after':1,'batch_sha256':'d'*64}
        with self.assertRaises(CommitUncertain) as raised:
            writer._commit(db,checkpoint)
        self.assertEqual(raised.exception.checkpoint,checkpoint)
        connection.invalidate.assert_called_once()


if __name__=='__main__':
    unittest.main()

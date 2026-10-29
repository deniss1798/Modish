"""A complete immutable local plan precedes any production publication lock."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gc
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import weakref

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import CatalogRecord, Product, ProductSource, SourceRule
from app.services.catalog.catalog_records import store_record
from app.services.catalog import refresh_snapshot as snapshots
from app.services.catalog.refresh_snapshot import (BASELINE_POSITIVE, PREVIOUS_ROWS,
    SOURCE_CODES, MAX_OPERATION_BYTES, SnapshotPolicy, SnapshotRejected, SourceInput,
    open_snapshot, row_fingerprint, rules_fingerprint, source_fingerprint, stage_snapshot)
from app.services.catalog.refresh_snapshot import capture_product_baseline, capture_record_baseline


def offer(pid='p1', **changes):
    return {'id': pid, 'name': 'Женская рубашка', 'brand': 'Example',
        'category': 'Женское/Рубашки', 'gender': 'женский', 'price': '3999.50',
        'oldprice': '5999.00', 'currency': 'RUR', 'sizes': 'M,L', 'available': True,
        'picture': f'https://shop.example/photo/{pid}.jpg',
        'url': f'https://shop.example/product/{pid}', **changes}


class RefreshSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'snapshot.sqlite'
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.sources = [ProductSource(id=code+'-source', code=code, name=code,
            network='direct') for code in SOURCE_CODES]
        self.db.add_all(self.sources)
        self.db.commit()
        self.now = datetime.now(timezone.utc)
        self.observed = self.now-timedelta(minutes=5)
        self.policy = SnapshotPolicy(previous_rows={code: 0 for code in SOURCE_CODES},
            available_quality={code: 0 for code in SOURCE_CODES})

    def tearDown(self):
        if self.path.exists():
            os.chmod(self.path, 0o600)

    def inputs(self, rows=None, **extra):
        return [SourceInput(source=source, rows=[offer()] if rows is None else rows,
            observed_at=self.observed, finished_at=self.now, feed_sha256='a'*64,
            **(extra if source.code == 'baon' else {})) for source in self.sources]

    def stage(self, inputs=None, **changes):
        return stage_snapshot(self.path, run_id=str(uuid4()),
            sources=self.inputs() if inputs is None else inputs, policy=self.policy, **changes)

    def all_operations(self, snapshot):
        cursor, values = 0, []
        while cursor < snapshot.operation_count:
            batch = snapshot.read_batch(cursor)
            self.assertLessEqual(len(batch.operations), 25)
            self.assertLessEqual(batch.bytes, MAX_OPERATION_BYTES)
            self.assertTrue(all(row['source_id'] == batch.source_id for row in batch.operations))
            self.assertGreater(batch.end_cursor, cursor)
            values.extend(batch.operations)
            cursor = batch.end_cursor
        return values

    def test_default_policies_retain_verified_snapshot_floors(self):
        policy = SnapshotPolicy()
        self.assertEqual(dict(policy.previous_rows), PREVIOUS_ROWS)
        self.assertEqual(dict(policy.available_quality), BASELINE_POSITIVE)
        with self.assertRaisesRegex(SnapshotRejected, 'unexpected_source_loss'):
            stage_snapshot(self.path, run_id=str(uuid4()), sources=self.inputs())

    def test_precaptured_baselines_retain_full_hash_after_orm_facts_change(self):
        source=self.sources[0]
        record,_=store_record(self.db,source,offer(),self.now,last_seen_at=self.observed)
        self.db.commit()
        product=self.db.get(Product,record.product_id)
        product_hash,record_hash=row_fingerprint(product),row_fingerprint(record)
        products=capture_product_baseline(iter([product]),code=source.code,source_id=source.id)
        records=capture_record_baseline(iter([record]),source_id=source.id)
        product.title='Edited after the read-only baseline'
        record.raw_json={'id':'changed-after-baseline'}
        snapshot=self.stage(self.inputs(products=products,records=records))
        operation=next(item for item in self.all_operations(snapshot) if item['source_code']=='baon')
        self.assertEqual(operation['expected_product_hash'],product_hash)
        self.assertEqual(operation['expected_record_hash'],record_hash)
        self.assertEqual(operation['product_id'],product.id)

    def test_roundtrip_uses_actual_normalizer_and_real_observation_not_processing_time(self):
        snapshot = self.stage()
        self.assertEqual(snapshot.operation_count, 4)
        self.assertEqual({entry['code'] for entry in snapshot.sources}, set(SOURCE_CODES))
        operations = self.all_operations(snapshot)
        for operation in operations:
            values, record = operation['product_values'], operation['record_values']
            self.assertEqual(values['price'], '3999.50')
            self.assertEqual(values['available_sizes'], ['M', 'L'])
            self.assertEqual(values['last_seen_in_feed_at'], self.observed.isoformat(timespec='microseconds'))
            self.assertNotEqual(values['last_seen_in_feed_at'], values['updated_at'])
            self.assertEqual(record['canonical_json']['price'], 3999.5)
            self.assertEqual(record['last_seen_at'], values['last_seen_in_feed_at'])
            self.assertEqual(operation['counts'], {'created': 1, 'reasons': {}})
        reopened = open_snapshot(snapshot.path, snapshot.sha256)
        self.assertEqual(self.all_operations(reopened), operations)

    def test_existing_ids_hashes_and_missing_clocks_are_preserved(self):
        source = self.sources[0]
        existing_record, _ = store_record(self.db, source, offer(), self.observed,
            last_seen_at=self.observed-timedelta(hours=1))
        missing_record, _ = store_record(self.db, source, offer('gone'), self.observed,
            last_seen_at=self.observed-timedelta(hours=2))
        self.db.commit()
        existing_product = self.db.get(Product, existing_record.product_id)
        missing_product = self.db.get(Product, missing_record.product_id)
        product_hash, record_hash = row_fingerprint(existing_product), row_fingerprint(existing_record)
        snapshot = self.stage(self.inputs(products=[existing_product, missing_product],
            records=[existing_record, missing_record]))
        operations = [row for row in self.all_operations(snapshot) if row['source_code'] == 'baon']
        updated = operations[0]
        self.assertEqual(updated['product_id'], existing_product.id)
        self.assertEqual(updated['record_id'], existing_record.id)
        self.assertEqual(updated['expected_product_hash'], product_hash)
        self.assertEqual(updated['expected_record_hash'], record_hash)
        self.assertNotIn('created_at', updated['product_values'])
        self.assertNotIn('first_seen_at', updated['record_values'])
        missing = [row for row in operations if row['kind'] == 'missing']
        self.assertEqual(len(missing), 2)
        for row in missing:
            self.assertNotIn('last_seen_in_feed_at', row['product_values'] or {})
            self.assertNotIn('last_seen_at', row['record_values'] or {})
        self.assertEqual(row_fingerprint(existing_product), product_hash)
        self.assertEqual(row_fingerprint(existing_record), record_hash)

    def test_duplicate_id_or_missing_identity_never_returns_a_sealed_snapshot(self):
        for rows, reason in (([offer(), offer()], 'duplicate_snapshot_external_id'),
                ([offer(id=None)], 'snapshot_row_identity_required')):
            with self.subTest(reason=reason):
                if self.path.exists():
                    os.chmod(self.path, 0o600)
                    self.path.unlink()
                with self.assertRaisesRegex(SnapshotRejected, reason):
                    self.stage(self.inputs(rows=rows))

    def test_retention_compares_live_prior_snapshot_and_quality_separately(self):
        inputs = self.inputs(previous_received=5)
        with self.assertRaisesRegex(SnapshotRejected, 'unexpected_source_loss'):
            self.stage(inputs)
        os.chmod(self.path, 0o600)
        self.path.unlink()
        with self.assertRaisesRegex(SnapshotRejected, 'available_quality_loss'):
            self.stage(self.inputs(previous_available_quality=2))

    def test_quality_quarantine_still_persists_honest_record_without_new_product(self):
        rows = [offer(price='invalid')]
        snapshot = self.stage(self.inputs(rows=rows))
        operations = self.all_operations(snapshot)
        self.assertTrue(all(row['product_id'] is None and row['product_values'] is None for row in operations))
        self.assertTrue(all(row['record_values']['status'] == 'quarantined' for row in operations))
        self.assertTrue(all(row['counts']['skipped'] == 1 for row in operations))

    def test_existing_quarantined_product_retains_old_facts_and_link(self):
        source = self.sources[0]
        record, _ = store_record(self.db, source, offer(), self.observed)
        self.db.commit()
        product = self.db.get(Product, record.product_id)
        snapshot = self.stage(self.inputs(rows=[offer(price='invalid')],
            products=[product], records=[record]))
        row = snapshot.read_batch(0).operations[0]
        self.assertEqual(row['product_id'], product.id)
        self.assertEqual(row['record_id'], record.id)
        self.assertEqual(row['record_values']['product_id'], product.id)
        self.assertNotIn('price', row['product_values'])
        self.assertEqual(row['product_values']['is_available'], 0)
        self.assertEqual(product.price, Decimal('3999.50'))

    def test_oversized_operation_and_exact_source_set_are_rejected(self):
        with self.assertRaisesRegex(SnapshotRejected, 'staged_operation_too_large'):
            self.stage(self.inputs(rows=[offer(description='x'*MAX_OPERATION_BYTES)]))
        os.chmod(self.path, 0o600)
        self.path.unlink()
        with self.assertRaisesRegex(SnapshotRejected, 'exact_four_existing_sources_required'):
            self.stage(self.inputs()[:3])

    def test_stage_never_overwrites_existing_artifact(self):
        self.path.write_bytes(b'existing-evidence')
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual(self.path.read_bytes(), b'existing-evidence')

    def test_metadata_and_policy_cannot_be_mutated_without_changing_the_artifact(self):
        snapshot = self.stage()
        with self.assertRaises(TypeError):
            snapshot.sources[0]['observed_at'] = self.now.isoformat()
        with self.assertRaises(TypeError):
            snapshot.sources[0]['stats']['created'] = 5000
        with self.assertRaises(TypeError):
            self.policy.previous_rows['baon'] = 0

    def test_wrong_nonnull_product_source_id_is_rejected_but_unassigned_legacy_is_allowed(self):
        source = self.sources[0]
        record, _ = store_record(self.db, source, offer(), self.observed)
        self.db.commit()
        product = self.db.get(Product, record.product_id)
        product.source_id = self.sources[1].id
        with self.assertRaisesRegex(SnapshotRejected, 'cross_source_staged_record_or_product'):
            self.stage(self.inputs(products=[product]))
        os.chmod(self.path, 0o600)
        self.path.unlink()
        product.source_id = None
        snapshot = self.stage(self.inputs(products=[product]))
        self.assertEqual(snapshot.read_batch(0).operations[0]['product_id'], product.id)

    def test_retained_baseline_overflow_rejects_without_truncating_or_consuming_rest(self):
        consumed = []
        def products():
            for index in range(100):
                consumed.append(index)
                yield Product(id=str(index), external_id=str(index), source='baon',
                    source_id=self.sources[0].id)
        with patch('app.services.catalog.refresh_snapshot.MAX_BASELINE_ROWS', 2):
            with self.assertRaisesRegex(SnapshotRejected, 'retained_catalogue_baseline_limit_exceeded'):
                self.stage(self.inputs(products=products()))
        self.assertEqual(consumed, [0, 1, 2])

    def test_streamed_baseline_does_not_retain_orm_objects_or_large_merchant_blobs(self):
        references = []
        def products():
            for index in range(12):
                row = Product(id=str(index), external_id=str(index), source='baon',
                    source_id=self.sources[0].id, feed_raw_json={'body': 'x'*100000})
                references.append(weakref.ref(row))
                yield row
        indexed = snapshots._index(products(), 'external_id', code='baon',
            source_id=self.sources[0].id, allow_unassigned=True)
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(len(indexed), 12)
        self.assertTrue(all(not hasattr(row, 'feed_raw_json') for row in indexed.values()))
        self.assertTrue(all(len(row.sha256) == 64 for row in indexed.values()))

    def test_streamed_baseline_hash_still_covers_full_merchant_and_canonical_facts(self):
        product = Product(id='p', external_id='p', source='baon', source_id=self.sources[0].id,
            feed_raw_json={'fact': 'before'})
        first = snapshots._index([product], 'external_id', code='baon', source_id=self.sources[0].id)['p']
        self.assertEqual(first.sha256, row_fingerprint(product))
        product.feed_raw_json = {'fact': 'after'}
        second = snapshots._index([product], 'external_id', code='baon', source_id=self.sources[0].id)['p']
        self.assertNotEqual(first.sha256, second.sha256)
        with self.assertRaisesRegex(SnapshotRejected, 'bounded_retained_catalogue_identity_required'):
            product.external_id = 'x'*129
            snapshots._index([product], 'external_id', code='baon', source_id=self.sources[0].id)

    def test_source_rules_overflow_rejects_before_normalizing_feed(self):
        rules = [SourceRule(id=str(index), source_id=self.sources[0].id,
            rule_type='min_price', rule_value='100') for index in range(3)]
        with patch('app.services.catalog.refresh_snapshot.MAX_SOURCE_RULES', 2):
            with self.assertRaisesRegex(SnapshotRejected, 'source_rules_limit_exceeded'):
                self.stage(self.inputs(rules=rules))

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership/mode contract')
    def test_public_parent_directory_is_rejected(self):
        os.chmod(self.path.parent, 0o755)
        try:
            with self.assertRaisesRegex(SnapshotRejected, 'owned_private_snapshot_parent_required'):
                self.stage()
        finally:
            os.chmod(self.path.parent, 0o700)

    def test_changed_file_hash_and_live_identity_are_rejected(self):
        snapshot = self.stage()
        os.chmod(self.path, 0o600)
        with self.path.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(SnapshotRejected, 'sealed_snapshot_changed'):
            snapshot.read_batch(0)
        with self.assertRaisesRegex(SnapshotRejected, 'snapshot_sha256_mismatch'):
            open_snapshot(self.path, snapshot.sha256)

    def test_batches_are_small_and_source_homogeneous(self):
        rows = [offer(str(index)) for index in range(31)]
        snapshot = self.stage(self.inputs(rows=rows))
        first = snapshot.read_batch(0)
        second = snapshot.read_batch(first.end_cursor)
        self.assertEqual(len(first.operations), 25)
        self.assertEqual(len(second.operations), 6)
        self.assertEqual(first.source_id, second.source_id)
        third = snapshot.read_batch(second.end_cursor)
        self.assertNotEqual(second.source_id, third.source_id)
        self.assertEqual(len(self.all_operations(snapshot)), 124)
        with self.assertRaisesRegex(SnapshotRejected, 'single_operation_exceeds_batch_byte_limit'):
            snapshot.read_batch(0, max_bytes=1)
        self.assertEqual(snapshot.read_batch(snapshot.operation_count).operations, ())

    def test_invalid_source_time_and_same_observation_fail_before_publication(self):
        cases = [replace(self.inputs()[0], observed_at=self.now+timedelta(minutes=10),
                    finished_at=self.now+timedelta(minutes=11)),
                 replace(self.inputs()[0], observed_at=self.now-timedelta(hours=73)),
                 replace(self.inputs()[0], observed_at=self.observed.replace(tzinfo=None))]
        for item in cases:
            with self.subTest(observed=item.observed_at):
                if self.path.exists():
                    os.chmod(self.path, 0o600)
                    self.path.unlink()
                inputs = self.inputs()
                inputs[0] = item
                with self.assertRaisesRegex(SnapshotRejected, 'fresh_real_snapshot_observation'):
                    self.stage(inputs)

    def test_fingerprints_include_inactive_rules_and_normalize_datetimes_and_decimal(self):
        product = Product(id='p', external_id='p', source='baon', title='Рубашка',
            price=Decimal('3000.00'), created_at=self.now)
        before = row_fingerprint(product)
        product.price = 3000
        product.created_at = self.now.replace(tzinfo=None)
        self.assertEqual(row_fingerprint(product), before)
        source = self.sources[0]
        before = source_fingerprint(source)
        source.status = 'changed'
        self.assertNotEqual(source_fingerprint(source), before)
        rule = SourceRule(id='r', source_id=source.id, rule_type='min_price', rule_value='100', is_active=0)
        before = rules_fingerprint([rule])
        rule.is_active = 1
        self.assertNotEqual(rules_fingerprint([rule]), before)


if __name__ == '__main__':
    unittest.main()

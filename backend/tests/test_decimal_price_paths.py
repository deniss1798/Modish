"""RUB kopecks survive admission, outfits and JSON/event boundaries."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.api import admin_products
from app.api.outfits import visible_outfits
from app.api.serializers import outfit_to_api
from app.models import MetricEvent, Outfit, Product, RecommendationEventV2
from app.schemas.api_product import product_to_api
from app.schemas.product_event import EventRequest
from app.schemas.shopping_intent import IntentSpec
from app.services import activation_service as activation
from app.services import outfit_engine_v2 as v2
from app.services import outfit_service, product_complements
from app.services.behavior_profile import category_price_adjustment, load_behavior_profile
from app.services.catalog.catalog_quality import product_is_feed_eligible
from app.services.catalog.quality_gate import evaluate_product
from app.services.catalog.rule_filters import product_passes_source_rules
from app.services.catalog_discovery import DiscoveryScope, _allowed
from app.services.catalog_filters import CatalogFilters
from app.services.feed_filters import product_passes_budget
from app.services.intent_eligibility import intent_commerce_problem
from app.services.intent_outfits import outfit_problem, serialize_outfit
from app.services.outfit_quality import scenario_product_ok
from app.services.product_eligibility import evaluate_eligibility
from app.services.product_event_service import record_product_event

import test_activation as fixtures


class DecimalPricePathTests(unittest.TestCase):
    setUp = fixtures.ActivationTests.setUp
    tearDown = fixtures.ActivationTests.tearDown
    product = fixtures.ActivationTests.product

    def products(self, prices):
        return {
            slot: self.product(category, price=Decimal(price))
            for slot, category, price in zip(
                ('top', 'bottom', 'shoes'), ('рубашки', 'брюки', 'обувь'), prices
            )
        }

    def outfit(self, products):
        row = Outfit(id=str(uuid4()), user_id=self.user.id,
            items_json={slot: product.id for slot, product in products.items()},
            total_price=sum((product.price for product in products.values()), Decimal('0')),
            style_direction='daily', reason='Confirmed fixture outfit', score=0.8)
        self.db.add(row)
        self.db.flush()
        return row

    def assert_json_numbers(self, value):
        # Plain json.dumps must work: Decimal cannot leak into JSON DB columns
        # or rely on a framework serializer converting RUB to strings.
        return json.loads(json.dumps(value, allow_nan=False))

    def test_every_budget_gate_rejects_a_fraction_above_the_whole_ruble_cap(self):
        scope = DiscoveryScope('womenswear', 'profile', 10000, 'profile')
        spec = IntentSpec(budget_max=10000)
        allowed_ids = set()
        for amount, allowed in (('10000.00', True), ('10000.99', False)):
            product = self.product(price=Decimal(amount))
            if allowed:
                allowed_ids.add(product.id)
            gates = {
                'personal': evaluate_eligibility(product, self.fit).eligible,
                'feed': product_passes_budget(product, self.fit),
                'activation': activation.product_ok(product, self.fit, self.session),
                'discovery': _allowed(product, scope, {}, datetime.now(timezone.utc)),
                'legacy': outfit_service._scenario_product_ok(product, 'daily', 10000),
                'v2': v2._hard_ok(product, fit=self.fit, rules_by_source={}),
                'intent': intent_commerce_problem(product, self.fit, spec) is None,
                'explicit_filter': CatalogFilters(max_price=10000).matches(product),
            }
            for gate, result in gates.items():
                with self.subTest(gate=gate, amount=amount):
                    self.assertEqual(result, allowed)
        sql_ids = set(self.db.scalars(select(Product.id).where(*CatalogFilters(max_price=10000).sql_conditions())))
        self.assertEqual(sql_ids, allowed_ids)

    def test_source_rule_decimal_threshold_is_exact_and_invalid_rule_still_ignored(self):
        product = self.product(price=Decimal('299.98'))
        product.source_id = 'decimal-source-fixture'
        rule = SimpleNamespace(is_active=1, rule_type='min_price', rule_value='299.99')
        rules = {product.source_id: [rule]}
        self.assertFalse(product_passes_source_rules(product, rules))
        product.price = Decimal('299.99')
        self.assertTrue(product_passes_source_rules(product, rules))
        product.price = Decimal('299.98')
        rule.rule_value = 'not-a-price'
        self.assertTrue(product_passes_source_rules(product, rules))

    def test_quality_price_limits_compare_kopecks_without_integer_truncation(self):
        tiny = self.product(price=Decimal('0.99'))
        self.assertTrue(product_is_feed_eligible(tiny))
        self.assertNotIn('invalid_price', evaluate_product(tiny).reasons)
        high = self.product(price=Decimal('10000000.99'))
        self.assertIn('invalid_price', evaluate_product(high).reasons)
        bounded = self.product(price=Decimal('500000.99'))
        self.assertFalse(scenario_product_ok(bounded, 'daily'))
        self.assertFalse(outfit_service._scenario_product_ok(bounded, 'daily', None))

    def test_product_json_preserves_price_old_price_discount_and_whole_compatibility(self):
        product = self.product(price=Decimal('1229.50'), old_price=Decimal('1599.99'))
        payload = self.assert_json_numbers(product_to_api(product))
        self.assertEqual(payload['price'], 1229.50)
        self.assertEqual(payload['old_price'], 1599.99)
        self.assertEqual(payload['discount_percent'], 23)
        self.assertIsInstance(payload['price'], float)
        product.price, product.old_price = Decimal('1000.00'), Decimal('2000.00')
        payload = self.assert_json_numbers(product_to_api(product))
        self.assertIs(type(payload['price']), int)
        self.assertIs(type(payload['old_price']), int)
        self.assertEqual(payload['discount_percent'], 50)

    def test_intent_total_preserves_kopecks_and_keeps_the_strict_full_outfit_budget(self):
        spec = IntentSpec(intent_type='outfit', budget_max=10000)
        products = self.products(('3333.33', '3333.33', '3333.33'))
        self.assertIsNone(outfit_problem(products, self.fit, spec))
        payload = self.assert_json_numbers(serialize_outfit(products, self.fit, spec,
            intent_id='decimal-intent-fixture', version=1))
        self.assertEqual(payload['total_price'], 9999.99)
        products['top'].price = Decimal('3334.33')
        self.assertEqual(outfit_problem(products, self.fit, spec), 'outfit_budget')

    def test_visible_outfits_use_exact_full_sum_with_or_without_activation(self):
        products = self.products(('3333.67', '3333.66', '3333.66'))
        row = self.outfit(products)
        self.assertEqual(visible_outfits(self.db, self.user, [row]), [])
        profile, _, _ = activation._preferences(self.db, self.user)
        self.assertNotIn('до 10000 ₽', activation._wow_explanation(list(products.values()), profile, self.fit))
        self.db.delete(self.session)
        self.db.flush()
        self.assertEqual(visible_outfits(self.db, self.user, [row]), [])
        products['top'].price -= Decimal('0.99')
        payload = self.assert_json_numbers(visible_outfits(self.db, self.user, [row]))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['total_price'], 10000)
        self.assertEqual(self.assert_json_numbers(outfit_to_api(row))['total_price'], 10000.99)

    def test_complete_wow_keeps_decimal_total_and_json_safe_snapshot(self):
        products = self.products(('3333.33', '3333.33', '3333.33'))
        row = self.outfit(products)
        self.session.swipes_json = {str(index): 'like' for index in range(15)}
        with patch.object(v2, 'generate_outfits_v2', side_effect=[[row], [], []]):
            result = activation.complete_wow(self.db, self.user, self.session)
        payload = self.assert_json_numbers(result)
        self.assertTrue(payload['completed'])
        self.assertEqual(payload['outfits'][0]['total_price'], 9999.99)
        self.assertIn('до 10000 ₽', payload['outfits'][0]['explanation'])
        self.assert_json_numbers(self.session.wow_json)
        self.db.flush()

    def test_v2_combinations_preserve_exact_sum_and_strict_total_budget(self):
        products = self.products(('1229.50', '2458.98', '3688.97'))
        buckets = {slot: [v2.SlotCandidate(slot, product, .7, .7, .7, .7)]
            for slot, product in products.items()}
        candidates = v2._build_combinations(buckets, scenario='daily', fit=self.fit, limit=1)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].total_price, Decimal('7377.45'))
        self.assertTrue(v2._outfit_budget_ok(Decimal('10000.00'), 3, self.fit))
        self.assertFalse(v2._outfit_budget_ok(Decimal('10000.01'), 3, self.fit))
        self.assertFalse(v2._outfit_budget_ok(Decimal('30000'), 3, self.fit))
        self.assertFalse(v2._outfit_budget_ok(Decimal('100'), 3, None))

    def test_v2_generator_keeps_strict_total_cap_for_activation_and_legacy(self):
        products = self.products(('3333.67', '3333.66', '3333.66'))
        for activated in (True, False):
            if not activated:
                self.db.delete(self.session)
                self.db.flush()
            rows = v2.generate_outfits_v2(self.db, self.user, count=1, scenario='daily')
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0].items_json), {'top', 'bottom'})
            self.assertEqual(rows[0].total_price, Decimal('6667.33'))
            self.assertEqual(outfit_to_api(rows[0])['completeness'], 'clothing_base')
            for row in rows:
                self.db.delete(row)
            for event in self.db.scalars(select(MetricEvent).where(MetricEvent.user_id == self.user.id)):
                self.db.delete(event)
            self.db.flush()
        products['top'].price -= Decimal('0.99')
        rows = v2.generate_outfits_v2(self.db, self.user, count=1, scenario='daily')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].total_price, Decimal('10000'))

    def test_legacy_total_keeps_kopecks_and_cannot_exceed_confirmed_total_budget(self):
        products = self.products(('4000.33', '4000.33', '4000.33'))
        self.db.delete(self.session)
        self.db.flush()
        self.assertEqual(outfit_service.generate_outfits(self.db, self.user, count=1, use_v2=False), [])
        self.fit.budget_max = 12001
        rows = outfit_service.generate_outfits(self.db, self.user, count=1, use_v2=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0].items_json.values()), {p.id for p in products.values()})
        self.assertEqual(rows[0].total_price, Decimal('12000.99'))
        self.assertEqual(self.assert_json_numbers(outfit_to_api(rows[0]))['total_price'], 12000.99)

    def test_complements_keep_activation_pair_cap_and_nonactivation_per_item_policy(self):
        anchor = self.product(price=Decimal('5000.50'))
        other = self.product('брюки', price=Decimal('5000.49'))
        with patch.object(activation, 'candidate_pool', return_value=[other]):
            self.assertEqual(product_complements.complementary_products(self.db, self.user, anchor), [])
        other.price = Decimal('4999.50')
        with patch.object(activation, 'candidate_pool', return_value=[other]):
            self.assertEqual(product_complements.complementary_products(self.db, self.user, anchor), [other])
        other.price = Decimal('5000.49')
        self.db.delete(self.session)
        self.db.flush()
        with patch.object(product_complements, '_catalog_slot_candidates', return_value=[other]):
            self.assertEqual(product_complements.complementary_products(self.db, self.user, anchor), [other])

    def test_event_snapshots_and_behavior_quantiles_are_json_numbers_with_kopecks(self):
        products = [self.product(price=Decimal(amount)) for amount in ('1229.50', '1999.99', '2999.05')]
        for product in products:
            record_product_event(self.db, self.user,
                EventRequest(event_type='save', product_id=product.id), commit=False)
        self.db.flush()
        events = list(self.db.scalars(select(RecommendationEventV2)))
        self.assertEqual(len(events), 3)
        self.assertEqual(sorted(self.assert_json_numbers(event.meta_json)['observed_price'] for event in events),
            [1229.50, 1999.99, 2999.05])
        for product in products:
            product.price = Decimal('9000.00')
        self.db.flush()
        profile = self.assert_json_numbers(load_behavior_profile(self.db, user_id=self.user.id))
        bounds = profile['category_prices']['рубашки']
        self.assertEqual((bounds['preferred_min'], bounds['preferred_max']), (1229.50, 2999.05))

    def test_behavior_json_float_bounds_include_equal_decimal_prices_exactly(self):
        snapshot = {'category_prices': {'рубашки': {
            'preferred_min': 1229.99, 'preferred_max': 2999.05, 'confidence': 1.0,
        }}}
        product = self.product(price=Decimal('1229.99'))
        self.assertGreater(category_price_adjustment(product, snapshot), 0)
        product.price = Decimal('2999.05')
        self.assertGreater(category_price_adjustment(product, snapshot), 0)
        product.price = Decimal('2999.06')
        self.assertLess(category_price_adjustment(product, snapshot), 0)

    def admin_item(self, price, **changes):
        fields = dict(external_id=str(uuid4()), source='admin-fixture', title='Брюки',
            brand='Brand', category='брюки', price=price, image_url='https://example.test/image.jpg',
            product_url='https://example.test/item', stock_observed_at=datetime.now(timezone.utc))
        fields.update(changes)
        return admin_products.ProductIn(**fields)

    def admin_import(self, items):
        with patch.object(admin_products, '_admin_auth'):
            return admin_products.admin_products_import(items, self.db, None)

    def test_admin_create_update_and_optional_old_price_preserve_decimal_rubles(self):
        item = self.admin_item(1229.50, old_price='1 599,99 ₽')
        self.assertEqual(self.admin_import([item]), {'created': 1, 'updated': 0})
        product = self.db.scalar(select(Product).where(Product.external_id == item.external_id))
        self.assertEqual((product.price, product.old_price), (Decimal('1229.50'), Decimal('1599.99')))
        self.admin_import([self.admin_item('1 000,99', external_id=item.external_id)])
        self.db.refresh(product)
        self.assertEqual((product.price, product.old_price), (Decimal('1000.99'), Decimal('1599.99')))
        self.admin_import([self.admin_item(1000, external_id=item.external_id, old_price=None)])
        self.db.refresh(product)
        self.assertEqual(product.price, Decimal('1000.00'))
        self.assertIsNone(product.old_price)

    def test_admin_invalid_price_batch_is_rejected_before_any_product_mutation(self):
        existing = self.product()
        original = existing.title
        changed = self.admin_item(1234.56, external_id=existing.external_id,
            source=existing.source, title='Must not be written')
        for field in ('price', 'old_price'):
            for invalid in (True, -1, 'not-a-price', {}, float('inf'), '10000000.01'):
                with self.subTest(field=field, kind=type(invalid).__name__):
                    bad = self.admin_item(1000, **({field: invalid} if field == 'old_price' else {}))
                    if field == 'price':
                        bad.price = invalid
                    with self.assertRaises(HTTPException) as raised:
                        self.admin_import([changed, bad])
                    self.assertEqual(raised.exception.status_code, 400)
                    self.assertEqual(raised.exception.detail['field'], field)
                    self.assertEqual(existing.title, original)
                    self.assertIsNone(self.db.scalar(select(Product).where(Product.external_id == bad.external_id)))

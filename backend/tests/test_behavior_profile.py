import unittest
from datetime import datetime, timezone
from app.services.behavior_profile import aggregate_behavior, behavior_adjustment
from app.models import Product


class BehaviorProfileTests(unittest.TestCase):
    def rows(self, count, likes=3, brand='brand'):
        now = datetime.now(timezone.utc)
        rows = [(str(i), brand, 'рубашки', 'view', {}, now) for i in range(count)]
        rows += [(str(i), brand, 'рубашки', 'like', {}, now) for i in range(likes)]
        return rows

    def test_three_of_four_differs_from_three_of_a_hundred(self):
        strong = aggregate_behavior(self.rows(4))['brands']['brand']
        weak = aggregate_behavior(self.rows(100))['brands']['brand']
        self.assertGreater(strong['affinity'], weak['affinity'])
        self.assertEqual(strong['exposure_count'], 4)
        self.assertEqual(weak['exposure_count'], 100)
        self.assertEqual(weak['negative_affinity'], 0)  # Silence isn't a dislike.

    def test_prefetch_is_not_exposure_and_repeated_view_is_not_new_evidence(self):
        rows = self.rows(4)
        now = datetime.now(timezone.utc)
        rows += [('0', 'brand', 'рубашки', 'view', {}, now)]*20
        rows += [('extra', 'brand', 'рубашки', 'impression', {}, now)]
        evidence = aggregate_behavior(rows)['brands']['brand']
        self.assertEqual(evidence['exposure_count'], 4)
        self.assertEqual(evidence['positive'], 3)

    def test_explicit_brand_rejection_does_not_spill_from_context_or_colour(self):
        now = datetime.now(timezone.utc)
        for reason in ('too_expensive', 'not_for_context', 'dont_like_color', 'dont_like_fit'):
            evidence = aggregate_behavior([('p', 'brand', 'рубашки', 'dislike', {'reason': reason}, now)])
            self.assertEqual(evidence['brands']['brand']['negative_affinity'], 0)
        evidence = aggregate_behavior([('p', 'brand', 'рубашки', 'dislike', {'reason': 'dont_like_brand'}, now)])
        self.assertGreater(evidence['brands']['brand']['negative_affinity'], 0)

    def test_prices_are_category_specific_and_need_historical_snapshots(self):
        now = datetime.now(timezone.utc)
        rows = []
        for category, amounts in [('рубашки', [3000, 4000, 5000]), ('обувь', [7000, 8000, 9000])]:
            rows += [(category+str(i), 'brand', category, 'save', {'observed_price': amount}, now)
                     for i, amount in enumerate(amounts)]
        profile = aggregate_behavior(rows)['category_prices']
        self.assertEqual((profile['рубашки']['preferred_min'], profile['рубашки']['preferred_max']), (3000, 5000))
        self.assertEqual((profile['обувь']['preferred_min'], profile['обувь']['preferred_max']), (7000, 9000))
        self.assertEqual(aggregate_behavior(self.rows(5))['category_prices'], {})

    def test_no_data_is_unknown_and_other_brands_remain_scoreable(self):
        self.assertEqual(aggregate_behavior([])['brands'], {})
        snapshot = aggregate_behavior(self.rows(4))
        other = Product(brand='different', category='рубашки', price=5000)
        self.assertEqual(behavior_adjustment(other, snapshot), 0)
        liked = Product(brand='brand', category='рубашки', price=5000)
        self.assertGreater(behavior_adjustment(liked, snapshot), 0)

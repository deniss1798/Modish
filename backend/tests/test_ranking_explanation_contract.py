"""Composition and public evidence retain the complete ranking contract."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import unittest

from app.models import Product
from app.schemas.api_product import product_to_api
from app.services.outfit_engine_v2 import SlotCandidate, _personal_score


class RankingExplanationContractTests(unittest.TestCase):
    def product(self):
        return Product(id='proof-product', external_id='proof-offer', source='baon',
            title='Рубашка', category='рубашки', price=3000, currency='RUB',
            available_sizes=['M'], feed_raw_json={'material': 'cotton', 'fit': 'regular'},
            raw_params_json={'silhouette': 'straight'}, is_active=1, is_available=1,
            is_deleted_from_feed=0, availability_status='in_stock',
            last_seen_in_feed_at=datetime.now(timezone.utc),
            product_url='https://example.test/product/proof-product')

    def test_composition_retains_full_score_and_does_not_double_apply_skip(self):
        product = SimpleNamespace(id='candidate')
        low = SlotCandidate('top', product, .2, .9, .9, .9, skip_penalty=.1)
        high = SlotCandidate('bottom', product, .8, .1, .1, .1, skip_penalty=.2)
        self.assertAlmostEqual(_personal_score([low, high]), .5)
        self.assertGreater(_personal_score([high]), _personal_score([low]))
        self.assertEqual(_personal_score([]), 0)

    def test_hidden_evidence_changes_invalidate_version_without_leaking_raw_feed(self):
        product = self.product()
        initial = product_to_api(product)
        version = initial['recommendation_evidence_version']
        self.assertEqual(len(version), 64)
        self.assertNotIn('feed_raw_json', initial)
        product.feed_raw_json = {'material': 'linen', 'fit': 'regular'}
        self.assertNotEqual(product_to_api(product)['recommendation_evidence_version'], version)
        product.feed_raw_json = {'fit': 'regular', 'material': 'cotton'}
        self.assertEqual(product_to_api(product)['recommendation_evidence_version'], version)
        product.last_seen_in_feed_at += timedelta(minutes=1)
        product.image_url = 'https://example.test/new-photo.jpg'
        self.assertEqual(product_to_api(product)['recommendation_evidence_version'], version)
        product.available_sizes_detailed = [{'raw_size': 'M', 'availability': 'unknown'}]
        self.assertNotEqual(product_to_api(product)['recommendation_evidence_version'], version)


if __name__ == '__main__':
    unittest.main()

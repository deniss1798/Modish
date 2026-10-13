"""Exact merchant size evidence must follow admission, replacement and replay."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app.models import Product
from app.services import intent_outfits, shopping_intent_service as intents
from app.services.fit_size_profile import update_size_profile
from app.services.catalog.size_resolver import RESOLVER_VERSION
import test_shopping_intent as fixtures


class IntentOutfitSizeMatchesTests(unittest.TestCase):
    setUp = fixtures.ShoppingIntentTests.setUp
    product = fixtures.ShoppingIntentTests.product
    create = fixtures.ShoppingIntentTests.create

    def _seed(self, *, alternative=False):
        update_size_profile(self.fit, sizes={
            'top': {'value': 'L', 'system': 'LETTER'},
            'bottom': {'value': '50', 'system': 'RU'},
            'one_piece': {'value': '44', 'system': 'EU'},
            'shoes': {'value': '7.5', 'system': 'UK'}})
        top = self.product(title='Рубашка', category='рубашки', price=5000,
            available_sizes=['L'], feed_raw_json={'size_system': 'LETTER', 'size': 'L'})
        bottom = self.product(price=6000, available_sizes=['50'],
            feed_raw_json={'size_system': 'RU', 'size': '50'})
        shoe = self.product(title='Лоферы', category='обувь', price=4000,
            available_sizes=['7.5'], feed_raw_json={'size_system': 'UK', 'size': '7.5'})
        other = self.product(title='Лоферы альтернативные', category='обувь', price=4500,
            source='alternate-shop', available_sizes=['7.5'],
            feed_raw_json={'size_system': 'UK', 'size': '7,5'}) if alternative else None
        self.db.flush()
        return {'top': top, 'bottom': bottom, 'shoes': shoe}, other

    def _issue(self):
        intent = self.create('Образ до 20000 без платья')
        result = intents.intent_response(self.db, self.user, intent, remember=True)
        self.assertTrue(result['outfits'])
        return intent, result['outfits'][0]

    def test_distinct_fit_slots_return_exact_merchant_value_system_and_provenance(self):
        products, _ = self._seed()
        _, outfit = self._issue()
        matches = outfit['size_matches']
        self.assertEqual(set(matches), {'top', 'bottom', 'shoes'})
        for slot, normalized, system in [('top', 'L', 'LETTER'), ('bottom', '50', 'RU'), ('shoes', '7.5', 'UK')]:
            with self.subTest(slot=slot):
                match = matches[slot]
                self.assertEqual((match['normalized_size'], match['size_system']), (normalized, system))
                self.assertEqual(match['raw_size'], normalized)
                self.assertEqual(match['availability'], 'available')
                self.assertIsNone(match['reason'])
                self.assertEqual(match['merchant_id'], products[slot].source)
                self.assertEqual(match['resolver_version'], RESOLVER_VERSION)
        self.assertEqual(outfit['confidence']['fit'], 'confirmed')

    def test_one_piece_uses_its_own_size_without_top_size_fallback(self):
        products, _ = self._seed()
        self.db.delete(products['top']); self.db.delete(products['bottom'])
        self.product(title='Платье', category='платья', price=7000,
            available_sizes=['44'], feed_raw_json={'size_system': 'EU'})
        self.db.flush()
        intent = self.create('Образ до 20000')
        outfit = intents.intent_response(self.db, self.user, intent, remember=True)['outfits'][0]
        self.assertEqual(set(outfit['size_matches']), {'one_piece', 'shoes'})
        self.assertEqual((outfit['size_matches']['one_piece']['normalized_size'],
            outfit['size_matches']['one_piece']['size_system']), ('44', 'EU'))

    def test_invalid_size_evidence_never_reaches_outfit_serialization(self):
        products, _ = self._seed()
        shoe = products['shoes']
        baseline = {key: deepcopy(getattr(shoe, key)) for key in
            ('feed_raw_json', 'available_sizes', 'is_available', 'availability_status')}
        cases = [
            {'feed_raw_json': {'size_system': 'EU'}},
            {'feed_raw_json': {}},
            {'available_sizes': ['8']},
            {'available_sizes_detailed': [{'raw_size': '7.5', 'availability': 'out_of_stock'}]},
            {'available_sizes_detailed': [{'raw_size': '7.5', 'availability': 'unknown'}]},
            {'is_available': 0, 'availability_status': 'out_of_stock'},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                for key, value in baseline.items(): setattr(shoe, key, deepcopy(value))
                shoe.available_sizes_detailed = []
                for key, value in changes.items(): setattr(shoe, key, value)
                self.db.flush()
                intent = self.create('Образ до 20000 без платья')
                with patch.object(intent_outfits, 'serialize_outfit',
                        side_effect=AssertionError('Invalid outfit must not serialize')):
                    result = intents.intent_response(self.db, self.user, intent, remember=True)
                self.assertEqual(result['outfits'], [])

    def test_replacement_keeps_locked_sizes_and_recomputes_new_merchant_evidence(self):
        self._seed(alternative=True)
        intent, before = self._issue()
        updated = intents.replace_outfit_item(self.db, self.user, intent.id, before['id'],
            expected_version=1, slot='shoes')
        after = intents.intent_response(self.db, self.user, updated, remember=True)['outfits'][0]
        for slot in ('top', 'bottom'):
            self.assertEqual(after['items'][slot], before['items'][slot])
            self.assertEqual(after['size_matches'][slot], before['size_matches'][slot])
        self.assertNotEqual(after['items']['shoes'], before['items']['shoes'])
        shoe = self.db.get(Product, after['items']['shoes'])
        self.assertEqual(after['size_matches']['shoes']['merchant_id'], shoe.source)
        self.assertEqual(after['size_matches']['shoes']['raw_size'], shoe.feed_raw_json['size'])
        self.assertEqual((after['size_matches']['shoes']['normalized_size'],
            after['size_matches']['shoes']['size_system']), ('7.5', 'UK'))

    def test_replay_revalidates_current_fit_and_stock_without_reusing_old_size(self):
        products, _ = self._seed()
        intent, before = self._issue()
        shoe = products['shoes']
        shoe.available_sizes = ['8']; shoe.feed_raw_json = {'size_system': 'UK', 'size': '8'}
        update_size_profile(self.fit, sizes={'shoes': {'value': '8', 'system': 'UK'}})
        self.db.flush()
        with patch.object(intents, 'build_intent_outfits', side_effect=AssertionError('Replay cannot rebuild')):
            replay = intents.intent_response(self.db, self.user, intent)['outfits'][0]
        self.assertEqual(replay['id'], before['id'])
        self.assertEqual(replay['size_matches']['shoes']['normalized_size'], '8')
        self.assertEqual(before['size_matches']['shoes']['normalized_size'], '7.5')
        shoe.feed_raw_json = {'size_system': 'EU', 'size': '8'}
        self.db.flush()
        with patch.object(intent_outfits, 'serialize_outfit',
                side_effect=AssertionError('Revalidation must exclude changed system')):
            self.assertEqual(intents.intent_response(self.db, self.user, intent)['outfits'], [])
        self.assertEqual(intent.context_json['outfits'][0]['items'], before['items'])


if __name__ == '__main__':
    unittest.main()

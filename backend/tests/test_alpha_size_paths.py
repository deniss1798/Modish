"""Alpha0.1 acceptance on a mixed fixed synthetic catalogue, without a gate oracle."""
import unittest
from uuid import uuid4

from app.services import activation_service as activation
from app.services import recommendation_engine as feed
from app.services import shopping_intent_service as intents
from app.services.fit_size_profile import submitted_sizes, update_size_profile
import test_activation as fixtures


class AlphaSizePathsTests(unittest.TestCase):
    product = fixtures.ActivationTests.product
    tearDown = fixtures.ActivationTests.tearDown

    def setUp(self):
        fixtures.ActivationTests.setUp(self)

    def check_session(self, wanted, wrong, system):
        update_size_profile(self.fit, sizes=submitted_sizes(clothing_size=wanted, clothing_system=system,
                           shoe_size='38', shoe_system='EU'))
        # The confirmed slots must win over a stale legacy field/session hint.
        self.fit.clothing_size = 'M'
        self.session.shoe_size = '39'
        allowed, rejected = set(), set()
        for category in ('рубашки', 'брюки', 'платья', 'юбки', 'футболки', 'обувь'):
            footwear = category == 'обувь'
            unit = 'EU' if footwear else system
            for index in range(4):
                product = self.product(category, available_sizes=['38' if footwear else wanted],
                    feed_raw_json={'size_system': unit}, price=1500)
                allowed.add(product.id)
            for index in range(3):
                product = self.product(category, available_sizes=['39' if footwear else wrong],
                    feed_raw_json={'size_system': unit}, price=999)
                rejected.add(product.id)
        self.db.flush()

        def assert_ids(ids):
            ids = list(ids)
            self.assertTrue(ids, 'Acceptance must include real positive results, not only empty outputs')
            self.assertTrue(set(ids) <= allowed)
            self.assertTrue(set(ids).isdisjoint(rejected))

        deck = activation.swipe_deck(self.db, self.user, self.session)
        self.assertGreaterEqual(len(deck['products']), 15)
        assert_ids(row['id'] for row in deck['products'])
        for row in deck['products'][:15]:
            activation.swipe_feedback(self.db, self.user, self.session, row['id'], 'like')
        wow = activation.complete_wow(self.db, self.user, self.session)
        self.assertTrue(wow['outfits'])
        for outfit in wow['outfits']:
            assert_ids(outfit['items'].values())
        self.db.commit()
        assert_ids(row.product.id for row in feed.generate_feed(self.db, self.user, limit=20))
        intent = intents.create_intent(self.db, self.user, text='Рубашка до 5000', request_id=uuid4().hex)
        result = intents.intent_response(self.db, self.user, intent, remember=True)
        assert_ids(row['product']['id'] for row in result['products'])

    def test_s_never_admits_xl_across_the_complete_personal_session(self):
        self.check_session('S', 'XL', 'LETTER')

    def test_l_never_admits_m_across_the_complete_personal_session(self):
        self.check_session('L', 'M', 'LETTER')

    def test_ru46_never_admits_ru48_or_uses_legacy_m_as_profile_size(self):
        self.check_session('46', '48', 'RU')


if __name__ == '__main__':
    unittest.main()

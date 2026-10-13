"""HTTP regressions for ordinary refusal and optional rich task feedback."""
from copy import deepcopy
import unittest

from sqlalchemy import select, func

from app.models import MetricEvent, RecommendationEventV2, TasteProfile, UserTasteFeature
import test_intent_http as http_fixtures


class OptionalDislikeReasonTests(unittest.TestCase):
    setUp = http_fixtures.IntentHTTPTests.setUp
    create = http_fixtures.IntentHTTPTests.create

    def _feedback(self, current, **fields):
        return self.client.post(f"/shopping-intents/{current['id']}/feedback",
            headers=self.headers, json={'expected_version': current['version'],
                'product_id': self.product.id, **fields})

    def _taste_snapshot(self):
        self.db.expire_all()
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        return deepcopy((taste.style_weights, taste.color_weights, taste.category_weights,
            taste.brand_weights, taste.profile_version))

    def _assert_ordinary_refusal(self, result):
        self.assertEqual(result.status_code, 200, result.text)
        response = result.json()
        self.assertEqual(response['version'], 2)
        self.assertEqual(response['products'], [])
        self.assertEqual(response['constraints']['budget_max'], 7000)
        event = self.db.scalar(select(RecommendationEventV2))
        self.assertEqual(event.event_type, 'dislike')
        self.assertEqual(event.product_id, self.product.id)
        self.assertFalse(event.meta_json.get('reason'))
        self.assertEqual(event.meta_json['surface'], 'shopping_intent')
        self.assertEqual(event.meta_json['intent_version'], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(MetricEvent)
            .where(MetricEvent.name == 'reason_dislike')), 0)
        self.assertLess(self._taste_snapshot()[2]['брюки'], 0)
        from app.services.intent_metrics import summary
        self.assertEqual(summary(self.db)['recommendation_funnel']['dislike_rate'], 1)

    def test_omitted_reason_records_ordinary_dislike_and_removes_product(self):
        self._assert_ordinary_refusal(self._feedback(self.create().json()))

    def test_explicit_null_reason_also_records_ordinary_dislike(self):
        self._assert_ordinary_refusal(self._feedback(self.create().json(), reason=None))

    def test_expensive_feedback_refines_task_budget_without_training_taste(self):
        current = self.create().json()
        before = self._taste_snapshot()
        result = self._feedback(current, reason='too_expensive')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['constraints']['budget_max'], 4800)
        self.assertEqual(self._taste_snapshot(), before)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(UserTasteFeature)), 0)
        self.assertEqual(self.db.scalar(select(MetricEvent).where(
            MetricEvent.name == 'reason_dislike')).meta_json['reason'], 'too_expensive')

    def test_task_only_feedback_excludes_product_without_training_taste(self):
        current = self.create().json()
        before = self._taste_snapshot()
        result = self._feedback(current, reason='not_for_context')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['products'], [])
        self.assertEqual(result.json()['constraints']['budget_max'], 7000)
        self.assertEqual(self._taste_snapshot(), before)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(UserTasteFeature)), 0)

    def test_supported_reason_is_preserved_and_unknown_reason_rejected(self):
        self.product.colors = ['black']
        self.db.commit()
        current = self.create().json()
        for reason in ('', 'other', 'made_up_reason'):
            rejected = self._feedback(current, reason=reason)
            self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
        accepted = self._feedback(current, reason='dont_like_color')
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(self.db.scalar(select(RecommendationEventV2)).meta_json['reason'], 'dont_like_color')
        kinds = set(self.db.scalars(select(UserTasteFeature.feature_type)))
        self.assertEqual(kinds, {'item', 'color'})

    def test_discovery_event_without_reason_remains_ordinary_dislike(self):
        from app.api.recommendations import router
        self.client.app.include_router(router)
        result = self.client.post('/recommendations/events', headers=self.headers,
            json={'event_type': 'dislike', 'product_id': self.product.id,
                'meta': {'surface': 'feed'}})
        self.assertEqual(result.status_code, 200, result.text)
        event = self.db.scalar(select(RecommendationEventV2))
        self.assertEqual(event.event_type, 'dislike')
        self.assertNotIn('reason', event.meta_json)


if __name__ == '__main__':
    unittest.main()

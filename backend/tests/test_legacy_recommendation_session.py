"""Legacy clients get real, current SKU facts with one atomic quota debit."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app import business
from app.api import recommendations as api
from app.db import Base
from app.models import (ActivationSession, MetricEvent, Outfit, Product, Recommendation,
    SavedRecommendation, StyleProfile, User, UserLimits)
from app.services import legacy_outfit_session as legacy, outfit_session as outfits
from app.services import recommendation_service as projection
from app.services.recommendation_revision import RecommendationSnapshotChanged
import test_outfit_session as fixtures


def seed_legacy_catalogue(db):
    user_id = fixtures.seed_outfit_refresh_catalogue(db)
    user = db.get(User, user_id)
    user.subscription_status = 'active'
    business.get_or_refresh_limits(db, user_id)
    db.commit()
    return user_id


def assert_legacy_batch(case, response, *, maximum=6000):
    case.assertTrue(response, 'A positive real catalogue must produce a legacy response')
    case.assertLessEqual(len(response), 3)
    seen = set()
    for row in response:
        content = row['content_json']
        ids = content['product_ids']
        case.assertEqual(set(ids), {'top', 'bottom', 'shoes'})
        case.assertEqual(set(content['items']), set(ids))
        case.assertEqual({item['product_id'] for item in content['items_v2']}, set(ids.values()))
        case.assertTrue(set(ids.values()).isdisjoint(seen))
        seen.update(ids.values())
        exact = sum((Decimal(str(item['price'])) for item in content['items_v2']), Decimal(0))
        case.assertEqual(Decimal(str(content['total_price'])), exact)
        case.assertLessEqual(exact, Decimal(maximum))
        for item in content['items_v2']:
            case.assertEqual(item['name'], content['items'][item['type']])
            case.assertTrue(item['url'])
            case.assertTrue(item['image_url'])
            case.assertTrue(item['brand'])
            case.assertNotIn(item['product_id'], fixtures.fixtures.BAD_IDS)


class LegacyRecommendationSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, autoflush=False) as db:
            self.user_id = seed_legacy_catalogue(db)

    def build(self, *, count=10):
        return legacy.build_legacy_outfits_response(self.engine, self.user_id, count=count)

    def used(self):
        with Session(self.engine) as db:
            return db.scalar(select(UserLimits.recommendation_batches_used).where(UserLimits.user_id == self.user_id))

    def test_old_count_ten_returns_three_real_outfits_with_one_debit(self):
        response = self.build()
        assert_legacy_batch(self, response)
        self.assertEqual(len(response), 3)
        self.assertEqual(self.used(), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 3)
            self.assertEqual(db.scalar(select(func.count()).select_from(Recommendation)), 3)
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 1)

    def test_missing_shoes_returns_empty_without_debit_or_partial_publication(self):
        with Session(self.engine) as db:
            db.execute(delete(Product).where(Product.category == 'обувь'))
            db.commit()
        self.assertEqual(self.build(), [])
        self.assertEqual(self.used(), 0)
        with Session(self.engine) as db:
            for model in (Outfit, Recommendation, MetricEvent):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)

    def test_projection_failure_after_all_flushes_rolls_back_outfits_records_quota_and_revision(self):
        with Session(self.engine) as db:
            before = db.get(User, self.user_id).recommendation_revision
        original, observed = legacy._project_and_charge, []

        def project(db, user, rows, response, rules):
            projected = original(db, user, rows, response, rules)
            assert_legacy_batch(self, projected)
            self.assertEqual(db.scalar(select(UserLimits.recommendation_batches_used)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Recommendation)), 3)
            observed.append(projected)
            raise RecommendationSnapshotChanged()

        with patch.object(legacy, '_project_and_charge', side_effect=project), self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(observed), 2)
        self.assertEqual(self.used(), 0)
        with Session(self.engine) as db:
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, before)
            for model in (Outfit, Recommendation, MetricEvent):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)

    def test_exhausted_preflight_never_ranks_or_publishes(self):
        with Session(self.engine) as db:
            db.scalar(select(UserLimits)).recommendation_batches_used = business.PLUS_MONTHLY_GENERATION_BATCHES
            db.commit()
        with patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=AssertionError('Denied quota cannot rank')), \
                self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 403)

    def test_direct_compatibility_generator_uses_real_products_and_does_not_commit(self):
        with Session(self.engine, autoflush=False) as db:
            rows = projection.generate_recommendations(db, db.scalar(select(StyleProfile)), count=99, scenario='daily')
            from app.api.serializers import rec_to_dict
            assert_legacy_batch(self, [rec_to_dict(row) for row in rows])
            self.assertEqual(len(rows), 3)
            db.rollback()
        self.assertEqual(self.used(), 0)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Recommendation)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)

    def test_legacy_reads_hide_fiction_revalidate_real_sizes_and_keep_saved_records(self):
        first = self.build(count=1)[0]
        with Session(self.engine) as db:
            fiction = Recommendation(id='unsupported-text-only', user_id=self.user_id, title='Old template',
                description='No product evidence', content_json={'items': {'top': 'Invented shirt'}})
            db.add(fiction)
            db.flush()
            for recommendation_id in (fiction.id, first['id']):
                db.add(SavedRecommendation(id=str(uuid4()), user_id=self.user_id, recommendation_id=recommendation_id))
            db.commit()
            with patch.object(api, 'user_from_token', side_effect=lambda credentials, session: session.get(User, self.user_id)):
                self.assertEqual({row['id'] for row in api.recommendations_outfits_legacy(db=db, credentials=None)}, {first['id']})
                self.assertEqual({row['recommendation_id'] for row in api.recommendations_saved(db=db, credentials=None)}, {first['id']})
                with self.assertRaises(HTTPException):
                    api.recommendation_detail(fiction.id, db=db, credentials=None)
                shoe_id = first['content_json']['product_ids']['shoes']
                db.get(Product, shoe_id).available_sizes = ['39']
                db.commit()
                self.assertEqual(api.recommendations_outfits_legacy(db=db, credentials=None), [])
                self.assertEqual(api.recommendations_saved(db=db, credentials=None), [])
                with self.assertRaises(HTTPException):
                    api.recommendation_detail(first['id'], db=db, credentials=None)
            self.assertEqual(db.scalar(select(func.count()).select_from(SavedRecommendation)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(Recommendation)), 2)

    def test_real_saved_history_is_uncapped_and_not_diversified_across_batches(self):
        first = self.build(count=1)[0]
        with Session(self.engine) as db:
            base = db.get(Recommendation, first['id'])
            for index in range(5):
                row = Recommendation(id=str(uuid4()), user_id=self.user_id, title=base.title, description=base.description,
                    content_json=deepcopy(base.content_json), tags_json=deepcopy(base.tags_json))
                db.add(row)
                db.flush()
                db.add(SavedRecommendation(id=str(uuid4()), user_id=self.user_id, recommendation_id=row.id))
            db.commit()
            with patch.object(api, 'user_from_token', side_effect=lambda credentials, session: session.get(User, self.user_id)):
                self.assertEqual(len(api.recommendations_saved(db=db, credentials=None)), 5)
                self.assertEqual(len(api.recommendations_outfits_legacy(db=db, credentials=None)), 3)

    def test_read_returns_current_price_without_rewriting_issued_recipe(self):
        first = self.build(count=1)[0]
        with Session(self.engine) as db:
            original = deepcopy(db.get(Recommendation, first['id']).content_json)
            product_id = first['content_json']['product_ids']['top']
            db.get(Product, product_id).price += Decimal('0.01')
            db.commit()
            with patch.object(api, 'user_from_token', side_effect=lambda credentials, session: session.get(User, self.user_id)):
                current = api.recommendation_detail(first['id'], db=db, credentials=None)
            self.assertEqual(Decimal(str(current['content_json']['total_price'])), Decimal(str(original['total_price'])) + Decimal('0.01'))
            self.assertEqual(db.get(Recommendation, first['id']).content_json, original)

    def test_no_activation_still_requires_whole_outfit_budget_at_one_kopeck_boundary(self):
        with Session(self.engine) as db:
            db.execute(delete(ActivationSession))
            for product in db.scalars(select(Product).where(Product.id.like('good-%'))):
                product.price = Decimal('2000.01') if product.category == 'обувь' else Decimal('2000')
            db.commit()
        self.assertEqual(self.build(count=1), [])
        self.assertEqual(self.used(), 0)
        with Session(self.engine) as db:
            db.get(Product, 'good-shoes-0').price = Decimal('2000')
            db.commit()
        exact = self.build(count=1)
        assert_legacy_batch(self, exact)
        self.assertEqual(exact[0]['content_json']['total_price'], 6000)

    def test_no_activation_read_hides_now_over_budget_recipe_even_if_each_item_fits(self):
        first = self.build(count=1)[0]
        with Session(self.engine) as db:
            db.execute(delete(ActivationSession))
            db.get(Product, first['content_json']['product_ids']['top']).price = Decimal('5900')
            db.commit()
            with patch.object(api, 'user_from_token', side_effect=lambda credentials, session: session.get(User, self.user_id)), \
                    self.assertRaises(HTTPException) as caught:
                api.recommendation_detail(first['id'], db=db, credentials=None)
            self.assertEqual(caught.exception.status_code, 404)
            self.assertIsNotNone(db.get(Recommendation, first['id']))

    def _malformed_stored_metadata(self, kind):
        first = self.build(count=1)[0]
        with Session(self.engine) as db:
            original = db.get(Recommendation, first['id'])
            content, tags = deepcopy(original.content_json), deepcopy(original.tags_json)
            if kind == 'tags':
                tags = ['old', 'invalid', 'metadata']
            elif kind == 'occasion':
                tags['occasion'] = [123]
            else:
                content['outfit_id'] = {'unsupported': 'reference'}
            changed = Recommendation(id=str(uuid4()), user_id=self.user_id, title=original.title,
                description=original.description, content_json=content, tags_json=tags)
            db.add(changed)
            db.flush()
            for row in (original, changed):
                db.add(SavedRecommendation(id=str(uuid4()), user_id=self.user_id, recommendation_id=row.id))
            db.commit()
            expected = {first['id'], changed.id} if kind == 'reference' else {first['id']}
            with patch.object(api, 'user_from_token', side_effect=lambda credentials, session: session.get(User, self.user_id)):
                feed = api.recommendations_outfits_legacy(db=db, credentials=None)
                saved = api.recommendations_saved(db=db, credentials=None)
                self.assertEqual({row['id'] for row in feed}, expected)
                self.assertEqual({row['recommendation_id'] for row in saved}, expected)
                if kind == 'reference':
                    fallback = api.recommendation_detail(changed.id, db=db, credentials=None)
                    self.assertEqual(fallback['content_json']['outfit_id'], changed.id)
                else:
                    with self.assertRaises(HTTPException) as caught:
                        api.recommendation_detail(changed.id, db=db, credentials=None)
                    self.assertEqual(caught.exception.status_code, 404)
            self.assertEqual(db.scalar(select(func.count()).select_from(Recommendation)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(SavedRecommendation)), 2)

    def test_stored_tags_list_does_not_break_valid_feed_or_saved_rows(self):
        self._malformed_stored_metadata('tags')

    def test_stored_nontext_occasion_does_not_break_valid_feed_or_saved_rows(self):
        self._malformed_stored_metadata('occasion')

    def test_stored_nontext_outfit_reference_is_canonicalized_to_recommendation_id(self):
        self._malformed_stored_metadata('reference')

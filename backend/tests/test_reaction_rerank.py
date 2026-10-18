"""Live HTTP reranking learns from reactions, beyond removing reacted cards.

Both untouched holdouts have the same confirmed size, category, brand, style,
price and quality. Only their colour differs. The same two-SKU candidate set is
requested before and after ten real HTTP reactions; changing its relative order
therefore cannot be explained by exclusion, refill or a mocked score/cache.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import create_access_token, get_db
from app.api.products import router as products_router
from app.api.recommendations import router as reactions_router
from app.db import Base
from app.models import (ActivationSession, Product, RecommendationEventV2,
    StyleProfile, TasteProfile, User, UserTasteFeature)
from app.services import activation_service as activation


class ReactionRerankTests(unittest.TestCase):
    @contextmanager
    def scenario(self, *, legacy=False):
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
            poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine, autoflush=False)
        self.user_id = 'reaction-rerank-user'
        self.reaction_ids = [f'reaction-{index:02}' for index in range(10)]
        self.holdouts = {'holdout-blue', 'holdout-red'}
        try:
            with self.sessions() as db:
                user = User(id=self.user_id, email='rerank@example.test', password_hash='unused')
                db.add(user)
                db.flush()
                session = activation.configure(db, user, gender_target='womenswear',
                    age_group='25-34', height=170, clothing_size='M',
                    clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
                    styles=['casual', 'minimalism'], price_segment='mass')
                session.completed_at = datetime.now(timezone.utc)
                profile = db.scalar(select(StyleProfile).where(StyleProfile.user_id == user.id))
                profile.completed_at = session.completed_at
                taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == user.id))
                # A small existing blue preference establishes a strict baseline;
                # no User Twin weights are fabricated for the new reactions.
                taste.category_weights = {'рубашки': 1.0}
                taste.color_weights = {'blue': 2.0}
                for pid, colour in [('holdout-blue', 'blue'), ('holdout-red', 'red')]:
                    db.add(self.product(pid, colour))
                # Seven positives (including saves), two colour-specific dislikes
                # and one weak skip are ten distinct, mixed product reactions.
                for index, pid in enumerate(self.reaction_ids):
                    db.add(self.product(pid, 'red' if index < 7 else 'blue'))
                if legacy:
                    # Existing migrated accounts have no ActivationSession. Their
                    # completed profile and durable prior 15-item history admit
                    # the warm legacy path; these old, unavailable items cannot
                    # enter either measured holdout page.
                    db.delete(session)
                    for index in range(15):
                        pid = f'old-warmup-{index:02}'
                        db.add(self.product(pid, 'black', brand='Previous brand', is_available=0,
                            availability_status='out_of_stock'))
                        db.add(RecommendationEventV2(id=f'old-reaction-{index:02}',
                            user_id=user.id, product_id=pid, event_type='like',
                            event_weight=1.5, meta_json={'surface': 'style_swipe'},
                            created_at=datetime.now(timezone.utc) - timedelta(days=31)))
                db.commit()
            app = FastAPI()
            app.include_router(products_router)
            app.include_router(reactions_router)

            def database():
                with self.sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database
            self.headers = {'Authorization': 'Bearer ' + create_access_token(self.user_id)}
            with TestClient(app) as client:
                self.client = client
                yield
        finally:
            engine.dispose()

    @staticmethod
    def product(pid, colour, **changes):
        fields = dict(id=pid, external_id=pid, source='baon', brand='Shared brand',
            title='Женская рубашка хлопковая', category='рубашки',
            description='Однотонная рубашка для повседневного гардероба.',
            price=Decimal('1599.50'), currency='RUB', gender_target='womenswear',
            colors=[colour], style_tags=['casual', 'minimalism'], available_sizes=['M'],
            feed_raw_json={'size_system': 'LETTER'},
            image_url=f'https://baon.ru/images/{pid}.jpg',
            product_url=f'https://baon.ru/products/{pid}', is_active=1,
            is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(changes)
        return Product(**fields)

    def feed(self, *, holdouts_only=False):
        params = [('limit', 20), ('categories', 'tops')]
        if holdouts_only:
            params.extend(('exclude_ids', pid) for pid in self.reaction_ids)
        response = self.client.get('/feed', params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        ids = [row['product']['id'] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        if holdouts_only:
            self.assertEqual(set(ids), self.holdouts)
        return rows

    def react(self, pid, event_type, *, reason=None):
        meta = {'surface': 'feed'}
        if reason:
            meta['reason'] = reason
        response = self.client.post('/recommendations/events', headers=self.headers,
            json={'product_id': pid, 'event_type': event_type, 'meta': meta})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def snapshot(self):
        with self.sessions() as db:
            taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
            twin = db.scalars(select(UserTasteFeature).where(
                UserTasteFeature.user_id == self.user_id)).all()
            return {
                'events': db.scalar(select(func.count()).select_from(RecommendationEventV2)),
                'revision': db.get(User, self.user_id).recommendation_revision,
                'weights': {kind: deepcopy(getattr(taste, f'{kind}_weights'))
                    for kind in ('category', 'brand', 'color', 'style')},
                'twin': {(row.feature_type, row.feature_value):
                    (row.preference_score, row.confidence, row.positive_count,
                     row.negative_count, row.last_signal_at, row.last_decay_at)
                    for row in twin},
            }

    def assert_ten_reactions_change_holdout_rank(self, *, legacy):
        with self.scenario(legacy=legacy):
            issued = self.feed()
            self.assertEqual({row['product']['id'] for row in issued},
                self.holdouts | set(self.reaction_ids))
            expected_algorithm = 'ranking_v4' if legacy else 'first_session_feed'
            self.assertTrue(all(row['ranking_algorithm'] == expected_algorithm for row in issued))
            before = self.feed(holdouts_only=True)
            before_state = self.snapshot()
            self.assertEqual([row['product']['id'] for row in before],
                ['holdout-blue', 'holdout-red'])
            self.assertGreater(before[0]['final_score'], before[1]['final_score'])
            reactions = ['like', 'save', 'like', 'save', 'like', 'like', 'save',
                'dislike', 'skip', 'dislike']
            for pid, action in zip(self.reaction_ids, reactions):
                result = self.react(pid, action,
                    reason='dont_like_color' if action == 'dislike' else None)
                self.assertFalse(result.get('idempotent', False))
            after = self.feed(holdouts_only=True)
            after_state = self.snapshot()
            self.assertEqual([row['product']['id'] for row in after],
                ['holdout-red', 'holdout-blue'])
            self.assertGreater(after[0]['final_score'], after[1]['final_score'])
            self.assertEqual(after_state['events'] - before_state['events'], 10)
            self.assertEqual(after_state['revision'] - before_state['revision'], 10)
            self.assertGreater(after_state['weights']['color']['red'],
                before_state['weights']['color'].get('red', 0))
            self.assertLess(after_state['weights']['color']['blue'],
                before_state['weights']['color']['blue'])
            self.assertGreater(after_state['twin'][('color', 'red')][0], 0)
            self.assertLess(after_state['twin'][('color', 'blue')][0], 0)
            # Neither holdout was reacted to. Delivery alone must not create an
            # item feature or turn candidate removal into the ranking oracle.
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(
                    RecommendationEventV2).where(
                    RecommendationEventV2.product_id.in_(self.holdouts))), 0)
            self.assertTrue(self.holdouts.isdisjoint({value for kind, value in
                after_state['twin'] if kind == 'item'}))

    def test_first_session_fresh_http_feed_uses_ten_new_reactions_including_save(self):
        self.assert_ten_reactions_change_holdout_rank(legacy=False)

    def test_warm_legacy_fresh_http_feed_uses_ten_new_reactions_including_save(self):
        self.assert_ten_reactions_change_holdout_rank(legacy=True)

    def test_repeated_save_cannot_train_taste_or_advance_revision_twice(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), self.scenario(legacy=legacy):
                self.feed()
                first = self.react(self.reaction_ids[0], 'save')
                self.assertFalse(first.get('idempotent', False))
                saved = self.snapshot()
                again = self.react(self.reaction_ids[0], 'save')
                self.assertTrue(again['idempotent'])
                self.assertEqual(again['weight'], 0)
                self.assertEqual(self.snapshot(), saved)


if __name__ == '__main__':
    unittest.main()

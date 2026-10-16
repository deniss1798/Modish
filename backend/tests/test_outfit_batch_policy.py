"""Recommendation batches stay short and never fill missing outfit roles."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.api import outfits as outfit_api
from app.models import ActivationSession, FitProfile, Outfit, Product, SourceRule, StyleProfile, User
from app.schemas.shopping_intent import IntentSpec
from app.services import activation_service, outfit_service, shopping_intent_service
from app.services.intent_outfits import build_intent_outfits
from app.services.outfit_policy import EMPTY_OUTFITS_MESSAGE, requested_outfit_count
from app.services.outfit_session import build_outfits_response
import test_outfit_session as fixtures
from test_intent_outfit_create_session import OUTFIT_TEXT


class OutfitBatchPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.db.close)
        self.user_id = fixtures.seed_outfit_refresh_catalogue(self.db)
        self.user = self.db.get(User, self.user_id)
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))

    def historical_outfits(self):
        # A Latin square provides five independently valid groups, every pair
        # sharing at most one garment. The expected cap is not due to scarcity.
        outfits = []
        for top, bottom in ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1)):
            items = {'top': f'good-top-{top}', 'bottom': f'good-bottom-{bottom}',
                'shoes': f'good-shoes-{(top + bottom) % 3}'}
            price = sum((self.db.get(Product, pid).price for pid in items.values()), Decimal(0))
            outfits.append(Outfit(id=f'historical-{top}-{bottom}', user_id=self.user_id,
                items_json=items, style_direction='daily', total_price=price,
                created_at=datetime.now(timezone.utc), is_saved=0))
        return outfits

    def test_shared_count_accepts_old_large_requests_but_caps_at_three(self):
        for count, expected in ((-1, 1), (0, 1), (1, 1), (2, 2), (3, 3), (10, 3), (1000, 3)):
            self.assertEqual(requested_outfit_count(count), expected)

    def test_large_normal_http_batch_returns_three_complete_real_outfits(self):
        self.db.close()
        result = build_outfits_response(self.engine, self.user_id, count=100)
        self.assertEqual(len(result), 3)
        fixtures.assert_complete_batch(self, result)

    def test_large_legacy_fallback_batch_remains_complete_and_at_most_three(self):
        self.db.execute(delete(ActivationSession))
        rows = outfit_service.generate_outfits(self.db, self.user, count=10, use_v2=False)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(set(row.items_json), {'top', 'bottom', 'shoes'})

    def test_missing_legacy_bottom_cannot_replace_current_rows_with_partial_outfits(self):
        old = self.historical_outfits()[0]
        self.db.add(old)
        self.db.execute(delete(Product).where(Product.category == 'брюки'))
        self.db.commit()
        before = self.user.recommendation_revision
        self.assertEqual(outfit_service.generate_outfits(self.db, self.user, count=10, use_v2=False), [])
        self.assertEqual(list(self.db.scalars(select(Outfit.id))), [old.id])
        self.assertEqual(self.user.recommendation_revision, before)

    def test_legacy_activation_cannot_assemble_a_total_over_its_confirmed_budget(self):
        for p in self.db.scalars(select(Product).where(Product.id.like('good-%'))):
            p.price = Decimal('3000')
        self.db.commit()
        self.assertEqual(outfit_service.generate_outfits(self.db, self.user, count=3, use_v2=False), [])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Outfit)), 0)

    def test_legacy_office_never_uses_sport_trousers_to_complete_a_smart_outfit(self):
        for p in self.db.scalars(select(Product).where(Product.id.like('good-bottom-%'))):
            p.title, p.style_tags = 'Брюки спортивные женские', ['sport']
        self.db.commit()
        self.assertEqual(outfit_service.generate_outfits(self.db, self.user,
            count=3, scenario='office', use_v2=False), [])

    def test_raw_legacy_emergency_cannot_bypass_a_source_rule(self):
        self.db.add(SourceRule(id='blocked-shoes-rule', source_id='outfit-merchant',
            rule_type='blocked_category', rule_value='обувь'))
        self.db.commit()
        with patch.object(outfit_service, '_catalog_products_for_outfits', return_value=[]), \
                patch.object(outfit_service, '_extend_buckets'):
            self.assertEqual(outfit_service.generate_outfits(self.db, self.user,
                count=3, use_v2=False), [])

    def test_intent_composer_caps_an_explicit_large_limit_with_positive_catalogue(self):
        intent = shopping_intent_service.create_intent(self.db, self.user, text=OUTFIT_TEXT, request_id='composer-cap')
        spec = IntentSpec.model_validate(intent.constraints_json)
        result = build_intent_outfits(self.db, self.user, spec, intent_id=intent.id, version=1, limit=100)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(set(row['items']) == {'top', 'bottom', 'shoes'} for row in result))

    def test_ready_old_intent_returns_three_of_five_valid_issued_outfits(self):
        intent = shopping_intent_service.create_intent(self.db, self.user, text=OUTFIT_TEXT, request_id='ready-cap')
        rows = self.historical_outfits()
        intent.context_json = {'result_version': 1, 'product_ids': [],
            'outfits': [{'id': row.id, 'items': row.items_json} for row in rows]}
        # Serialization derives the issued id from version + products; both
        # archived context and current size evidence remain unchanged.
        response = shopping_intent_service.issued_intent_response(self.db, self.user, intent)
        self.assertEqual(len(response['outfits']), 3)
        self.assertEqual(len(intent.context_json['outfits']), 5)

    def test_old_wow_snapshot_returns_three_and_empty_uses_explicit_honest_message(self):
        session = self.db.get(ActivationSession, self.user_id)
        rows = self.historical_outfits()
        self.db.add_all(rows)
        session.wow_json = [{'id': row.id, 'title': 'На каждый день'} for row in rows]
        self.db.flush()
        profile = self.db.scalar(select(StyleProfile).where(StyleProfile.user_id == self.user_id))
        response = activation_service.wow_response(self.db, self.user, session, profile, self.fit)
        self.assertEqual(len(response['outfits']), 3)
        self.assertEqual(len(session.wow_json), 5)
        self.assertIsNone(response['message'])
        self.db.execute(delete(Product).where(Product.category == 'обувь'))
        self.db.flush()
        response = activation_service.wow_response(self.db, self.user, session, profile, self.fit)
        self.assertEqual(response['outfits'], [])
        self.assertEqual(response['message'], EMPTY_OUTFITS_MESSAGE)

    def test_current_list_caps_valid_historical_outfits_but_saved_library_keeps_all_five(self):
        rows = self.historical_outfits()
        for row in rows:
            row.is_saved = 1
        self.db.add_all(rows)
        self.db.flush()
        with patch.object(outfit_api, 'user_from_token', return_value=self.user):
            self.assertEqual(len(outfit_api.outfits_list(db=self.db)), 3)
            self.assertEqual(len(outfit_api.outfits_list(db=self.db, saved_only=True)), 5)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Outfit)), 5)

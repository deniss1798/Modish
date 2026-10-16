"""User gender is an explicit choice, independent of sizes, photos and taste."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import fit_profile as fit_api, onboarding as onboarding_api, recommendations as recommendations_api
from app.db import Base
from app.models import ActivationSession, FitProfile, Product, Recommendation, SavedRecommendation, StyleProfile, User
from app.services import activation_service as activation, onboarding_service as onboarding
from app.services.fit_size_profile import missing_fit_requirements


class UserGenderChoiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(id=str(uuid4()), email='gender-choice@example.test', password_hash='unused')
        self.profile = StyleProfile(id=str(uuid4()), user_id=self.user.id, style_target='unknown')
        self.db.add_all([self.user, self.profile])
        self.db.commit()
        self.preferences = dict(age_group='25-34', clothing_size='M', shoe_size='38',
            clothing_size_system='LETTER', shoe_size_system='EU', height=170,
            styles=['casual', 'minimalism'], price_segment='mass')

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def fit(self):
        return self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))

    def test_model_insert_and_get_create_keep_gender_null_without_defaults(self):
        column = FitProfile.__table__.c.gender_target
        self.assertTrue(column.nullable)
        self.assertIsNone(column.default)
        self.assertIsNone(column.server_default)
        with patch.object(fit_api, 'user_from_token', return_value=self.user):
            response = fit_api.fit_profile_me(db=self.db, credentials=None)
            again = fit_api.fit_profile_me(db=self.db, credentials=None)
        self.assertIsNone(response['gender_target'])
        self.assertIsNone(again['gender_target'])
        self.assertIsNone(response['clothing_size'])
        self.assertIn('gender', response['missing_fit_requirements'])
        self.db.expire_all()
        self.assertIsNone(self.fit().gender_target)
        # A direct ORM insert also has no Python/DB default.
        other = User(id=str(uuid4()), email='gender-direct@example.test', password_hash='unused')
        self.db.add(other)
        direct = FitProfile(id=str(uuid4()), user_id=other.id, height_cm=170)
        self.db.add(direct)
        self.db.commit()
        self.db.refresh(direct)
        self.assertIsNone(direct.gender_target)

    def test_status_reports_null_and_requires_confirmation_without_creating_fit(self):
        with patch.object(onboarding_api, 'user_from_token', return_value=self.user):
            status = onboarding_api.get_onboarding_status(db=self.db, credentials=None)
        self.assertIsNone(status['gender_target'])
        self.assertFalse(status['preferences_ready'])
        self.assertTrue(status['fit_confirmation_required'])
        self.assertIn('gender', status['missing_fit_requirements'])
        self.assertIsNone(self.fit())

    def test_preferences_schema_requires_explicit_gender(self):
        for value in ({}, {'gender_target': None}, {'gender_target': ''}, {'gender_target': 'unknown'}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                onboarding_api.ActivationPreferencesRequest(**self.preferences, **value)
        for choice in ('womenswear', 'menswear', 'unisex'):
            payload = onboarding_api.ActivationPreferencesRequest(**self.preferences, gender_target=choice)
            self.assertEqual(payload.gender_target, choice)

    def test_configure_missing_gender_rejects_before_any_profile_or_session_write(self):
        for choice in (None, '', 'unknown'):
            with self.subTest(choice=choice), self.assertRaises(HTTPException) as error:
                activation.configure(self.db, self.user, **self.preferences, gender_target=choice)
            self.assertEqual(error.exception.status_code, 400)
            self.assertIsNone(self.fit())
            self.assertIsNone(self.db.get(ActivationSession, self.user.id))
            self.assertEqual(self.profile.style_target, 'unknown')

    def test_explicit_supported_choice_is_persisted_and_resumed(self):
        payload = onboarding_api.ActivationPreferencesRequest(**self.preferences, gender_target='womenswear')
        with patch.object(onboarding_api, 'user_from_token', return_value=self.user):
            onboarding_api.activation_preferences(payload, db=self.db, credentials=None)
            status = onboarding_api.get_onboarding_status(db=self.db, credentials=None)
        self.assertEqual(self.fit().gender_target, 'womenswear')
        self.assertEqual(self.profile.style_target, 'womenswear')
        self.assertEqual(status['gender_target'], 'womenswear')
        self.assertTrue(status['preferences_ready'])
        self.assertNotIn('gender', status['missing_fit_requirements'])

    def test_unsupported_focused_segment_never_overwrites_existing_explicit_choice(self):
        fit = FitProfile(id=str(uuid4()), user_id=self.user.id, height_cm=170, gender_target='menswear')
        self.db.add(fit)
        self.db.commit()
        for choice in ('menswear', 'unisex', None):
            with self.subTest(choice=choice), self.assertRaises(HTTPException) as error:
                activation.configure(self.db, self.user, **self.preferences, gender_target=choice)
            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(fit.gender_target, 'menswear')
            self.assertIsNone(self.db.get(ActivationSession, self.user.id))

    def test_non_gender_steps_and_photo_analysis_never_infer_gender(self):
        self.profile.photo_analysis = {'gender': 'female', 'gender_target': 'womenswear',
            'body_proportions': 'rectangle', 'color_palette': ['black']}
        onboarding.apply_photo_confirm(self.db, user=self.user, profile=self.profile,
            body_shape='rectangle', color_type=None, height_category=None)
        self.assertIsNone(self.fit().gender_target)
        onboarding.apply_step3(self.db, user=self.user, profile=self.profile,
            style_preferences=['romantic'], price_segment='mass')
        self.assertIsNone(self.fit().gender_target)
        self.db.commit()
        self.db.expire_all()
        self.assertIsNone(self.fit().gender_target)

    def test_step1_accepts_explicit_choices_and_invalid_input_does_not_erase_them(self):
        for choice, expected in (('male', 'menswear'), ('female', 'womenswear'), ('unisex', 'unisex')):
            onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender=choice, age_group=None)
            self.assertEqual(self.fit().gender_target, expected)
        for missing in (None, '', 'unknown'):
            with self.subTest(value=missing), self.assertRaises(HTTPException):
                onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender=missing, age_group=None)
            self.assertEqual(self.fit().gender_target, 'unisex')

    def test_explicit_profile_patch_preserves_all_choices_without_activation(self):
        for choice in ('menswear', 'womenswear', 'unisex'):
            payload = fit_api.FitProfilePatchRequest(height=170, gender_target=choice)
            with patch.object(fit_api, 'user_from_token', return_value=self.user):
                response = fit_api.fit_profile_patch(payload, db=self.db, credentials=None)
                read = fit_api.fit_profile_me(db=self.db, credentials=None)
            self.assertEqual(response['gender_target'], choice)
            self.assertEqual(read['gender_target'], choice)

    def test_missing_gender_closes_old_activation_even_with_saved_reactions(self):
        session = activation.configure(self.db, self.user, **self.preferences, gender_target='womenswear')
        session.swipes_json = {str(uuid4()): 'like' for _ in range(15)}
        self.fit().gender_target = None
        self.db.commit()
        self.assertEqual(missing_fit_requirements(self.fit()), ['gender'])
        for call in (lambda: activation.swipe_deck(self.db, self.user, session),
                     lambda: activation.swipe_feedback(self.db, self.user, session, 'old-card', 'like'),
                     lambda: activation.complete_wow(self.db, self.user, session)):
            with self.assertRaises(HTTPException) as error:
                call()
            self.assertEqual(error.exception.status_code, 409)
        session.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            activation.require_warm_feed(self.db, self.user)
        self.assertEqual(error.exception.status_code, 409)
        with patch.object(onboarding_api, 'user_from_token', return_value=self.user):
            status = onboarding_api.get_onboarding_status(db=self.db, credentials=None)
        self.assertFalse(status['preferences_ready'])
        self.assertTrue(status['fit_confirmation_required'])
        self.assertIsNone(status['gender_target'])

    def test_missing_gender_rejects_legacy_generation_before_debiting_quota(self):
        with patch.object(recommendations_api, 'user_from_token', return_value=self.user), \
             patch.object(recommendations_api.business, 'assert_can_generate') as quota, \
             patch.object(recommendations_api.business, 'generate_outfit_recommendations') as generate:
            with self.assertRaises(HTTPException) as error:
                recommendations_api.recommendations_generate(recommendations_api.GenerateRequest(),
                    db=self.db, credentials=None)
        self.assertEqual(error.exception.status_code, 409)
        quota.assert_not_called()
        generate.assert_not_called()

    def test_stored_legacy_cards_are_hidden_until_choice_without_deleting_saved_history(self):
        rec = Recommendation(id=str(uuid4()), user_id=self.user.id, title='Stored outfit', description='Synthetic history')
        saved = SavedRecommendation(id=str(uuid4()), user_id=self.user.id, recommendation_id=rec.id)
        self.db.add_all([rec, saved])
        self.db.commit()
        with patch.object(recommendations_api, 'user_from_token', return_value=self.user):
            self.assertEqual(recommendations_api.recommendations_outfits_legacy(db=self.db, credentials=None), [])
            self.assertEqual(recommendations_api.recommendations_saved(db=self.db, credentials=None), [])
            with self.assertRaises(HTTPException) as error:
                recommendations_api.recommendation_detail(rec.id, db=self.db, credentials=None)
            self.assertEqual(error.exception.status_code, 404)
            onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender='unisex', age_group=None)
            self.assertEqual(recommendations_api.recommendations_outfits_legacy(db=self.db, credentials=None), [])
            self.assertEqual(recommendations_api.recommendations_saved(db=self.db, credentials=None), [])
            with self.assertRaises(HTTPException) as error:
                recommendations_api.recommendation_detail(rec.id, db=self.db, credentials=None)
            self.assertEqual(error.exception.status_code, 404)
            activation.configure(self.db, self.user, gender_target='womenswear', **self.preferences)
            onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender='unisex', age_group=None)
            self.db.delete(self.db.get(ActivationSession, self.user.id))
            ids = {}
            for slot, category, title in (('top', 'рубашки', 'Рубашка хлопковая'),
                    ('bottom', 'брюки', 'Брюки прямые'), ('shoes', 'обувь', 'Лоферы кожаные')):
                sku = str(uuid4())
                ids[slot] = sku
                self.db.add(Product(id=sku, external_id=sku, source='baon', brand='Synthetic control',
                    title=title, category=category, price=1000, currency='RUB', gender_target='unisex',
                    available_sizes=['38' if slot == 'shoes' else 'M'],
                    feed_raw_json={'size_system': 'EU' if slot == 'shoes' else 'LETTER'},
                    image_url=f'https://baon.ru/images/{sku}.jpg', product_url=f'https://baon.ru/products/{sku}',
                    colors=['black'], style_tags=['casual'], is_active=1, is_available=1,
                    availability_status='in_stock', last_seen_in_feed_at=datetime.now(timezone.utc)))
            positive = Recommendation(id=str(uuid4()), user_id=self.user.id, title='Real control',
                description='Actual catalogue products', content_json={'product_ids': ids}, tags_json={'occasion': ['daily']})
            self.db.add(positive)
            self.db.flush()
            self.db.add(SavedRecommendation(id=str(uuid4()), user_id=self.user.id, recommendation_id=positive.id))
            self.db.commit()
            self.assertEqual({row['id'] for row in recommendations_api.recommendations_outfits_legacy(
                db=self.db, credentials=None)}, {positive.id})
            self.assertEqual({row['recommendation_id'] for row in recommendations_api.recommendations_saved(
                db=self.db, credentials=None)}, {positive.id})
            self.assertEqual(recommendations_api.recommendation_detail(positive.id,
                db=self.db, credentials=None)['id'], positive.id)
        self.assertIsNotNone(self.db.get(SavedRecommendation, saved.id))
        self.assertIsNotNone(self.db.get(Recommendation, rec.id))

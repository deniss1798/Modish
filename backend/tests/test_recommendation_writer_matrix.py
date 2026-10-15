"""Writer-enrollment slice. Unconverted paths must retain their ranking locks."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import fit_profile as fit_api, profile as profile_api, taste_profile as taste_api
from app import business
from app.db import Base
from app.models import ActivationSession, StyleProfile, TasteProfile, User, UserProductState, UserTasteFeature
from app.schemas.product_event import EventRequest
from app.services import activation_service as activation, onboarding_service as onboarding
from app.services.product_event_service import record_product_event
from app.services.profile_repository import ensure_style_profile, ensure_taste_profile
from app.services.recommendation_revision import profile_input_mutation
from app.services.user_twin_service import apply_event_to_user_twin, get_feature_preference
from fit_fixtures import confirmed_fit
from test_catalog_device_regressions import product


class RecommendationWriterMatrixTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        self.db = Session(engine, autoflush=False)
        self.addCleanup(engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id='writer', email='writer@example.test', password_hash='unused')
        self.profile = StyleProfile(id='style', user_id='writer', style_target='unknown')
        self.fit = confirmed_fit(id='fit', user_id='writer', height_cm=170,
            gender_target='womenswear', clothing_size='M', budget_max=10000, shoe_size='38', shoe_system='EU')
        self.taste = TasteProfile(id='taste', user_id='writer')
        self.product = product(source='baon', gender_target='womenswear', currency='RUB',
            style_tags=['casual'], available_sizes=['M'])
        self.db.add_all([self.user, self.profile, self.fit, self.taste, self.product])
        self.db.commit()

    def revision(self):
        return self.db.scalar(select(User.recommendation_revision).where(User.id == self.user.id))

    def test_profile_edit_apis_increment_once_and_replay_as_noop(self):
        cases = (
            (fit_api, fit_api.fit_profile_patch, fit_api.FitProfilePatchRequest(
                height=175, gender_target='womenswear', clothing_size='M', shoe_size='38', shoe_size_system='EU')),
            (profile_api, profile_api.users_me_preferences_patch, profile_api.UserPreferencesPatchRequest(
                height=176, fit_preference='oversized')),
            (profile_api, profile_api.style_profile_patch, profile_api.StyleProfilePatchRequest(
                profile_json={'analysis': {'color_palette': ['black']}})),
            (profile_api, profile_api.patch_style_target, profile_api.StyleTargetRequest(style_target='womenswear')),
            (taste_api, taste_api.taste_profile_patch, taste_api.TasteProfilePatchRequest(
                price_min=100, price_max=3500, preferred_fit='slim')),
        )
        for module, function, payload in cases:
            with self.subTest(writer=function.__name__), patch.object(module, 'user_from_token', return_value=self.user):
                before = self.revision()
                function(payload, db=self.db, credentials=None)
                self.assertEqual(self.revision(), before + 1)
                function(payload, db=self.db, credentials=None)
                self.assertEqual(self.revision(), before + 1)

    def test_legacy_onboarding_edits_rollback_and_nested_helpers_coalesce(self):
        onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender='female', age_group='25-34')
        onboarding.apply_step3(self.db, user=self.user, profile=self.profile,
            style_preferences=['casual', 'minimalism'], price_segment='mass')
        self.assertEqual(self.revision(), 1)
        self.db.rollback()
        self.assertEqual(self.revision(), 0)
        self.assertEqual(self.profile.style_target, 'unknown')
        onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender='female', age_group='25-34')
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        onboarding.apply_step1(self.db, user=self.user, profile=self.profile, gender='female', age_group='25-34')
        self.db.commit()
        self.assertEqual(self.revision(), 1)

    def test_product_history_is_versioned_and_duplicate_save_is_noop(self):
        record_product_event(self.db, self.user, EventRequest(product_id=self.product.id, event_type='save'))
        self.assertEqual(self.revision(), 1)
        replay = record_product_event(self.db, self.user, EventRequest(product_id=self.product.id, event_type='save'))
        self.assertTrue(replay['idempotent'])
        self.assertEqual(self.revision(), 1)
        record_product_event(self.db, self.user, EventRequest(product_id=self.product.id, event_type='dislike'), commit=False)
        self.assertEqual(self.revision(), 2)
        self.db.rollback()
        self.assertEqual(self.revision(), 1)

    def test_rejection_versions_hidden_exclusions_when_feature_writer_returns_no_targets(self):
        # Fault-inject the optional feature writer's no-op result. Exclusion
        # freshness must depend on the accepted event, not on feature writes.
        with patch('app.services.product_event_service.apply_event_to_user_twin', return_value=[]):
            record_product_event(self.db, self.user, EventRequest(product_id=self.product.id,
                event_type='dislike', meta={'reason': 'too_expensive'}), commit=False)
        self.assertEqual(list(self.db.scalars(select(UserTasteFeature))), [])
        self.assertIsNotNone(self.db.scalar(select(UserProductState)).hidden_until)
        self.assertEqual(self.revision(), 1)
        self.db.rollback()
        self.assertEqual(self.revision(), 0)
        self.assertEqual(list(self.db.scalars(select(UserProductState))), [])

    def test_initializers_mark_only_new_rows_and_keep_existing_reads_readonly(self):
        self.db.delete(self.taste)
        self.db.delete(self.profile)
        self.db.commit()
        ensure_taste_profile(self.db, self.user.id)
        ensure_style_profile(self.db, self.user.id)
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        ensure_taste_profile(self.db, self.user.id)
        ensure_style_profile(self.db, self.user.id)
        self.db.commit()
        self.assertEqual(self.revision(), 1)

    def test_direct_legacy_tag_preferences_participate_and_empty_tags_are_noop(self):
        business.apply_preferences_from_tags(self.db, self.user.id, {'colors': ['black']}, 0)
        business.apply_preferences_from_tags(self.db, self.user.id, {}, 2)
        self.db.commit()
        self.assertEqual(self.revision(), 0)
        field = next(iter(business.TAG_FIELD_TO_CATEGORY))
        business.apply_preferences_from_tags(self.db, self.user.id, {field: ['synthetic-value']}, 2)
        self.db.commit()
        self.assertEqual(self.revision(), 1)

    def test_allocation_does_not_version_taste_but_actual_first_exposure_does(self):
        session = ActivationSession(user_id=self.user.id, completed_at=datetime.now(timezone.utc))
        self.db.add(session)
        self.db.commit()
        with profile_input_mutation(self.db, self.user.id):
            session.issued_feed_ids = [self.product.id]
            session.swipe_product_ids = [self.product.id]
            session.wow_json = [{'id': 'allocated-only'}]
        self.db.commit()
        self.assertEqual(self.revision(), 0)
        activation.record_exposure_event(self.db, self.user, self.product.id, 'view', surface='feed')
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        activation.record_exposure_event(self.db, self.user, self.product.id, 'view', surface='feed')
        self.db.commit()
        self.assertEqual(self.revision(), 1)

    def test_direct_feature_write_and_persisted_decay_are_versioned_with_noop_replay(self):
        observed = datetime.now(timezone.utc)
        features = apply_event_to_user_twin(self.db, user_id=self.user.id, product=self.product,
            event_type='like', occurred_at=observed)
        feature_type, feature_value = features[0].feature_type, features[0].feature_value
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        for expected in (2, 2):
            get_feature_preference(self.db, user_id=self.user.id, feature_type=feature_type,
                feature_value=feature_value, apply_time_decay=True, as_of=observed + timedelta(days=30))
            self.db.commit()
            self.assertEqual(self.revision(), expected)

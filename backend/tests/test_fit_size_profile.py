import importlib.util
from copy import deepcopy
from pathlib import Path
import unittest
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import create_access_token
from app.api.fit_profile import fit_profile_me, fit_profile_patch, FitProfilePatchRequest
from app.api.onboarding import get_onboarding_status, ActivationPreferencesRequest
from pydantic import ValidationError
from app.db import Base
from app.models import FitProfile, Product, User
from app.services.fit_size_profile import (
    missing_fit_requirements, requested_size_for_product, size_profile_to_api,
    submitted_sizes, update_size_profile,
)


class FitSizeProfileTests(unittest.TestCase):
    def test_legacy_default_is_not_a_confirmed_size(self):
        fit = FitProfile(clothing_size='M', height_cm=170)
        self.assertEqual(set(missing_fit_requirements(fit)), {'top', 'bottom', 'one_piece', 'shoes', 'height'})
        self.assertEqual(requested_size_for_product(fit, Product(title='Брюки', category='брюки')), (None, None))

    def test_slots_are_independent_and_null_clears_without_guessing(self):
        fit = FitProfile(clothing_size=None)
        update_size_profile(fit, sizes=submitted_sizes(clothing_size='M', shoe_size='38', shoe_system='EU',
            overrides={'bottom': {'value': '48', 'system': 'RU'}}), height_confirmed=True)
        self.assertEqual(missing_fit_requirements(fit), [])
        for title, category, wanted in [('Рубашка', 'рубашки', ('M', 'LETTER')),
                ('Брюки', 'брюки', ('48', 'RU')), ('Платье', 'платья', ('M', 'LETTER')),
                ('Пальто', 'верхний_слой', ('M', 'LETTER')), ('Ботинки', 'обувь', ('38', 'EU'))]:
            self.assertEqual(requested_size_for_product(fit, Product(title=title, category=category)), wanted)
        update_size_profile(fit, sizes={'shoes': None})
        self.assertEqual(missing_fit_requirements(fit), ['shoes'])

    def test_numeric_profile_needs_a_system_and_height_needs_confirmation(self):
        fit = FitProfile(height_cm=170)
        update_size_profile(fit, sizes=submitted_sizes(clothing_size='48', shoe_size='38'))
        self.assertEqual(len(missing_fit_requirements(fit)), 5)

    def test_candidate_size_reads_preserve_shared_profile_and_see_new_confirmation(self):
        choice = {'value': 'M', 'system': 'LETTER', 'state': 'confirmed',
            'audit': {'evidence': ['user_confirmation']}}
        stored = {'height_confirmed': True, 'sizes': {slot: choice
            for slot in ('top', 'bottom', 'one_piece')}}
        stored['sizes']['shoes'] = {'value': '38', 'system': 'EU', 'state': 'confirmed'}
        fit = FitProfile(size_profile_json=stored)
        before = deepcopy(stored)
        for title, category, wanted in [('Рубашка', 'рубашки', ('M', 'LETTER')),
                ('Брюки', 'брюки', ('M', 'LETTER')), ('Платье', 'платья', ('M', 'LETTER')),
                ('Пальто', 'верхний_слой', ('M', 'LETTER')), ('Лоферы', 'обувь', ('38', 'EU')),
                ('Комбинезон', 'комбинезоны', (None, None))]:
            with self.subTest(category=category):
                self.assertEqual(requested_size_for_product(fit, Product(title=title, category=category)), wanted)
        self.assertEqual(stored, before)
        self.assertIs(fit.size_profile_json, stored)
        self.assertIs(stored['sizes']['top'], choice)
        self.assertIs(stored['sizes']['bottom'], choice)
        choice['value'] = 'L'
        self.assertEqual(requested_size_for_product(fit,
            Product(title='Пальто', category='верхний_слой')), ('L', 'LETTER'))
        choice['state'] = 'size_unknown'
        self.assertEqual(requested_size_for_product(fit,
            Product(title='Рубашка', category='рубашки')), (None, None))

    def test_candidate_size_read_does_not_expose_mutable_malformed_values(self):
        stored = {'sizes': {'top': {'value': ['M'], 'system': {'name': 'LETTER'}, 'state': 'confirmed'}}}
        fit = FitProfile(size_profile_json=stored)
        values = requested_size_for_product(fit, Product(title='Рубашка', category='рубашки'))
        values[0].append('L')
        values[1]['name'] = 'RU'
        self.assertEqual(stored['sizes']['top']['value'], ['M'])
        self.assertEqual(stored['sizes']['top']['system'], {'name': 'LETTER'})

    def test_api_snapshot_and_size_update_keep_shared_input_isolated(self):
        choice = {'value': 'M', 'system': 'LETTER', 'state': 'confirmed',
            'audit': {'evidence': ['user_confirmation']}}
        stored = {'sizes': {'top': choice, 'bottom': choice}}
        first = FitProfile(size_profile_json=stored)
        second = FitProfile(size_profile_json=stored)
        before = deepcopy(stored)
        snapshot = size_profile_to_api(first)
        snapshot['sizes']['top']['audit']['evidence'].append('api_edit')
        self.assertEqual(stored, before)
        overrides = {'top': {'value': 'L', 'system': 'LETTER'}}
        override_before = deepcopy(overrides)
        update_size_profile(first, sizes=overrides)
        self.assertEqual(overrides, override_before)
        self.assertEqual(second.size_profile_json, before)
        self.assertEqual(requested_size_for_product(first, Product(title='Рубашка')), ('L', 'LETTER'))
        self.assertEqual(requested_size_for_product(first, Product(title='Брюки')), ('M', 'LETTER'))
        self.assertEqual(requested_size_for_product(second, Product(title='Рубашка')), ('M', 'LETTER'))

    def test_new_profile_has_no_m_and_partial_patch_preserves_other_sizes(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            user = User(id=str(uuid4()), email='sizes@example.test', password_hash='unused')
            db.add(user); db.commit()
            credentials = HTTPAuthorizationCredentials(scheme='Bearer', credentials=create_access_token(user.id))
            response = fit_profile_me(db=db, credentials=credentials)
            self.assertIsNone(response['clothing_size'])
            self.assertEqual(response['size_profile']['sizes']['top']['state'], 'size_unknown')
            fit_profile_patch(FitProfilePatchRequest(height=168, gender_target='womenswear',
                clothing_size='M', shoe_size='38', shoe_size_system='EU'), db=db, credentials=credentials)
            response = fit_profile_patch(FitProfilePatchRequest(height=168, gender_target='womenswear',
                sizes={'bottom': {'value': '48', 'system': 'RU'}}), db=db, credentials=credentials)
            self.assertEqual(response['size_profile']['sizes']['bottom']['value'], '48')
            self.assertEqual(response['size_profile']['sizes']['top']['value'], 'M')
            self.assertEqual(response['size_profile']['sizes']['shoes']['value'], '38')
            self.assertEqual(response['missing_fit_requirements'], [])
            self.assertEqual(response['shoe_size'], '38')
            status = get_onboarding_status(db=db, credentials=credentials)
            self.assertFalse(status['fit_confirmation_required'])
            self.assertEqual(status['height'], 168)

    def test_activation_requires_explicit_height(self):
        values = dict(age_group='25-34', clothing_size='M', shoe_size='38',
            styles=['casual', 'minimalism'], price_segment='mass')
        with self.assertRaises(ValidationError):
            ActivationPreferencesRequest(**values)
        self.assertEqual(ActivationPreferencesRequest(**values, height=168).height, 168)

    def test_migration_preserves_old_values_but_does_not_confirm_them(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        file = Path(__file__).resolve().parents[1] / 'alembic/versions/20261007_0023_fit_sizes.py'
        spec = importlib.util.spec_from_file_location('fit_sizes_migration', file)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE fit_profiles (id VARCHAR(36) PRIMARY KEY, clothing_size VARCHAR(32) NOT NULL DEFAULT 'M')"))
            connection.execute(text("INSERT INTO fit_profiles (id) VALUES ('old')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text("INSERT INTO fit_profiles (id) VALUES ('new')"))
            rows = connection.execute(text('SELECT id, clothing_size, size_profile_json FROM fit_profiles ORDER BY id')).all()
            self.assertEqual(rows, [('new', None, '{}'), ('old', 'M', '{}')])
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            self.assertEqual(connection.scalar(text("SELECT clothing_size FROM fit_profiles WHERE id='new'")), '')

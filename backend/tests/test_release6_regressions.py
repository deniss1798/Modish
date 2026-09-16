import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from app.db import Base
from app.models import User, FitProfile, AuthSession
from app.services.catalog_scope import product_is_fashion
from app.services.catalog.catalog_quality import product_is_feed_eligible
from app.services.recommendation_engine import generate_feed
from app.services.auth_sessions import issue_session, token_hash
from app.api.auth import refresh, logout, RefreshRequest
from app.api.products import _feed_scored_items
from app.api.deps import user_from_token
from fastapi.security import HTTPAuthorizationCredentials
from test_styling_regressions import product


class Release6Regressions(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(id=str(uuid4()), email='release6@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()
        self.db.add(FitProfile(id=str(uuid4()), user_id=self.user.id, height_cm=180,
            gender_target='menswear', clothing_size='XL', budget_max=30000))
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def test_home_beauty_and_unknown_goods_never_enter_feed(self):
        for title, category in [('Сыворотка для лица', 'красота'),
            ('Подушка &laquo;Золотая альпака&raquo;', 'для дома'),
            ('Сервировочное блюдо в виде новогодней ели', 'посуда'),
            ('Набор косметики', 'рубашки'), ('Чехол для платья', 'платья'),
            ('Подушка в форме футболки', 'футболки'), ('Непонятный товар', 'разное')]:
            p = product(title, category)
            self.assertFalse(product_is_fashion(p), title)
            self.assertFalse(product_is_feed_eligible(p), title)
            self.db.add(p)
        good = product(); self.db.add(good); self.db.commit()
        self.assertEqual([s.product.id for s in generate_feed(self.db, self.user)], [good.id])

    def test_valid_clothing_is_preserved(self):
        for title, category in [('Майка DC Shoes Tank', 'разное'), ('Комплект брючный', 'костюмы'),
            ('Куртка мужская', 'куртки'), ('Лоферы кожаные', 'обувь'), ('Рубашка с принтом елочки', 'рубашки')]:
            self.assertTrue(product_is_fashion(product(title, category)), title)

    def test_inflight_swipes_excluded_even_before_events_are_committed(self):
        p, next_p = product(), product()
        self.db.add_all([p, next_p]); self.db.commit()
        rows = _feed_scored_items(self.db, self.user, limit=30, exclude_ids=[p.id])
        self.assertEqual([r['product']['id'] for r in rows], [next_p.id])

    def test_refresh_is_persistent_revocable_and_not_an_access_token(self):
        raw = issue_session(self.db, self.user.id); self.db.commit()
        stored = self.db.execute(select(AuthSession)).scalar_one()
        self.assertNotEqual(raw, stored.token_hash)
        self.assertEqual(token_hash(raw), stored.token_hash)
        request = RefreshRequest(refresh_token=raw)
        for _ in range(2):
            response = refresh(request, self.db)
            user = user_from_token(HTTPAuthorizationCredentials(scheme='Bearer', credentials=response.access_token), self.db)
            self.assertEqual(user.id, self.user.id)
        with self.assertRaises(HTTPException):
            user_from_token(HTTPAuthorizationCredentials(scheme='Bearer', credentials=raw), self.db)
        logout(request, self.db)
        with self.assertRaises(HTTPException): refresh(request, self.db)

    def test_expired_and_unknown_sessions_are_rejected(self):
        raw = issue_session(self.db, self.user.id); self.db.commit()
        session = self.db.execute(select(AuthSession)).scalar_one()
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1); self.db.commit()
        for token in (raw, 'unknown-session-token' * 3):
            with self.assertRaises(HTTPException): refresh(RefreshRequest(refresh_token=token), self.db)

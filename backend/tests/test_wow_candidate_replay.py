"""Repeated Wow ranking preserves full-catalog results as its inputs change."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import ActivationSession, FitProfile, Product, User
from app.services import activation_service as activation, wow_session as wow
from app.services.product_identity import product_identity_keys
from app.services.recommendation_context import context_for
from app.services.recommendation_revision import profile_input_signature
from app.services.wow_candidate_replay import wow_candidate_replay_scope
from test_wow_session import seed_wow_catalogue


class WowCandidateReplayTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id='replay-owner', email='replay@example.test', password_hash='unused')
        self.other = User(id='other-owner', email='other-replay@example.test', password_hash='unused')
        self.db.add_all([self.user, self.other])
        self.db.flush()
        for user, clothing, shoe in ((self.user, 'M', '38'), (self.other, 'S', '37')):
            activation.configure(self.db, user, gender_target='womenswear', age_group='25-34',
                height=170, clothing_size=clothing, clothing_size_system='LETTER',
                shoe_size=shoe, shoe_size_system='EU', styles=['casual', 'minimalism'],
                price_segment='mass')
        for index in range(85):
            self.add(f'top-{index:03d}', 'top', price=1500 + index * 10)
        for role in ('bottom', 'one_piece', 'shoes'):
            for index in range(12):
                self.add(f'{role}-{index:03d}', role, price=1600 + index * 10)
        self.add('zz-late-silk-cheap', 'top', price=350, material='silk')
        self.add('aa-alias-first', 'top', material='silk',
            image_url='https://catalog.example.test/shared-alias.jpg', group_id='alias-family-a')
        self.add('zz-alias-later', 'top', material='silk',
            image_url='https://catalog.example.test/shared-alias.jpg', group_id='alias-family-b')
        self.add('zz-alias-unblocked', 'top', material='silk', group_id='alias-family-a')
        self.db.commit()
        self.db.info.update(recommendation_read_only=True,
            recommendation_as_of=datetime.now(timezone.utc))
        self.session = self.db.get(ActivationSession, self.user.id)
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))

    def add(self, pid, role, *, price=1500, material='cotton', **changes):
        category, title = {
            'top': ('рубашки', 'Рубашка однотонная женская'),
            'bottom': ('брюки', 'Брюки прямые женские'),
            'one_piece': ('платья', 'Платье женское миди'),
            'shoes': ('обувь', 'Лоферы кожаные женские'),
        }[role]
        fields = dict(id=pid, external_id=pid, source='baon', title=title,
            brand='Replay catalogue', category=category, price=price, currency='RUB',
            gender_target='womenswear', available_sizes=['38'] if role == 'shoes' else ['M'],
            feed_raw_json={'size_system': 'EU' if role == 'shoes' else 'LETTER', 'material': material},
            colors=['black'], style_tags=['casual', 'minimalism', 'elegant'],
            image_url=f'https://catalog.example.test/{pid}.jpg',
            product_url=f'https://catalog.example.test/products/{pid}',
            is_active=1, is_available=1, is_deleted_from_feed=0,
            availability_status='in_stock', last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(changes)
        self.db.add(Product(**fields))

    def pool(self, *, user=None, session=None, **kwargs):
        user = self.user if user is None else user
        session = self.session if session is None else session
        evaluations = {}
        rows = activation.candidate_pool(self.db, user, session,
            context=context_for('daily'), outfit_scenario='daily',
            taste_features={('material', 'silk'): (1.0, 1.0)},
            outfit_evaluations=evaluations, **kwargs)
        return [(row.id, evaluations[row]) for row in rows]

    @contextmanager
    def reference(self):
        scope = self.db.info.pop('_wow_candidate_replay', None)
        try:
            yield
        finally:
            if scope is not None:
                self.db.info['_wow_candidate_replay'] = scope

    def assert_full_catalogue_parity(self, **kwargs):
        actual = self.pool(**kwargs)
        with self.reference():
            expected = self.pool(**kwargs)
        self.assertEqual(actual, expected)
        return actual

    def scope(self, **kwargs):
        return wow_candidate_replay_scope(self.db, self.user.id,
            profile_input_signature(self.db, self.user.id), **kwargs)

    def test_late_affinity_cheap_overlap_aliases_and_changed_exclusions_match_full_scan(self):
        with self.scope():
            first = self.assert_full_catalogue_parity()
            ids = [pid for pid, _ in first]
            self.assertIn('zz-late-silk-cheap', ids)
            self.assertIn('aa-alias-first', ids)
            self.assertNotIn('zz-alias-later', ids)
            self.assertNotIn('zz-alias-unblocked', ids)
            self.assertEqual(len([pid for pid in ids if pid.startswith('top-')
                or pid in {'zz-late-silk-cheap', 'aa-alias-first'}]), 50)
            excluded = set(ids[:4]) | {'aa-alias-first'}
            keys = set().union(*(product_identity_keys(self.db.get(Product, pid)) for pid in excluded))
            second = self.assert_full_catalogue_parity(exclude_ids=excluded,
                outfit_excluded_keys=keys)
            self.assertFalse(set(pid for pid, _ in second) & excluded)
            self.assertNotIn('zz-alias-later', [pid for pid, _ in second])
            # Removing prior exclusions must restore every eligible candidate,
            # including rows omitted from the first fifty finalists.
            self.assertEqual(self.assert_full_catalogue_parity(), first)
            later_alias = self.db.get(Product, 'zz-alias-later')
            exposed = self.assert_full_catalogue_parity(
                outfit_excluded_keys=product_identity_keys(later_alias))
            self.assertIn('zz-alias-unblocked', [pid for pid, _ in exposed])

    def test_small_replay_limit_preserves_full_catalogue_and_late_candidates(self):
        with self.scope(limit=2):
            first = self.assert_full_catalogue_parity()
            self.assertIn('zz-late-silk-cheap', [pid for pid, _ in first])
            self.assert_full_catalogue_parity(exclude_ids={first[0][0], first[1][0]})

    def test_live_profile_and_session_json_changes_match_new_full_rankings(self):
        with self.scope():
            first = self.assert_full_catalogue_parity()
            liked = first[0][0]
            self.session.swipes_json[liked] = 'dislike'
            without = self.assert_full_catalogue_parity()
            self.assertNotIn(liked, [pid for pid, _ in without])
            self.session.swipes_json.pop(liked)
            self.assertEqual(self.assert_full_catalogue_parity(), first)
            sizes = self.fit.size_profile_json['sizes']
            for role in ('top', 'bottom', 'one_piece'):
                sizes[role]['value'] = 'S'
            self.assertTrue(all(self.db.get(Product, pid).category == 'обувь'
                for pid, _ in self.assert_full_catalogue_parity()))
            for role in ('top', 'bottom', 'one_piece'):
                sizes[role]['value'] = 'M'
            self.assertEqual(self.assert_full_catalogue_parity(), first)

    def test_owner_revision_other_owner_and_transaction_changes_cannot_reuse_stale_rankings(self):
        other_session = self.db.get(ActivationSession, self.other.id)
        with self.scope():
            first = self.assert_full_catalogue_parity()
            self.assertTrue(first)
            self.assertEqual(self.assert_full_catalogue_parity(user=self.other, session=other_session), [])
            self.user.recommendation_revision += 1
            self.fit.budget_max = 500
            reduced = self.assert_full_catalogue_parity()
            self.assertEqual([pid for pid, _ in reduced], ['zz-late-silk-cheap'])
            self.db.commit()
            self.fit.budget_max = 10000
            self.db.commit()
            self.assertEqual(self.assert_full_catalogue_parity(), first)

    @contextmanager
    def controlled_stock_clock(self):
        from app.services.catalog import stock_freshness
        from app.services import wow_candidate_replay as replay
        now = [self.db.info['recommendation_as_of']]

        class StockClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now[0] if tz is not None else now[0].replace(tzinfo=None)

        with patch.object(stock_freshness, 'datetime', StockClock), \
                patch.object(replay, 'datetime', StockClock):
            yield now

    def test_future_observation_entering_clock_skew_window_matches_a_new_full_scan(self):
        observed = self.db.info['recommendation_as_of'] + timedelta(minutes=5, seconds=1)
        self.add('zz-future-silk', 'top', material='silk', price=400,
            last_seen_in_feed_at=observed)
        self.db.commit()
        with self.controlled_stock_clock() as now, self.scope():
            first = self.assert_full_catalogue_parity()
            self.assertNotIn('zz-future-silk', [pid for pid, _ in first])
            now[0] += timedelta(seconds=2)
            second = self.assert_full_catalogue_parity()
            self.assertIn('zz-future-silk', [pid for pid, _ in second])

    def test_stock_expiring_during_repeated_ranking_is_removed(self):
        observed = self.db.info['recommendation_as_of'] - timedelta(hours=72) + timedelta(seconds=1)
        self.add('zz-expiring-silk', 'top', material='silk', price=400,
            last_seen_in_feed_at=observed)
        self.db.commit()
        with self.controlled_stock_clock() as now, self.scope():
            first = self.assert_full_catalogue_parity()
            self.assertIn('zz-expiring-silk', [pid for pid, _ in first])
            now[0] += timedelta(seconds=2)
            second = self.assert_full_catalogue_parity()
            self.assertNotIn('zz-expiring-silk', [pid for pid, _ in second])


class WowReplayProposalTests(unittest.TestCase):
    def test_cached_and_uncached_wow_keep_all_three_titles_choices_scores_and_reasons(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with Session(engine, autoflush=False) as db:
            owner = seed_wow_catalogue(db)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        from app.services import wow_candidate_replay as replay
        checked_at = datetime.now(timezone.utc)

        class RequestClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return checked_at if tz is not None else checked_at.replace(tzinfo=None)

        # Decayed Taste must use the same request clock in both rankings.
        with patch.object(wow, 'datetime', RequestClock):
            with patch.object(replay, 'wow_candidate_replay_scope',
                    side_effect=lambda *args, **kwargs: _empty_scope()), \
                    patch.object(wow, 'wow_candidate_replay_scope',
                        side_effect=lambda *args, **kwargs: _empty_scope(), create=True):
                reference = wow._prepare(sessions, owner)
            actual = wow._prepare(sessions, owner)
        choices = lambda proposal: [(batch.scenario, batch.title, batch.choice)
            for batch in proposal.batches]
        self.assertEqual(len(actual.batches), 3)
        self.assertEqual(choices(actual), choices(reference))


@contextmanager
def _empty_scope():
    yield


if __name__ == '__main__':
    unittest.main()

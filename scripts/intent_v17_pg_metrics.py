"""Positive outfit metric contract in an isolated PostgreSQL transaction."""
from datetime import datetime, timezone
import json, os, sys
from uuid import uuid4
sys.path[:0] = ['/app', '/app/tests']
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
assert make_url(os.environ['DATABASE_URL']).host == 'modish_foundation_test_pg'
assert make_url(os.environ['DATABASE_URL']).database == 'modish_test'
from app.db import SessionLocal
from app.models import User, Product, FitProfile
from app.services import activation_service as activation
from app.services.intent_metrics import summary
import test_shopping_intent as fixtures
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    before = db.scalar(select(func.count()).select_from(Product))
    user = User(id=str(uuid4()), email='pg-metric-'+uuid4().hex+'@example.test', password_hash='disabled-test-account')
    uid = user.id
    db.add(user); db.flush()
    activation.configure(db, user, gender_target='womenswear', age_group='25-34', height=168, clothing_size='48',
        clothing_size_system='RU', shoe_size='38', shoe_size_system='EU',
        styles=['casual', 'minimalism'], price_segment='mid')
    case = fixtures.ShoppingIntentTests()
    case.db, case.user = db, user
    case.fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    # This fixture supplies explicit source-size evidence; it does not pretend
    # real merchants gained known sizes or renew their stock observations.
    case.test_outfit_rates_use_visible_unique_outfits_and_ignore_wrong_owner()
    measured = summary(db)
    report = {'scope': 'isolated_postgresql_synthetic_contract_not_real_coverage',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'image_id': os.environ['AUDIT_IMAGE_ID'],
        'checks_passed': True, 'real_stock_timestamps_modified': False,
        'outfit_funnel': measured['outfit_funnel'],
        'invalid_recommendation_rate': measured['invalid_recommendation_rate'],
        'test_writes_rolled_back': True}
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product)) == before
    assert db.get(User, uid) is None
print(json.dumps(report))

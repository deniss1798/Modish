"""HTTP contract and transaction boundaries for a temporary shopping task."""
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.api.deps import get_db, create_access_token
from app.api.shopping_intents import router
from app.models import User, ShoppingIntent, MetricEvent
from app.services import activation_service as activation
import test_shopping_intent as fixtures


class IntentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(bind=self.engine,autoflush=False)
        self.db=self.sessions();self.addCleanup(self.db.close)
        self.user=User(id=str(uuid4()),email='http-intent@example.test',password_hash='disabled')
        self.other=User(id=str(uuid4()),email='other-intent@example.test',password_hash='disabled')
        self.db.add_all([self.user,self.other]);self.db.flush()
        session=activation.configure(self.db,self.user,gender_target='womenswear', age_group='25-34',height=168,
            clothing_size='48',clothing_size_system='RU',shoe_size='38',shoe_size_system='EU',
            styles=['casual','minimalism'],price_segment='mid')
        session.completed_at=datetime.now(timezone.utc)
        self.product=fixtures.ShoppingIntentTests.product(self)
        self.db.commit()
        app=FastAPI();app.include_router(router)
        def database():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db]=database
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer '+create_access_token(self.user.id)}

    def create(self):
        return self.client.post('/shopping-intents',headers=self.headers,
            json={'text':'Чёрные брюки до 7000','request_id':'stable-http-request-key'})

    def test_authentication_validation_idempotency_and_warm_gate(self):
        self.assertEqual(self.client.post('/shopping-intents',json={'text':'Брюки','request_id':'stable-http-request-key'}).status_code,401)
        self.assertEqual(self.client.post('/shopping-intents',headers=self.headers,
            json={'text':'Брюки','request_id':'short'}).status_code,422)
        result=self.create();self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['products'][0]['product']['id'],self.product.id)
        self.assertEqual(self.create().json()['id'],result.json()['id'])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ShoppingIntent)),1)
        cold={'Authorization':'Bearer '+create_access_token(self.other.id)}
        self.assertEqual(self.client.post('/shopping-intents',headers=cold,
            json={'text':'Брюки','request_id':'other-stable-key-123'}).status_code,409)

    def test_version_conflict_does_not_apply_cheaper_twice(self):
        current=self.create().json();url=f"/shopping-intents/{current['id']}/refine"
        first=self.client.post(url,headers=self.headers,json={'text':'Дешевле','expected_version':1})
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['constraints']['budget_max'],5600)
        conflict=self.client.post(url,headers=self.headers,json={'text':'Дешевле','expected_version':1})
        self.assertEqual(conflict.status_code,409)
        read=self.client.get(f"/shopping-intents/{current['id']}",headers=self.headers).json()
        self.assertEqual((read['version'],read['constraints']['budget_max']),(2,5600))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.name=='intent_updated')),1)

    def test_get_revalidates_price_and_expiry_without_new_recommendations(self):
        current=self.create().json();url=f"/shopping-intents/{current['id']}"
        self.product.price=8000;self.db.commit()
        read=self.client.get(url,headers=self.headers)
        self.assertEqual(read.status_code,200)
        self.assertEqual(read.json()['products'],[])
        intent=self.db.get(ShoppingIntent,current['id'])
        intent.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);self.db.commit()
        self.assertEqual(self.client.get(url,headers=self.headers).status_code,410)

    def test_owner_cannot_read_another_session_even_after_warmup(self):
        current=self.create().json()
        session=activation.configure(self.db,self.other,gender_target='womenswear', age_group='25-34',height=168,
            clothing_size='48',clothing_size_system='RU',shoe_size='38',shoe_size_system='EU',
            styles=['casual','minimalism'],price_segment='mid')
        session.completed_at=datetime.now(timezone.utc);self.db.commit()
        foreign={'Authorization':'Bearer '+create_access_token(self.other.id)}
        self.assertEqual(self.client.get(f"/shopping-intents/{current['id']}",headers=foreign).status_code,404)

    def test_explicit_completion_is_idempotent_and_not_inferred_from_saved_results(self):
        from app.services.intent_metrics import summary
        current=self.create().json()
        close=f"/shopping-intents/{current['id']}/close"
        payload={'outcome':'completed','expected_version':1}
        self.assertEqual(self.client.post(close,headers=self.headers,json=payload).status_code,200)
        self.assertEqual(self.client.post(close,headers=self.headers,json=payload).status_code,200)
        self.assertEqual(self.client.get(f"/shopping-intents/{current['id']}",headers=self.headers).status_code,410)
        metrics=summary(self.db)
        self.assertEqual(metrics['events']['intent_completed'],1)
        self.assertEqual(metrics['intent_funnel']['completion_rate'],1)
        self.assertEqual(metrics['intent_funnel']['result_rate'],1)
        self.assertEqual(metrics['intent_funnel']['explicit_abandonment_rate'],0)
        self.assertEqual(metrics['intent_funnel']['merchant_click_rate'],0)

"""Admission validates web syntax without performing network checks per card."""
import unittest
from datetime import datetime, timezone
from urllib.parse import quote
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ProductSource

from app.services.catalog.affiliate_link_service import _public_web_url, merchant_url, resolve_outbound_url
from app.services.catalog.catalog_quality import product_is_feed_eligible
from app.services.catalog.quality_gate import evaluate_product
from app.services.catalog.catalog_records import store_record
from app.services.product_eligibility import evaluate_eligibility
from test_catalog_device_regressions import product
from test_catalog_foundation import offer
from fit_fixtures import confirmed_fit


class CatalogPublicUrlTests(unittest.TestCase):
    def test_valid_web_urls_keep_exact_path_query_and_supported_idn(self):
        for url in (
                'https://shop.example/product/123?color=red&size=M#details',
                'https://cdn.example:443/photo%20one.jpg?width=800',
                'http://shop.example:80/product/123',
                'https://SHOP.EXAMPLE./product/123',
                'https://магазин.рф/товар/123',
                'https://1.1.1.1/catalogue',
                'https://[2606:4700:4700::1111]/catalogue'):
            with self.subTest(url=url), patch('socket.getaddrinfo') as dns:
                self.assertEqual(_public_web_url(url), url)
                dns.assert_not_called()

    def test_invalid_urls_and_nonpublic_literals_are_never_admitted(self):
        for url in (
                None, 123, {}, '', 'data:image/png;base64,AA', 'file:///photo.jpg',
                '//shop.example/photo.jpg', 'https://@shop.example/p',
                'https://REPLACE_ME:REPLACE_ME@shop.example/p', 'https://shop.example:bad/p',
                'https://shop.example:65536/p', 'https://shop.example:0/p',
                'https://shop.example/a b.jpg', 'https://shop.example/\np.jpg',
                'https://shop.example/\tphoto.jpg', 'https://shop.example/\x7fp',
                'https://shop.example\\@localhost/p', 'https://shop%2eexample/p',
                'https://localhost./p', 'https://images.localhost/p',
                'https://images.localhost./p', 'https://images.local./p',
                'https://images.internal/p', 'https://127.0.0.1/p',
                'https://shop。localhost/p', 'https://shop．local/p',
                'https://shop｡internal/p',
                'https://127.1/p', 'https://0177.0.0.1/p', 'https://0x7f.0.0.1/p',
                'https://127.0.0.1./p', 'https://10.0.0.1/p',
                'https://[::1]/p', 'https://[::ffff:127.0.0.1]/p',
                'https://[64:ff9b::7f00:1]/p', 'https://224.0.0.1/p',
                'https://[ff02::1]/p', 'https://.shop.example/p',
                'https://shop..example/p', 'https://shop.example../p',
                'https://bad_host.example/p', 'https://-shop.example/p',
                'https://' + 'a' * 64 + '.example/p',
                'https://shop.example/' + 'x' * 8192):
            with self.subTest(url=url):
                self.assertEqual(_public_web_url(url), '')

    def test_outbound_unwraps_only_valid_destinations_and_can_use_a_valid_fallback(self):
        good = 'https://shop.example/product/123?size=M'
        bad = 'https://localhost./product/123'
        for destination, expected in ((good, good), (bad, '')):
            wrapped = 'https://partner.example/g/campaign/?ulp=' + quote(destination, safe='')
            self.assertEqual(merchant_url(wrapped), expected)
        self.assertEqual(resolve_outbound_url(product(original_url=bad, product_url=good)), good)
        self.assertEqual(resolve_outbound_url(product(original_url=bad,
            product_url='https://shop.example:bad/p', affiliate_url='javascript:alert(1)')), '')

    def test_same_gate_rejects_bad_image_and_bad_outbound_in_personal_admission(self):
        fit = confirmed_fit(gender_target='menswear', clothing_size='M', budget_max=10000)
        valid = product(currency='RUB')
        self.assertTrue(product_is_feed_eligible(valid))
        self.assertTrue(evaluate_eligibility(valid, fit).eligible)
        for changes, reason in (({'image_url': 'https://shop.example:bad/photo.jpg'}, 'invalid_image_url'),
                                ({'product_url': 'https://localhost./product/123'}, 'invalid_product_url')):
            candidate = product(currency='RUB', **changes)
            self.assertFalse(product_is_feed_eligible(candidate))
            self.assertFalse(evaluate_eligibility(candidate, fit).eligible)
            self.assertIn(reason, evaluate_product(candidate).reasons)


class CatalogUrlImportTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        Base.metadata.create_all(engine)
        self.source = ProductSource(id='url-source', code='baon', name='BAON', network='direct')
        self.db.add(self.source)
        self.db.commit()

    def test_malformed_web_urls_quarantine_without_creating_a_live_product(self):
        for changes, reason in (({'picture': 'https://cdn.example:bad/photo.jpg'}, 'invalid_image_url'),
                                ({'url': 'https://localhost./product/123'}, 'invalid_product_url')):
            with self.subTest(reason=reason):
                record, _ = store_record(self.db, self.source, offer(**changes), datetime.now(timezone.utc))
                self.assertEqual(record.status, 'quarantined')
                self.assertIn(reason, record.reasons)
                self.assertIsNone(record.product_id)

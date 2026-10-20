"""Fresh-process guards and real HTTP contracts for the local device fixture.

The normal test process may already have app/config imported. Each fixture test
therefore boots a new Python process, never resetting those shared application
modules or falling back to a developer's database/environment file.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest


WORKSPACE = Path(__file__).resolve().parents[2]
_CHILD_CHECK = r'''
import builtins
import io
import json
import logging
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

sys.path.insert(0, 'scripts')
import run_device_fixture as fixture
assert not any(name == 'app' or name.startswith('app.') for name in sys.modules)
mode = sys.argv[1]

if mode == 'bind':
    for host, port in (('0.0.0.0', 8773), ('127.0.0.1', 8774), ('::1', 8773)):
        try:
            fixture.validate_bind(host, port)
        except ValueError:
            pass
        else:
            raise AssertionError('A noncontract bind was admitted')
    fixture.validate_bind('127.0.0.1', 8773)
    assert not any(name == 'app' or name.startswith('app.') for name in sys.modules)
    print(json.dumps({'bind_guard': True}))
    raise SystemExit(0)

# These are intentionally unusable synthetic inherited values. Bootstrap must
# erase them before importing the application or opening any database.
os.environ.update(DATABASE_URL='postgresql://unreachable.invalid/forbidden',
    JWT_SECRET='SYNTHETIC_INHERITED_VALUE', AI_API_KEY='SYNTHETIC_FORBIDDEN_AI',
    ADMITAD_ENABLED='true', CLOSED_ALPHA_ENABLED='false')
original_open, original_io_open = builtins.open, io.open

def fenced_open(original):
    def opened(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)).name == '.env':
            raise AssertionError('A device fixture must never open .env')
        return original(path, *args, **kwargs)
    return opened

with patch('builtins.open', new=fenced_open(original_open)), patch('io.open', new=fenced_open(original_io_open)):
    app = fixture.create_fixture_app()
logging.getLogger().setLevel(logging.ERROR)
database = app.state.device_fixture_database
assert database.is_relative_to((fixture.WORKSPACE / '.tmp').resolve())
assert database.parent.name.startswith('device-fixture-') and database.exists()
assert os.environ['DATABASE_URL'] == 'sqlite:///' + database.as_posix()
assert os.environ['CLOSED_ALPHA_ENABLED'] == 'true'
assert os.environ['CLOSED_ALPHA_ALLOWED_EMAILS'] == 'device-alpha@example.com'
assert 'AI_API_KEY' not in os.environ and 'ADMITAD_ENABLED' not in os.environ
try:
    fixture.create_fixture_app()
except RuntimeError:
    pass
else:
    raise AssertionError('An already imported application must not be reused')

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.models import Product
from app.api import media_proxy

with app.state.device_fixture_sessions() as db:
    assert db.scalar(select(func.count()).select_from(Product)) == 65
    unknown = db.get(Product, fixture.UNKNOWN_SHOE_ID)
    assert unknown.available_sizes == ['38'] and unknown.feed_raw_json == {}

with TestClient(app) as client:
    assert client.get('/health/live').status_code == 200
    assert client.get('/health/ready').json()['database'] == 'ok'
    info = client.get('/__fixture__/info').json()
    assert info['known_size_products'] == 64 and info['unknown_size_products'] == 1
    assert len(info['categories']) == 8 and set(info['categories'].values()) == {8}
    assert not any(key in info for key in ('token', 'password', 'jwt', 'email', 'database_url'))
    with patch.object(media_proxy, '_download_image', side_effect=AssertionError('Production download must never run')) as download:
        image = client.get('/media/proxy-image', params={'url': 'https://baon.ru/images/device-shirts-00.jpg'})
        assert image.status_code == 200 and image.headers['content-type'] == 'image/jpeg'
        assert image.content == (fixture.WORKSPACE / 'test' / 'fixtures' / 'build12-blouse.jpg').read_bytes()
        for url in ('https://baon.ru/images/not-seeded.jpg', 'http://127.0.0.1/private',
                    'https://unrelated.example/image.jpg'):
            assert client.get('/media/proxy-image', params={'url': url}).status_code == 403
        download.assert_not_called()

    if mode == 'contract':
        registered = client.post('/auth/register', json={
            'email': fixture.FIXTURE_EMAIL, 'password': 'SYNTHETIC_DEVICE_PASSWORD'})
        assert registered.status_code == 200, 'Real local registration failed'
        headers = {'Authorization': 'Bearer ' + registered.json()['access_token']}
        base = ['fit_sizes_v1', 'shopping_intent_v1', 'catalogue_discovery_v1', 'catalog_status_v1']
        active = base + ['context_v1', 'dna_ranking_v1', 'personalized_search_v1']
        cold = client.get('/onboarding/status', headers=headers)
        assert cold.status_code == 200 and cold.json()['recommendation_features'] == base
        preferences = client.post('/onboarding/preferences', headers=headers, json={
            'gender_target': 'womenswear', 'age_group': '25-34', 'height': 170,
            'clothing_size': 'M', 'clothing_size_system': 'LETTER',
            'shoe_size': '38', 'shoe_size_system': 'EU',
            'styles': ['casual', 'minimalism'], 'price_segment': 'mass'})
        assert preferences.status_code == 200 and preferences.json()['recommendation_features'] == active
        status = client.get('/onboarding/status', headers=headers)
        assert status.json()['recommendation_features'] == active
        deck = client.get('/onboarding/style-swipe', headers=headers)
        assert deck.status_code == 200 and len(deck.json()['products']) >= 15
        assert fixture.UNKNOWN_SHOE_ID not in {row['id'] for row in deck.json()['products']}
        for row in deck.json()['products'][:15]:
            rated = client.post('/onboarding/style-swipe', headers=headers,
                json={'product_id': row['id'], 'reaction': 'like'})
            assert rated.status_code == 200
        wow = client.post('/onboarding/complete', headers=headers)
        assert wow.status_code == 200 and wow.json()['outfits'], 'Positive fixture must produce a real WOW'
        assert wow.json()['recommendation_features'] == active
        completed = client.get('/onboarding/status', headers=headers)
        assert completed.json()['recommendation_features'] == active
        feed = client.get('/feed', headers=headers)
        assert feed.status_code == 200 and feed.json()
        assert fixture.UNKNOWN_SHOE_ID not in {row['product']['id'] for row in feed.json()}
        catalogue = client.get('/catalog/discovery', headers=headers, params={'category': 'shoes'})
        assert catalogue.status_code == 200
        row = next(row for row in catalogue.json()['products'] if row['id'] == fixture.UNKNOWN_SHOE_ID)
        assert row['size_verification'] == 'not_verified' and row['catalogue_discovery'] is True
        print(json.dumps({'real_http_capabilities': True, 'wow_count': len(wow.json()['outfits']),
            'unknown_only_catalogue': True}))

if mode == 'fence':
    for action in (lambda: socket.getaddrinfo('baon.ru', 443),
            lambda: socket.gethostbyname('baon.ru'),
            lambda: socket.gethostbyaddr('192.0.2.1'),
            lambda: socket.getnameinfo(('192.0.2.1', 443), 0)):
        try:
            action()
        except PermissionError:
            pass
        else:
            raise AssertionError('External DNS was admitted')
    for operation in ('connect', 'connect_ex', 'bind', 'sendto'):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if operation == 'sendto' else socket.SOCK_STREAM) as sock:
            try:
                if operation == 'bind':
                    sock.bind(('0.0.0.0', 0))
                elif operation == 'sendto':
                    sock.sendto(b'SYNTHETIC', ('192.0.2.1', 53))
                else:
                    getattr(sock, operation)(('192.0.2.1', 443))
            except PermissionError:
                pass
            else:
                raise AssertionError('An external socket operation was admitted')
    with socket.socket() as listener, socket.socket() as sender:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        sender.connect(listener.getsockname())
        accepted, _ = listener.accept()
        with accepted:
            sender.sendall(b'SYNTHETIC')
            assert accepted.recv(9) == b'SYNTHETIC'
    print(json.dumps({'environment_fenced': True, 'external_network_denied': True,
        'loopback_works': True, 'media_intercepted': True}))
'''


class DeviceFixtureTests(unittest.TestCase):
    def check(self, mode):
        result = subprocess.run([sys.executable, '-c', _CHILD_CHECK, mode], cwd=WORKSPACE,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_bootstrap_is_fresh_fenced_and_network_cannot_leave_loopback(self):
        result = self.check('fence')
        self.assertEqual(result, {'environment_fenced': True, 'external_network_denied': True,
            'loopback_works': True, 'media_intercepted': True})

    def test_real_http_status_preferences_and_nonempty_wow_keep_the_same_capabilities(self):
        result = self.check('contract')
        self.assertTrue(result['real_http_capabilities'])
        self.assertGreaterEqual(result['wow_count'], 1)
        self.assertLessEqual(result['wow_count'], 3)
        self.assertTrue(result['unknown_only_catalogue'])

    def test_only_the_fixed_loopback_bind_is_accepted_before_any_app_import(self):
        self.assertEqual(self.check('bind'), {'bind_guard': True})


if __name__ == '__main__':
    unittest.main()

"""Run the real API against a fresh, isolated local device-test catalogue.

Only standard-library imports are allowed above the bootstrap boundary. This
utility never loads backend/.env, uses participant credentials or contacts shops.
It serves one fixed loopback address; restart it for a fresh UUID database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
from uuid import uuid4


WORKSPACE = Path(__file__).resolve().parents[1]
FIXTURE_HOST = '127.0.0.1'
FIXTURE_PORT = 8773
FIXTURE_EMAIL = 'device-alpha@example.com'
UNKNOWN_SHOE_ID = 'catalogue-unknown-shoe'
_CATEGORIES = (
    ('shirts', 'рубашки', 'Рубашка женская хлопковая'),
    ('tshirts', 'футболки', 'Футболка женская однотонная'),
    ('jumpers', 'джемперы', 'Джемпер женский хлопковый'),
    ('trousers', 'брюки', 'Брюки женские прямые'),
    ('jeans', 'джинсы', 'Джинсы женские прямые'),
    ('skirts', 'юбки', 'Юбка женская миди'),
    ('dresses', 'платья', 'Платье женское однотонное'),
    ('shoes', 'обувь', 'Лоферы женские кожаные'),
)


def validate_bind(host, port):
    if host != FIXTURE_HOST or port != FIXTURE_PORT:
        raise ValueError('Device fixture accepts only 127.0.0.1:8773')


def _loopback_host(value):
    if value == 'localhost':
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except (ValueError, TypeError):
        return False


def _install_loopback_network_guard():
    """Keep local HTTP/event-loop sockets; deny external sockets and DNS."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_bind = socket.socket.bind
    original_sendto = socket.socket.sendto
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_gethostbyaddr = socket.gethostbyaddr
    original_getnameinfo = socket.getnameinfo

    def require_local(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if (
                not isinstance(address, tuple)
                or not address
                or not _loopback_host(address[0])
            ):
                raise PermissionError(
                    'External network is disabled in the isolated device fixture'
                )

    def connect(sock, address):
        require_local(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        require_local(sock, address)
        return original_connect_ex(sock, address)

    def bind(sock, address):
        require_local(sock, address)
        return original_bind(sock, address)

    def sendto(sock, data, *args):
        if args:
            require_local(sock, args[-1])
        return original_sendto(sock, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        if not _loopback_host(host):
            raise PermissionError('External DNS is disabled in the isolated device fixture')
        return original_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host):
        if not _loopback_host(host):
            raise PermissionError('External DNS is disabled in the isolated device fixture')
        return original_gethostbyname(host)

    def gethostbyname_ex(host):
        if not _loopback_host(host):
            raise PermissionError('External DNS is disabled in the isolated device fixture')
        return original_gethostbyname_ex(host)

    def gethostbyaddr(host):
        if not _loopback_host(host):
            raise PermissionError('External DNS is disabled in the isolated device fixture')
        return original_gethostbyaddr(host)

    def getnameinfo(address, flags):
        if not isinstance(address, tuple) or not address or not _loopback_host(address[0]):
            raise PermissionError('External DNS is disabled in the isolated device fixture')
        return original_getnameinfo(address, flags)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.bind = bind
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    socket.gethostbyaddr = gethostbyaddr
    socket.getnameinfo = getnameinfo


def _fresh_database():
    base = (WORKSPACE / '.tmp').resolve()
    if not base.is_relative_to(WORKSPACE) or base.name != '.tmp':
        raise ValueError('Device fixture storage must remain inside workspace .tmp')
    base.mkdir(exist_ok=True)
    run_id = uuid4().hex
    directory = base / ('device-fixture-' + run_id)
    directory.mkdir(exist_ok=False)
    database = (directory / 'fixture.sqlite').resolve()
    if not database.is_relative_to(base) or database.exists():
        raise ValueError('Device fixture requires a fresh workspace database')
    return run_id, database


def _bootstrap_environment(database):
    environment = {
        name: os.environ[name]
        for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH')
        if name in os.environ
    }
    environment.update(
        DATABASE_URL='sqlite:///' + database.as_posix(),
        JWT_SECRET='SYNTHETIC_DEVICE_FIXTURE_ONLY_SIGNING_KEY',
        APP_ENV='test',
        PYTHON_DOTENV_DISABLED='1',
        CLOSED_ALPHA_ENABLED='true',
        CLOSED_ALPHA_ALLOWED_EMAILS=FIXTURE_EMAIL,
        RATE_LIMIT_PER_MINUTE='10000',
        AUTH_LOGIN_PER_MINUTE='60',
        AUTH_REGISTER_PER_MINUTE='20',
    )
    os.environ.clear()
    os.environ.update(environment)
    _install_loopback_network_guard()
    # Do not rely on a particular python-dotenv version respecting its env
    # flag: replace its loader before importing ANY application module.
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False


def _catalogue_fields():
    now = datetime.now(timezone.utc)
    for code, category, title in _CATEGORIES:
        shoe = code == 'shoes'
        for index in range(8):
            pid = f'device-{code}-{index:02}'
            yield {
                'id': pid,
                'external_id': pid,
                'source': 'baon',
                'title': f'{title} {index:02}',
                'category': category,
                'brand': f'Device fixture {code}',
                'description': 'Синтетическая карточка для локальной проверки приложения.',
                'price': Decimal(600 + index * 100),
                'currency': 'RUB',
                'gender_target': 'womenswear',
                'colors': ['black'],
                'style_tags': ['casual', 'minimalism', 'elegant'],
                'available_sizes': ['38' if shoe else 'M'],
                'feed_raw_json': {'size_system': 'EU' if shoe else 'LETTER'},
                'image_url': f'https://baon.ru/images/{pid}.jpg',
                'product_url': f'https://baon.ru/products/{pid}',
                'is_active': 1,
                'is_available': 1,
                'availability_status': 'in_stock',
                'is_deleted_from_feed': 0,
                'last_seen_in_feed_at': now,
            }
    yield {
        'id': UNKNOWN_SHOE_ID,
        'external_id': UNKNOWN_SHOE_ID,
        'source': 'baon',
        'title': 'Лоферы размер уточните',
        'category': 'обувь',
        'brand': 'Device fixture unknown size',
        'price': Decimal('900'),
        'currency': 'RUB',
        'gender_target': 'womenswear',
        'colors': ['black'],
        'style_tags': ['casual', 'minimalism'],
        'available_sizes': ['38'],
        'feed_raw_json': {},
        'image_url': f'https://baon.ru/images/{UNKNOWN_SHOE_ID}.jpg',
        'product_url': f'https://baon.ru/products/{UNKNOWN_SHOE_ID}',
        'is_active': 1,
        'is_available': 1,
        'availability_status': 'in_stock',
        'is_deleted_from_feed': 0,
        'last_seen_in_feed_at': now,
    }


def create_fixture_app():
    if any(name == 'app' or name.startswith('app.') for name in sys.modules):
        raise RuntimeError(
            'Create the device fixture in a fresh Python process before importing app'
        )
    run_id, database = _fresh_database()
    _bootstrap_environment(database)
    sys.path.insert(0, str(WORKSPACE / 'backend'))

    # Application/framework imports must stay below the environment fence.
    from fastapi import HTTPException, Query
    from fastapi.responses import Response
    from fastapi.routing import APIRoute
    from sqlalchemy.orm import sessionmaker

    from app.api.deps import get_db
    from app.db import Base, engine
    from app.main import API_VERSION, app
    from app.models import Product
    from app.services import photo_analysis_jobs

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    fields = list(_catalogue_fields())
    with sessions() as db:
        db.add_all(
            Product(**row)
            for row in fields
        )
        db.commit()

    def database_override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_override
    photo_analysis_jobs.SessionLocal = sessions
    image_path = (WORKSPACE / 'test' / 'fixtures' / 'build12-blouse.jpg').resolve()
    if not image_path.is_relative_to(WORKSPACE / 'test' / 'fixtures'):
        raise ValueError('Device fixture image must remain in test/fixtures')
    image = image_path.read_bytes()
    if not image.startswith(b'\xff\xd8'):
        raise ValueError('Device fixture requires the existing local JPEG')
    known_images = frozenset(row['image_url'] for row in fields)

    def fixture_image(url: str = Query(..., max_length=2048)):
        if url not in known_images:
            raise HTTPException(
                status_code=403,
                detail='Изображение не входит в локальную тестовую подборку.',
            )
        return Response(
            content=image,
            media_type='image/jpeg',
            headers={
                'X-Content-Type-Options': 'nosniff',
                'Cache-Control': 'no-store',
            },
        )

    # Only this isolated process intercepts exact seeded URLs. Production SSRF
    # rules and admission helpers are untouched, and no merchant is contacted.
    app.router.routes.insert(
        0,
        APIRoute('/media/proxy-image', fixture_image, methods=['GET']),
    )
    info = {
        'fixture': 'modish-device-local',
        'run_id': run_id,
        'version': API_VERSION,
        'known_size_products': 64,
        'unknown_size_products': 1,
        'categories': {code: 8 for code, *_ in _CATEGORIES},
        'host': FIXTURE_HOST,
        'port': FIXTURE_PORT,
    }

    @app.get('/__fixture__/info')
    def fixture_info():
        return info

    app.state.device_fixture_info = info
    app.state.device_fixture_database = database
    app.state.device_fixture_sessions = sessions
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Isolated Modish API for the local Android emulator'
    )
    parser.add_argument('--host', default=FIXTURE_HOST)
    parser.add_argument('--port', type=int, default=FIXTURE_PORT)
    args = parser.parse_args(argv)
    try:
        validate_bind(args.host, args.port)
    except ValueError as error:
        parser.error(str(error))
    app = create_fixture_app()
    print(json.dumps({'fixture_prepared': True, **app.state.device_fixture_info}), flush=True)
    import uvicorn

    uvicorn.run(
        app,
        host=FIXTURE_HOST,
        port=FIXTURE_PORT,
        access_log=False,
        log_level='warning',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

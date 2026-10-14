"""Keep legacy photo handlers disabled during build14 schema deployment/rollback.

This companion leaves the frozen build13 source unchanged. It must wrap every
worker exposed by the rollback API; ordinary reads and ASGI lifecycle pass through.
"""
import json
from urllib.parse import unquote


BLOCKED = frozenset(('/onboarding/photo', '/style-profile/analyze'))
BODY = json.dumps(
    {'detail': 'Анализ фото временно недоступен. Попробуйте позже.'},
    ensure_ascii=False,
).encode('utf-8')


def normalized_path(path):
    # ASGI path is decoded; also accept encoded fixtures/proxy representations.
    # Conservative blocking is safe here: these endpoints must stay unavailable.
    for _ in range(4):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    return path.rstrip('/')


class Guard:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if (scope['type'] == 'http' and scope.get('method', '').upper() == 'POST'
                and normalized_path(scope.get('path', '')) in BLOCKED):
            await send({'type': 'http.response.start', 'status': 503, 'headers': [
                (b'content-type', b'application/json; charset=utf-8'),
                (b'content-length', str(len(BODY)).encode('ascii')),
                (b'retry-after', b'300'), (b'cache-control', b'no-store'),
            ]})
            await send({'type': 'http.response.body', 'body': BODY})
            return
        await self.wrapped(scope, receive, send)


class LazyBase:
    def __init__(self):
        self.application = None

    async def __call__(self, scope, receive, send):
        if self.application is None:
            from app.main import app as base
            self.application = base
        await self.application(scope, receive, send)


app = Guard(LazyBase())

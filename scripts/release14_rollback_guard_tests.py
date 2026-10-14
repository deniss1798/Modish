"""Offline ASGI guard regressions; no DB, providers, credentials or network."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, '/app')
from release14_rollback_guard import Guard


class RollbackGuardTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, scope):
        received, sent, called = [], [], []

        async def receive():
            received.append(True)
            return {'type': 'http.request', 'body': b'photo fixture'}

        async def send(event):
            sent.append(event)

        async def base(actual, receiver, sender):
            called.append(actual)
            await receiver()
            await sender({'type': 'fixture.base'})

        await Guard(base)(scope, receive, send)
        return received, sent, called

    async def test_post_handlers_never_receive_upload_or_call_base(self):
        for path in ('/onboarding/photo', '/style-profile/analyze'):
            with self.subTest(path=path):
                received, sent, called = await self.invoke({'type': 'http', 'method': 'POST', 'path': path})
                self.assertEqual((received, called), ([], []))
                self.assertEqual(sent[0]['status'], 503)
                self.assertEqual(dict(sent[0]['headers'])[b'retry-after'], b'300')
                self.assertEqual(dict(sent[0]['headers'])[b'cache-control'], b'no-store')
                self.assertEqual(json.loads(sent[1]['body'])['detail'],
                                 'Анализ фото временно недоступен. Попробуйте позже.')

    async def test_trailing_and_encoded_aliases_are_blocked(self):
        for path in ('/onboarding/photo/', '/onboarding/photo//', '/style-profile/analyze/',
                     '/onboarding%2Fphoto', '/onboarding%252Fphoto', '/style-profile/%61nalyze'):
            with self.subTest(path=path):
                received, sent, called = await self.invoke({'type': 'http', 'method': 'POST', 'path': path})
                self.assertEqual((received, called), ([], []))
                self.assertEqual(sent[0]['status'], 503)

    async def test_get_options_and_other_posts_pass_through(self):
        for method, path in (('GET', '/health'), ('GET', '/onboarding/photo'),
                             ('OPTIONS', '/style-profile/analyze'), ('POST', '/auth/login'),
                             ('POST', '/onboarding/photo-extra')):
            with self.subTest(method=method, path=path):
                scope = {'type': 'http', 'method': method, 'path': path}
                received, sent, called = await self.invoke(scope)
                self.assertEqual(received, [True])
                self.assertEqual(called, [scope])
                self.assertEqual(sent, [{'type': 'fixture.base'}])

    async def test_lifespan_and_websocket_pass_through(self):
        for scope in ({'type': 'lifespan'}, {'type': 'websocket', 'path': '/onboarding/photo'}):
            received, sent, called = await self.invoke(scope)
            self.assertEqual(received, [True])
            self.assertEqual(called, [scope])
            self.assertEqual(sent, [{'type': 'fixture.base'}])


if __name__ == '__main__':
    unittest.main()

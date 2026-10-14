"""Caller-level SSRF/DoS regressions using raw in-memory httpx streams only."""
import asyncio
import gzip
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.api import media_proxy
from app.services.catalog import feed_import_service as feeds
from app.services.public_fetch import FetchDenied, MAX_REDIRECTS


_REAL_CLIENT = httpx.Client
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _records(address='1.1.1.1'):
  return [(socket.AF_INET6 if ':' in address else socket.AF_INET,
    socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
    (address, 443, 0, 0) if ':' in address else (address, 443))]


class _SyncBody(httpx.SyncByteStream):
  def __init__(self, chunks):
    self.chunks = chunks
    self.consumed = 0
    self.closed = False

  def __iter__(self):
    for chunk in self.chunks:
      self.consumed += 1
      if isinstance(chunk, Exception):
        raise chunk
      yield chunk

  def close(self):
    self.closed = True


class _AsyncBody(httpx.AsyncByteStream):
  def __init__(self, chunks):
    self.chunks = chunks
    self.consumed = 0
    self.closed = False

  async def __aiter__(self):
    for chunk in self.chunks:
      self.consumed += 1
      if isinstance(chunk, Exception):
        raise chunk
      yield chunk

  async def aclose(self):
    self.closed = True


class _WaitingBody(_AsyncBody):
  def __init__(self):
    super().__init__([])
    self.started = asyncio.Event()

  async def __aiter__(self):
    self.started.set()
    await asyncio.Event().wait()
    yield b'never available'


class _DribbleBody(_WaitingBody):
  async def __aiter__(self):
    self.started.set()
    while True:
      self.consumed += 1
      yield b'x'
      await asyncio.sleep(0.005)


class _ClientFactory:
  """Wrap real client constructors, replacing their transport without sockets."""
  def __init__(self, reply, *, asynchronous=False):
    self.reply = reply
    self.asynchronous = asynchronous
    self.clients = []
    self.options = []
    self.requests = []
    self.streams = []
    self.responses = []

  def _handle(self, request):
    self.requests.append(request)
    status, headers, chunks = self.reply(request, len(self.requests) - 1)
    body = chunks if isinstance(chunks, (httpx.SyncByteStream, httpx.AsyncByteStream)) else (
      _AsyncBody if self.asynchronous else _SyncBody)(chunks)
    self.streams.append(body)
    response = httpx.Response(status, headers=headers, stream=body)
    self.responses.append(response)
    return response

  def __call__(self, **options):
    self.options.append(options)
    client = (_REAL_ASYNC_CLIENT if self.asynchronous else _REAL_CLIENT)(
      transport=httpx.MockTransport(self._handle), **options)
    self.clients.append(client)
    return client


class _WaitingHeadersFactory(_ClientFactory):
  def __init__(self):
    super().__init__(None, asynchronous=True)
    self.started = asyncio.Event()

  async def _handle(self, request):
    self.requests.append(request)
    self.started.set()
    await asyncio.Event().wait()
    raise AssertionError('Stalled headers must be cancelled by the total deadline')


class _DownloadsCase:
  def start_dns(self):
    dns = patch('app.services.public_fetch.socket.getaddrinfo', return_value=_records())
    self.dns = dns.start()
    self.addCleanup(dns.stop)

  def assert_closed_and_pinned(self, factory):
    self.assertTrue(factory.clients)
    self.assertTrue(all(client.is_closed for client in factory.clients))
    self.assertTrue(all(stream.closed for stream in factory.streams))
    self.assertTrue(all(response.is_closed for response in factory.responses))
    self.assertEqual(len({id(client) for client in factory.clients}), len(factory.clients))
    for options in factory.options:
      self.assertIs(options['follow_redirects'], False)
      self.assertIs(options['trust_env'], False)
    for request in factory.requests:
      self.assertEqual(request.url.host, '1.1.1.1')
      self.assertEqual(request.headers['Accept-Encoding'], 'identity')
      self.assertNotIn('cookie', request.headers)
      self.assertEqual(request.extensions['sni_hostname'], request.headers['Host'])


class MediaPublicDownloadTests(_DownloadsCase, unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.start_dns()

  async def download(self, factory, url='https://baon.ru/start', *, limit=64, chunk_size=4):
    with patch.object(media_proxy.httpx, 'AsyncClient', factory), \
        patch.object(media_proxy, '_MAX_BYTES', limit), \
        patch.object(media_proxy, 'STREAM_CHUNK_BYTES', chunk_size):
      return await media_proxy.proxy_image(url)

  async def test_public_image_is_pinned_and_streamed_to_its_exact_size_limit(self):
    factory = _ClientFactory(lambda req, n: (200, {'content-type': 'image/jpeg'}, [b'1234', b'5678']),
      asynchronous=True)
    response = await self.download(factory, limit=8)
    self.assertEqual(response.body, b'12345678')
    self.assertEqual(response.media_type, 'image/jpeg')
    self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
    self.assertEqual(response.headers['Content-Security-Policy'], "default-src 'none'; sandbox")
    self.assertEqual(factory.requests[0].headers['Host'], 'baon.ru')
    self.assert_closed_and_pinned(factory)

  async def test_relative_and_cross_origin_redirects_are_resolved_and_revalidated(self):
    for location in ('/next', 'https://cdn.baon.ru/next'):
      with self.subTest(location=location):
        self.dns.reset_mock()
        factory = _ClientFactory(lambda req, n: (
          (302, {'location': location, 'set-cookie': 'sid=REPLACE_ME'}, [b'ignored'])
          if n == 0 else (200, {'content-type': 'image/png'}, [b'image'])), asynchronous=True)
        self.assertEqual((await self.download(factory)).body, b'image')
        self.assertEqual(self.dns.call_count, 2)
        self.assertEqual([req.url.path for req in factory.requests], ['/start', '/next'])
        self.assertEqual(factory.requests[1].headers['Host'],
          'cdn.baon.ru' if location.startswith('https') else 'baon.ru')
        self.assertEqual(factory.streams[0].consumed, 0)
        self.assert_closed_and_pinned(factory)

  async def test_relative_redirect_cannot_rebind_the_original_host_to_private_dns(self):
    self.dns.side_effect = [_records(), _records('127.0.0.1')]
    factory = _ClientFactory(lambda req, n: (302, {'location': '/next'}, [b'ignored']), asynchronous=True)
    with self.assertRaises(HTTPException) as caught:
      await self.download(factory)
    self.assertEqual(caught.exception.status_code, 403)
    self.assertEqual(self.dns.call_count, 2)
    self.assertEqual(len(factory.requests), 1)
    self.assert_closed_and_pinned(factory)

  async def test_cross_origin_redirect_to_private_or_disallowed_hosts_is_denied(self):
    for location in ('https://127.0.0.1/a', 'https://other.example/a',
        'http://baon.ru/a', 'file:///etc/passwd'):
      with self.subTest(location=location):
        factory = _ClientFactory(lambda req, n: (302, {'location': location}, [b'ignored']), asynchronous=True)
        with self.assertRaises(HTTPException) as caught:
          await self.download(factory)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(len(factory.requests), 1)
        self.assert_closed_and_pinned(factory)

  async def test_redirect_loop_is_capped_and_missing_location_fails_closed(self):
    for location, count in (('/loop', MAX_REDIRECTS + 1), (None, 1)):
      with self.subTest(location=location):
        factory = _ClientFactory(lambda req, n: (
          302, {'location': location} if location else {}, [b'ignored']), asynchronous=True)
        with self.assertRaises(HTTPException):
          await self.download(factory)
        self.assertEqual(len(factory.requests), count)
        self.assertTrue(all(stream.consumed == 0 for stream in factory.streams))
        self.assert_closed_and_pinned(factory)

  async def test_content_length_over_budget_is_rejected_before_reading_the_body(self):
    factory = _ClientFactory(lambda req, n: (200,
      {'content-type': 'image/jpeg', 'content-length': '9'}, [b'1234', b'56789']), asynchronous=True)
    with self.assertRaises(HTTPException):
      await self.download(factory, limit=8)
    self.assertEqual(factory.streams[0].consumed, 0)
    self.assert_closed_and_pinned(factory)

  async def test_unknown_length_stops_stream_consumption_at_the_budget(self):
    factory = _ClientFactory(lambda req, n: (200, {'content-type': 'image/jpeg'},
      [b'1234', b'5678', b'not-consumed']), asynchronous=True)
    with self.assertRaises(HTTPException):
      await self.download(factory, limit=6)
    self.assertEqual(factory.streams[0].consumed, 2)
    self.assert_closed_and_pinned(factory)

  async def test_gzip_bomb_truncation_and_unknown_encoding_abort_and_close(self):
    cases = [('gzip', gzip.compress(b'x' * 100000)),
      ('gzip', gzip.compress(b'hello')[:-5]), ('br', b'payload')]
    for encoding, body in cases:
      with self.subTest(encoding=encoding, bytes=len(body)):
        factory = _ClientFactory(lambda req, n: (200,
          {'content-type': 'image/jpeg', 'content-encoding': encoding}, [body]), asynchronous=True)
        with self.assertRaises(HTTPException):
          await self.download(factory, limit=256)
        self.assert_closed_and_pinned(factory)

  async def test_valid_gzip_uses_raw_stream_and_is_decoded_exactly_once(self):
    body = gzip.compress(b'valid image bytes')
    factory = _ClientFactory(lambda req, n: (200,
      {'content-type': 'image/jpeg', 'content-encoding': 'gzip'}, [body]), asynchronous=True)
    self.assertEqual((await self.download(factory)).body, b'valid image bytes')
    self.assert_closed_and_pinned(factory)

  async def test_svg_and_html_are_rejected_before_body_consumption(self):
    for content_type in ('image/svg+xml', 'text/html'):
      with self.subTest(content_type=content_type):
        factory = _ClientFactory(lambda req, n: (200,
          {'content-type': content_type}, [b'not-consumed']), asynchronous=True)
        with self.assertRaises(HTTPException) as caught:
          await self.download(factory)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(factory.streams[0].consumed, 0)
        self.assert_closed_and_pinned(factory)

  async def test_total_timeout_cancels_and_closes_a_stalled_stream(self):
    body = _WaitingBody()
    factory = _ClientFactory(lambda req, n: (200, {'content-type': 'image/jpeg'}, body), asynchronous=True)
    with patch.object(media_proxy, '_TOTAL_TIMEOUT_SECONDS', 0.1):
      with self.assertRaises(HTTPException) as caught:
        await self.download(factory)
    self.assertTrue(body.started.is_set())
    self.assertEqual(caught.exception.status_code, 502)
    self.assertIn('Повторите', caught.exception.detail)
    self.assert_closed_and_pinned(factory)

  async def test_caller_cancellation_closes_the_active_response_and_client(self):
    body = _WaitingBody()
    factory = _ClientFactory(lambda req, n: (200, {'content-type': 'image/jpeg'}, body), asynchronous=True)
    task = asyncio.create_task(self.download(factory))
    await asyncio.wait_for(body.started.wait(), 1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assert_closed_and_pinned(factory)


class FeedPublicDownloadTests(_DownloadsCase, unittest.TestCase):
  def setUp(self):
    self.start_dns()

  def download(self, factory, url='https://export.admitad.com/start', *, limit=64,
               chunk_size=4, attempts=1):
    factory.asynchronous = True
    with patch.object(feeds.httpx, 'AsyncClient', factory), \
        patch.object(feeds, '_MAX_FEED_BYTES', limit), \
        patch.object(feeds, 'STREAM_CHUNK_BYTES', chunk_size):
      return feeds._download_feed_body(url, max_attempts=attempts)

  def test_public_feed_is_pinned_and_preserves_declared_text_encoding(self):
    payload = 'название'.encode('cp1251')
    factory = _ClientFactory(lambda req, n: (200,
      {'content-type': 'text/csv; charset=windows-1251'}, [payload]))
    self.assertEqual(self.download(factory, limit=len(payload)), 'название')
    self.assertEqual(factory.requests[0].headers['Host'], 'export.admitad.com')
    self.assert_closed_and_pinned(factory)

  def test_relative_and_cross_origin_redirects_are_resolved_and_revalidated(self):
    for location in ('/next', 'https://feed.merchant.example/next'):
      with self.subTest(location=location):
        self.dns.reset_mock()
        factory = _ClientFactory(lambda req, n: (
          (302, {'location': location, 'set-cookie': 'sid=REPLACE_ME'}, [b'ignored'])
          if n == 0 else (200, {}, [b'csv-content'])))
        self.assertEqual(self.download(factory), 'csv-content')
        self.assertEqual(self.dns.call_count, 2)
        self.assertEqual([req.url.path for req in factory.requests], ['/start', '/next'])
        self.assertEqual(factory.requests[1].headers['Host'],
          'feed.merchant.example' if location.startswith('https') else 'export.admitad.com')
        self.assertEqual(factory.streams[0].consumed, 0)
        self.assert_closed_and_pinned(factory)

  def test_redirect_rebinding_private_target_or_https_downgrade_is_not_requested_or_retried(self):
    for location in ('/next', 'https://private.merchant.example/next', 'http://public.merchant.example/next'):
      with self.subTest(location=location):
        self.dns.side_effect = [_records(), _records('10.0.0.1')]
        factory = _ClientFactory(lambda req, n: (302, {'location': location}, [b'ignored']))
        with patch.object(feeds.asyncio, 'sleep', new_callable=AsyncMock) as sleep, self.assertRaises(FetchDenied):
          self.download(factory, attempts=4)
        self.assertEqual(len(factory.requests), 1)
        sleep.assert_not_called()
        self.assert_closed_and_pinned(factory)
    self.dns.side_effect = None

  def test_unsafe_initial_target_cannot_create_a_client_or_schedule_a_retry(self):
    factory = _ClientFactory(lambda req, n: (200, {}, [b'unexpected']))
    with patch.object(feeds.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
      for url in ('http://127.0.0.1/a', 'file:///etc/passwd',
          'https://REPLACE_ME:REPLACE_ME@merchant.example/feed', 'https://merchant.example:8080/a'):
        with self.subTest(url=url), self.assertRaises(FetchDenied):
          self.download(factory, url=url, attempts=4)
    self.assertEqual(factory.clients, [])
    sleep.assert_not_called()

  def test_redirect_loop_is_capped_and_missing_location_fails_closed(self):
    for location, count in (('/loop', MAX_REDIRECTS + 1), (None, 1)):
      with self.subTest(location=location):
        factory = _ClientFactory(lambda req, n: (302,
          {'location': location} if location else {}, [b'ignored']))
        with self.assertRaises(FetchDenied):
          self.download(factory)
        self.assertEqual(len(factory.requests), count)
        self.assertTrue(all(stream.consumed == 0 for stream in factory.streams))
        self.assert_closed_and_pinned(factory)

  def test_content_length_over_budget_fails_before_body_and_is_not_retried(self):
    factory = _ClientFactory(lambda req, n: (200, {'content-length': '9'}, [b'not-consumed']))
    with patch.object(feeds.asyncio, 'sleep', new_callable=AsyncMock) as sleep, self.assertRaises(FetchDenied):
      self.download(factory, limit=8, attempts=4)
    self.assertEqual(factory.streams[0].consumed, 0)
    self.assertEqual(len(factory.clients), 1)
    sleep.assert_not_called()
    self.assert_closed_and_pinned(factory)

  def test_unknown_length_stops_stream_consumption_at_the_budget(self):
    factory = _ClientFactory(lambda req, n: (200, {}, [b'1234', b'5678', b'not-consumed']))
    with self.assertRaises(FetchDenied):
      self.download(factory, limit=6)
    self.assertEqual(factory.streams[0].consumed, 2)
    self.assert_closed_and_pinned(factory)

  def test_gzip_bomb_truncation_and_unknown_encoding_abort_and_close(self):
    for encoding, payload in [('gzip', gzip.compress(b'x' * 100000)),
        ('gzip', gzip.compress(b'hello')[:-5]), ('gzip, gzip', gzip.compress(b'hello'))]:
      with self.subTest(encoding=encoding, bytes=len(payload)):
        factory = _ClientFactory(lambda req, n: (200, {'content-encoding': encoding}, [payload]))
        with self.assertRaises(FetchDenied):
          self.download(factory, limit=256)
        self.assert_closed_and_pinned(factory)

  def test_valid_gzip_uses_raw_stream_and_is_decoded_exactly_once(self):
    factory = _ClientFactory(lambda req, n: (200,
      {'content-encoding': 'gzip'}, [gzip.compress(b'csv-content')]))
    self.assertEqual(self.download(factory), 'csv-content')
    self.assert_closed_and_pinned(factory)

  def test_transient_stream_error_closes_every_attempt_and_hides_target_in_final_error(self):
    factory = _ClientFactory(lambda req, n: (200, {}, [
      b'part', httpx.ReadTimeout('private-query-sentinel')]))
    with patch.object(feeds.asyncio, 'sleep', new_callable=AsyncMock) as sleep, self.assertRaisesRegex(
        RuntimeError, '^feed_download_unavailable$'):
      self.download(factory, attempts=2)
    self.assertEqual(len(factory.clients), 2)
    sleep.assert_awaited_once()
    self.assert_closed_and_pinned(factory)

  def test_total_feed_deadline_closes_stalled_or_dribbling_stream_without_retry(self):
    for body in (_WaitingBody(), _DribbleBody()):
      with self.subTest(stream=type(body).__name__):
        factory = _ClientFactory(lambda req, n: (200, {}, body))
        with patch.object(feeds, '_FEED_DEADLINE_SECONDS', 0.05), \
            self.assertRaisesRegex(FetchDenied, '^download_deadline$'):
          # Dribble bytes do not fill one iterator chunk or trip a read timeout.
          # The total deadline must still cancel the read and close resources.
          self.download(factory, chunk_size=64 * 1024, attempts=4)
        self.assertTrue(body.started.is_set())
        self.assertLess(body.consumed, 64 * 1024)
        self.assertEqual(len(factory.clients), 1)
        self.assert_closed_and_pinned(factory)

  def test_total_feed_deadline_also_cancels_stalled_response_headers(self):
    factory = _WaitingHeadersFactory()
    with patch.object(feeds, '_FEED_DEADLINE_SECONDS', 0.05), \
        self.assertRaisesRegex(FetchDenied, '^download_deadline$'):
      self.download(factory, attempts=4)
    self.assertTrue(factory.started.is_set())
    self.assertEqual(len(factory.clients), 1)
    self.assertEqual(factory.streams, [])
    self.assert_closed_and_pinned(factory)

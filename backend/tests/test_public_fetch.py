"""Pure public-target and bounded-body contracts, with DNS fully mocked."""
import asyncio
from concurrent.futures import Future
import gzip
import socket
import threading
import traceback
import unittest
from unittest.mock import patch

import httpx

from app.services.public_fetch import (
  BoundedBody, FetchDenied, resolve_public_target, resolve_public_target_async,
)
from app.services import public_fetch


def _records(*addresses):
  return [(socket.AF_INET6 if ':' in address else socket.AF_INET,
    socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
    (address, 443, 0, 0) if ':' in address else (address, 443))
    for address in addresses]


class PublicTargetTests(unittest.TestCase):
  def test_public_dns_is_pinned_with_original_host_sni_and_query(self):
    with patch('app.services.public_fetch.socket.getaddrinfo', return_value=_records(
        '2606:4700:4700::1111', '1.1.1.1', '8.8.8.8')) as dns:
      target = resolve_public_target('https://merchant.example.:443/a%20b?item=one#fragment')
    dns.assert_called_once_with('merchant.example', 443, type=socket.SOCK_STREAM)
    self.assertEqual(target.url, 'https://merchant.example/a%20b?item=one')
    self.assertEqual(target.connect_url, 'https://1.1.1.1/a%20b?item=one')
    self.assertEqual(target.host_header, 'merchant.example')
    self.assertEqual(target.extensions, {'sni_hostname': 'merchant.example'})

  def test_only_web_schemes_no_userinfo_and_default_ports_are_allowed(self):
    denied = ['file:///etc/passwd', 'ftp://merchant.example/a',
      'gopher://merchant.example/a', 'http+unix://merchant.example/a',
      'https://REPLACE_ME:REPLACE_ME@merchant.example/a', 'https://@merchant.example/a',
      'https://merchant.example:8443/a', 'http://merchant.example:443/a',
      'https://merchant.example:0/a', 'https://merchant.example:65536/a',
      'https://merchant.example/a\nInjected: value', 'https://merchant.example/a b',
      'https://merchant.example/' + 'x' * 8192]
    with patch('app.services.public_fetch.socket.getaddrinfo', return_value=_records('1.1.1.1')):
      for url in denied:
        with self.subTest(url=url[:90]), self.assertRaises(FetchDenied):
          resolve_public_target(url)
      self.assertEqual(resolve_public_target('http://merchant.example:80/a').connect_url,
        'http://1.1.1.1/a')

  def test_localhost_and_internal_names_are_rejected_without_dns(self):
    with patch('app.services.public_fetch.socket.getaddrinfo') as dns:
      for host in ('localhost', 'LOCALHOST.', 'cdn.localhost', 'host.local',
          'host.internal', 'single-label', '[fe80::1%25eth0]'):
        with self.subTest(host=host), self.assertRaises(FetchDenied):
          resolve_public_target('https://' + host + '/a')
    dns.assert_not_called()

  def test_nonpublic_ipv4_and_ipv6_literals_never_reach_dns(self):
    addresses = ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.0.1',
      '169.254.169.254', '0.0.0.0', '100.64.0.1', '192.0.2.1',
      '224.0.0.1', '255.255.255.255', '::', '::1', 'fc00::1', 'fe80::1',
      'ff02::1', '::ffff:127.0.0.1', '64:ff9b::7f00:1', '2002:0a00:0001::1')
    with patch('app.services.public_fetch.socket.getaddrinfo') as dns:
      for address in addresses:
        host = '[' + address + ']' if ':' in address else address
        with self.subTest(address=address), self.assertRaises(FetchDenied):
          resolve_public_target('https://' + host + '/a')
    dns.assert_not_called()

  def test_public_literal_targets_use_bracketed_ipv6_host_headers(self):
    with patch('app.services.public_fetch.socket.getaddrinfo') as dns:
      ipv4 = resolve_public_target('https://1.1.1.1/a')
      ipv6 = resolve_public_target('https://[2606:4700:4700::1111]/a')
    dns.assert_not_called()
    self.assertEqual(ipv4.connect_url, 'https://1.1.1.1/a')
    self.assertEqual(ipv6.host_header, '[2606:4700:4700::1111]')
    self.assertEqual(ipv6.extensions['sni_hostname'], '2606:4700:4700::1111')

  def test_idna_origin_uses_ascii_for_dns_host_and_tls_sni(self):
    with patch('app.services.public_fetch.socket.getaddrinfo', return_value=_records('1.1.1.1')) as dns:
      target = resolve_public_target('https://пример.рф/a')
    dns.assert_called_once_with('xn--e1afmkfd.xn--p1ai', 443, type=socket.SOCK_STREAM)
    self.assertEqual(target.hostname, 'xn--e1afmkfd.xn--p1ai')
    self.assertEqual(target.host_header, target.extensions['sni_hostname'])

  def test_any_nonpublic_dns_answer_rejects_the_entire_target(self):
    for addresses in (('1.1.1.1', '10.0.0.1'),
        ('2606:4700:4700::1111', '::1'), ('1.1.1.1', 'fc00::1'), ()):
      with self.subTest(addresses=addresses), patch(
          'app.services.public_fetch.socket.getaddrinfo', return_value=_records(*addresses)):
        with self.assertRaises(FetchDenied):
          resolve_public_target('https://merchant.example/a')

  def test_dns_errors_and_target_repr_do_not_echo_sensitive_url_text(self):
    url = 'https://merchant.example/feed?access=REPLACE_ME'
    with patch('app.services.public_fetch.socket.getaddrinfo', return_value=_records('1.1.1.1')):
      target = resolve_public_target(url)
    self.assertNotIn('REPLACE_ME', repr(target))
    with patch('app.services.public_fetch.socket.getaddrinfo',
        side_effect=socket.gaierror('private-query-sentinel')):
      try:
        resolve_public_target(url)
      except FetchDenied as error:
        self.assertEqual(str(error), 'invalid_download_target')
        self.assertNotIn('private-query-sentinel', ''.join(traceback.format_exception(error)))
      else:
        self.fail('DNS failure must deny the target')


class PublicTargetAsyncTests(unittest.IsolatedAsyncioTestCase):
  async def test_async_resolver_has_a_five_second_deadline_and_fixed_error(self):
    async def timed_out(awaitable, *, timeout):
      self.assertEqual(timeout, 5)
      awaitable.cancel()
      raise TimeoutError('internal resolver deadline')

    pending = Future()
    with patch('app.services.public_fetch._submit_resolution', return_value=pending), \
        patch('app.services.public_fetch.asyncio.wait_for', side_effect=timed_out), \
        patch('app.services.public_fetch.socket.getaddrinfo') as dns:
      with self.assertRaisesRegex(FetchDenied, '^download_dns_timeout$'):
        await resolve_public_target_async('https://merchant.example/a')
    dns.assert_not_called()
    self.assertTrue(pending.cancelled())

  async def test_async_resolver_returns_the_same_pinned_contract(self):
    with patch('app.services.public_fetch.socket.getaddrinfo', return_value=_records('1.1.1.1')):
      target = await asyncio.wait_for(resolve_public_target_async('https://merchant.example/a'), 1)
    self.assertEqual(target.connect_url, 'https://1.1.1.1/a')


class _ControlledPool:
  def __init__(self, *, running=False):
    self.running = running
    self.futures = []

  def submit(self, function, raw):
    future = Future()
    if self.running:
      future.set_running_or_notify_cancel()
    self.futures.append(future)
    return future


class BoundedDnsTests(unittest.TestCase):
  def test_eight_outstanding_resolutions_are_bounded_and_cancelled_queue_releases_one(self):
    pool = _ControlledPool()
    with patch.object(public_fetch, '_DNS_POOL', pool), \
        patch.object(public_fetch, '_DNS_SLOTS', threading.BoundedSemaphore(8)):
      futures = [public_fetch._submit_resolution('https://merchant.example/a') for _ in range(8)]
      with self.assertRaisesRegex(FetchDenied, '^download_dns_busy$'):
        public_fetch._submit_resolution('https://merchant.example/a')
      self.assertTrue(futures[0].cancel())
      public_fetch._submit_resolution('https://merchant.example/a')
      with self.assertRaisesRegex(FetchDenied, '^download_dns_busy$'):
        public_fetch._submit_resolution('https://merchant.example/a')
      for future in pool.futures:
        future.cancel()

  def test_timed_out_running_dns_keeps_its_slot_until_worker_finishes(self):
    pool = _ControlledPool(running=True)
    with patch.object(public_fetch, '_DNS_POOL', pool), \
        patch.object(public_fetch, '_DNS_SLOTS', threading.BoundedSemaphore(1)), \
        patch.object(public_fetch, '_DNS_TIMEOUT_SECONDS', 0.001):
      with self.assertRaisesRegex(FetchDenied, '^download_dns_timeout$'):
        resolve_public_target('https://merchant.example/a')
      self.assertFalse(pool.futures[0].cancelled())
      with self.assertRaisesRegex(FetchDenied, '^download_dns_busy$'):
        public_fetch._submit_resolution('https://merchant.example/a')
      pool.futures[0].set_result(None)
      resumed = public_fetch._submit_resolution('https://merchant.example/a')
      resumed.set_result(None)

  def test_executor_submission_failure_releases_the_reserved_slot(self):
    class ClosedPool:
      def submit(self, function, raw):
        raise RuntimeError('internal-pool-sentinel')

    slots = threading.BoundedSemaphore(1)
    with patch.object(public_fetch, '_DNS_POOL', ClosedPool()), \
        patch.object(public_fetch, '_DNS_SLOTS', slots):
      with self.assertRaisesRegex(FetchDenied, '^download_dns_busy$'):
        public_fetch._submit_resolution('https://merchant.example/a')
      self.assertTrue(slots.acquire(blocking=False))
      slots.release()


class BoundedBodyTests(unittest.TestCase):
  def test_identity_accepts_exact_limit_and_stops_before_retaining_excess(self):
    body = BoundedBody(httpx.Headers(), 8)
    body.add(b'1234')
    body.add(b'5678')
    self.assertEqual(body.finish(), b'12345678')
    with self.assertRaisesRegex(FetchDenied, '^download_too_large$'):
      body.add(b'x')
    self.assertEqual(body.body, b'12345678')

  def test_content_length_oversize_negative_and_malformed_are_rejected_upfront(self):
    for value in ('9', '-1', 'not-a-number', '2, 3'):
      with self.subTest(value=value), self.assertRaises(FetchDenied):
        BoundedBody(httpx.Headers({'content-length': value}), 8)
    self.assertIsInstance(BoundedBody(httpx.Headers({'content-length': '8'}), 8), BoundedBody)

  def test_unknown_or_chained_content_encodings_fail_closed(self):
    for encoding in ('br', 'deflate', 'gzip, gzip', 'gzip, identity'):
      with self.subTest(encoding=encoding), self.assertRaisesRegex(
          FetchDenied, '^unsupported_download_encoding$'):
        BoundedBody(httpx.Headers({'content-encoding': encoding}), 100)

  def test_gzip_stream_decodes_across_chunks_and_requires_complete_encoding(self):
    encoded = gzip.compress(b'hello world')
    body = BoundedBody(httpx.Headers({'content-encoding': 'gzip'}), 100)
    for offset in range(0, len(encoded), 3):
      body.add(encoded[offset:offset+3])
    self.assertEqual(body.finish(), b'hello world')
    truncated = BoundedBody(httpx.Headers({'content-encoding': 'gzip'}), 100)
    truncated.add(encoded[:-5])
    with self.assertRaisesRegex(FetchDenied, '^truncated_download_encoding$'):
      truncated.finish()

  def test_gzip_bomb_is_rejected_at_the_decoded_budget(self):
    encoded = gzip.compress(b'x' * 100000)
    self.assertLess(len(encoded), 256)
    body = BoundedBody(httpx.Headers({'content-encoding': 'gzip'}), 256)
    with self.assertRaisesRegex(FetchDenied, '^download_too_large$'):
      body.add(encoded)
    self.assertLessEqual(len(body.body), 256)

  def test_gzip_encoded_byte_budget_is_independent_of_small_decoded_body(self):
    encoded = gzip.compress(b'a')
    self.assertGreater(len(encoded), 8)
    body = BoundedBody(httpx.Headers({'content-encoding': 'gzip'}), 8)
    with self.assertRaisesRegex(FetchDenied, '^download_too_large$'):
      body.add(encoded)
    self.assertEqual(body.body, b'')

  def test_invalid_trailing_and_concatenated_gzip_fail_closed(self):
    for encoded in (b'not gzip', gzip.compress(b'a') + b'trailing',
        gzip.compress(b'a') + gzip.compress(b'b')):
      with self.subTest(length=len(encoded)), self.assertRaisesRegex(
          FetchDenied, '^invalid_download_encoding$'):
        BoundedBody(httpx.Headers({'content-encoding': 'gzip'}), 100).add(encoded)

  def test_empty_download_is_not_reported_as_a_success(self):
    for encoding in ('identity', 'gzip'):
      body = BoundedBody(httpx.Headers({'content-encoding': encoding}), 100)
      if encoding == 'gzip':
        body.add(gzip.compress(b''))
      with self.assertRaisesRegex(FetchDenied, '^empty_download_body$'):
        body.finish()

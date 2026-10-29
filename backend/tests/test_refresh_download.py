"""Offline transport, freshness and sealing checks for scheduled exports."""
from contextlib import contextmanager, nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json
from email.message import Message
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from app.services.catalog import refresh_download as download


class Response:
    def __init__(self, *, status=200, chunks=(b'id,name\n1,top\n',), headers=None):
        self.status_code, self.chunks = status, chunks
        self.headers = {'content-type': 'text/csv'} if headers is None else headers

    def iter_raw(self, size):
        yield from self.chunks


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        if os.name != 'nt':
            self.root.chmod(0o700)
        self.now = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
        self.responses = [Response() for _ in download.FEEDS]
        self.calls = []
        self.sources = [SimpleNamespace(code=code, feed_url=self.url(feed))
            for code, feed in download.FEEDS.items()]
        self.run = uuid4().hex

    @staticmethod
    def url(feed):
        return 'https://export.admitad.com/ru/webmaster/websites/123/products/export_adv_products/?' + urlencode(
            {'user': 'private-test-user', 'code': 'SYNTHETIC_TEST_ACCESS_CODE', 'feed_id': feed, 'format': 'csv'})

    @contextmanager
    def opener(self, target, timeout):
        self.calls.append((target, timeout))
        yield self.responses[len(self.calls) - 1]

    def run_download(self, **overrides):
        kwargs = dict(run_id=self.run, now_fn=lambda: self.now, monotonic_fn=lambda: 10,
            opener=self.opener, resolver=lambda url: url, deadline_guard=lambda seconds: nullcontext())
        kwargs.update(overrides)
        return download.download_inputs(self.sources, self.root, **kwargs)

    def manifest(self, **overrides):
        result = self.run_download()
        value = json.loads(result.path.read_bytes())
        value.update(overrides)
        return result, value

    def rewrite(self, path, value):
        if os.name != 'nt':
            path.chmod(0o600)
        path.write_text(json.dumps(value), encoding='utf-8')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_exact_four_real_observations_hashes_and_secret_free_immutable_result(self):
        result = self.run_download()
        self.assertEqual(result.run_id, self.run)
        self.assertEqual([feed.code for feed in result.feeds], list(download.FEEDS))
        for feed in result.feeds:
            self.assertEqual(feed.observed_at, self.now)
            self.assertEqual(feed.finished_at, self.now)
            self.assertEqual(feed.sha256, hashlib.sha256(feed.path.read_bytes()).hexdigest())
            self.assertEqual(feed.bytes, feed.path.stat().st_size)
            self.assertFalse(feed.path.with_suffix('.csv.partial').exists())
        with self.assertRaises(FrozenInstanceError):
            result.run_id = 'other'
        for path in self.root.glob('*.json'):
            text = path.read_text()
            for secret in ('private-test-user', 'SYNTHETIC_TEST_ACCESS_CODE', 'export.admitad.com', 'https://'):
                self.assertNotIn(secret, text)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(timeout == 30 for _, timeout in self.calls))

    def test_resume_retains_original_observation_not_current_clock(self):
        result = self.run_download()
        reopened = download.load_downloads(self.root, result.sha256,
            now_fn=lambda: self.now + timedelta(days=1))
        self.assertEqual(reopened.feeds, result.feeds)
        self.assertEqual(len(self.calls), 4)

    def test_exact_72_hours_accepted_then_expired(self):
        result = self.run_download()
        download.load_downloads(self.root, result.sha256, now_fn=lambda: self.now + timedelta(hours=72))
        with self.assertRaisesRegex(download.DownloadRejected, 'expired'):
            download.load_downloads(self.root, result.sha256,
                now_fn=lambda: self.now + timedelta(hours=72, microseconds=1))

    def test_expired_explicit_recovery_retains_facts_and_still_rejects_future(self):
        result = self.run_download()
        recovered = download.load_downloads(self.root, result.sha256,
            now_fn=lambda: self.now + timedelta(days=5), allow_expired_for_recovery=True)
        self.assertEqual(recovered, result)
        with self.assertRaisesRegex(download.DownloadRejected, 'expired_or_invalid'):
            download.load_downloads(self.root, result.sha256,
                now_fn=lambda: self.now - timedelta(seconds=1), allow_expired_for_recovery=True)

    def test_no_overwrite_no_retry_of_same_directory(self):
        self.run_download()
        with self.assertRaisesRegex(download.DownloadRejected, 'new_empty'):
            self.run_download()
        self.assertEqual(len(self.calls), 4)

    def test_fixed_four_unique_sources_required_before_any_http(self):
        self.sources[3] = self.sources[0]
        with self.assertRaisesRegex(download.DownloadRejected, 'exact_four'):
            self.run_download()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unbounded_source_iterable_consumed_only_five_times(self):
        calls = []
        source = SimpleNamespace(code='baon', feed_url=self.url('19982'))
        def many():
            while True:
                calls.append(1)
                yield source
        self.sources = many()
        with self.assertRaisesRegex(download.DownloadRejected, 'exact_four'):
            self.run_download()
        self.assertEqual(len(calls), 5)

    def test_url_admission_rejects_downgrade_redirector_duplicate_query_and_other_feed(self):
        original = self.sources[0].feed_url
        attacks = [original.replace('https:', 'http:'), original.replace('export.admitad.com', '127.0.0.1'),
            original.replace('export.admitad.com', 'export.admitad.com.evil.test'),
            original.replace('export.admitad.com', 'export.admitad.com:443'),
            original.replace('/websites/123/', '/websites/../'), original + '&code=second',
            original.replace('19982', '25461'), original + '#fragment', original + '&redirect=x',
            original.replace('export.admitad.com', 'user@export.admitad.com')]
        for url in attacks:
            with self.subTest(url=url):
                self.sources[0].feed_url = url
                with self.assertRaises(download.DownloadRejected):
                    self.run_download()
        self.assertEqual(self.calls, [])

    def test_redirect_is_never_followed(self):
        self.responses[0] = Response(status=302, headers={'location': 'http://127.0.0.1/'})
        with self.assertRaisesRegex(download.DownloadRejected, 'http_status'):
            self.run_download()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root / 'download-manifest.json').exists())

    def test_bad_headers_rejected_before_body(self):
        bad = [({'content-type': 'text/html'}, 'content_type'),
            ({'content-type': 'text/csv', 'content-length': str(download.MAX_BYTES + 1)}, 'length'),
            ({'content-type': 'text/csv', 'content-length': '-1'}, 'length'),
            ({'content-type': 'text/csv', 'content-length': '0'}, 'length'),
            ({'content-type': 'text/csv', 'content-length': '١'}, 'length'),
            ({'content-type': 'text/csv', 'content-encoding': 'gzip'}, 'encoding'),
            ({'content-type': 'text/csv', 'transfer-encoding': 'unknown'}, 'transfer'),
            ({'content-type': 'text/csv', 'transfer-encoding': 'chunked', 'content-length': '10'}, 'transfer')]
        for headers, message in bad:
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as other:
                self.root = Path(other).resolve()
                if os.name != 'nt': self.root.chmod(0o700)
                self.calls.clear()
                self.responses[0] = Response(headers=headers)
                with self.assertRaisesRegex(download.DownloadRejected, message):
                    self.run_download()
                self.assertFalse((self.root / 'baon.csv').exists())

    def test_empty_and_truncated_downloads_do_not_publish(self):
        self.responses[0] = Response(chunks=(), headers={'content-type': 'text/csv', 'content-length': '5'})
        with self.assertRaisesRegex(download.DownloadRejected, 'empty_or_truncated'):
            self.run_download()
        self.assertFalse((self.root / 'baon.csv').exists())
        self.assertFalse((self.root / 'download-manifest.json').exists())

    def test_exact_content_length_allowed(self):
        self.responses[0].headers['content-length'] = str(len(self.responses[0].chunks[0]))
        self.run_download()

    def test_actual_stream_bytes_bounded_without_content_length(self):
        with patch.object(download, 'MAX_BYTES', 10):
            with self.assertRaisesRegex(download.DownloadRejected, 'byte_bound'):
                self.run_download()
        self.assertFalse((self.root / 'baon.csv').exists())

    def test_deadline_bounds_slow_chunks_and_propagates_guard(self):
        ticks = iter([0, 0, 0, 601])
        with self.assertRaisesRegex(download.DownloadRejected, 'total_deadline'):
            self.run_download(monotonic_fn=lambda: next(ticks))
        self.assertFalse((self.root / 'download-manifest.json').exists())

    def test_real_clock_rollback_rejected(self):
        times = iter([self.now, self.now - timedelta(seconds=1), self.now])
        with self.assertRaisesRegex(download.DownloadRejected, 'observation_time'):
            self.run_download(now_fn=lambda: next(times))

    def test_historical_clock_environment_forbidden_before_http(self):
        with patch.dict(os.environ, {'AUDIT_FIXED_NOW_UTC': '2020-01-01'}):
            with self.assertRaisesRegex(download.DownloadRejected, 'historical'):
                self.run_download()
        self.assertEqual(self.calls, [])

    def test_transport_error_does_not_expose_url_or_credentials(self):
        @contextmanager
        def broken(*_):
            raise OSError(self.sources[0].feed_url)
            yield
        with self.assertRaisesRegex(download.DownloadRejected, '^download_transport_or_storage_failed$') as failure:
            self.run_download(opener=broken)
        self.assertTrue(failure.exception.__suppress_context__)
        self.assertNotIn('private-test', str(failure.exception))

    def test_manifest_hash_and_feed_hash_reject_tampering(self):
        result = self.run_download()
        with self.assertRaisesRegex(download.DownloadRejected, 'manifest_hash'):
            download.load_downloads(self.root, '0' * 64, now_fn=lambda: self.now)
        path = result.feeds[0].path
        if os.name != 'nt': path.chmod(0o600)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(download.DownloadRejected, 'feed_hash'):
            download.load_downloads(self.root, result.sha256, now_fn=lambda: self.now)

    def test_mutated_observation_requires_original_started_hash_and_valid_clock(self):
        result, value = self.manifest()
        value['sources'][0]['observed_at'] = (self.now + timedelta(days=1)).isoformat()
        sha = self.rewrite(result.path, value)
        with self.assertRaisesRegex(download.DownloadRejected, 'observation'):
            download.load_downloads(self.root, sha, now_fn=lambda: self.now)

    def test_extra_untracked_file_and_incomplete_directory_rejected(self):
        with self.assertRaises(download.DownloadRejected):
            download.load_downloads(self.root, '0' * 64, now_fn=lambda: self.now)
        result = self.run_download()
        (self.root / 'baon.csv.partial').write_bytes(b'old')
        with self.assertRaisesRegex(download.DownloadRejected, 'contents'):
            download.load_downloads(self.root, result.sha256, now_fn=lambda: self.now)

    def test_unbounded_or_wrong_kind_chunk_rejected(self):
        self.responses[0] = Response(chunks=(b'x' * (download.CHUNK_BYTES + 1),))
        with self.assertRaisesRegex(download.DownloadRejected, 'chunk_bound'):
            self.run_download()

    def test_default_transport_pins_ip_keeps_verified_host_and_does_not_use_proxy(self):
        target = SimpleNamespace(hostname='export.admitad.com',
            connect_url='https://8.8.8.8/ru/export/?user=private-test-user&code=SYNTHETIC_TEST_ACCESS_CODE')
        headers = Message()
        headers['content-type'] = 'text/csv'
        connection = SimpleNamespace(set_debuglevel=lambda level: self.assertEqual(level, 0),
            request=lambda *a, **k: None, getresponse=lambda: SimpleNamespace(status=200,
                headers=headers, read=lambda size: b'', close=lambda: None),
            close=lambda: None)
        with patch.object(download.http.client, 'HTTPSConnection', return_value=connection) as constructor:
            with download._open_stream(target, 12) as response:
                self.assertEqual(response.status_code, 200)
            self.assertEqual(constructor.call_args.args, ('export.admitad.com',))
            self.assertTrue(constructor.call_args.kwargs['context'].check_hostname)
            with patch.object(download.socket, 'create_connection', return_value='pinned') as connect:
                self.assertEqual(connection._create_connection(('ignored', 443), 12), 'pinned')
                self.assertEqual(connect.call_args.args, (('8.8.8.8', 443),))

    def test_default_transport_rejects_ambiguous_duplicate_length_header(self):
        headers = Message()
        headers['content-type'] = 'text/csv'
        headers['content-length'] = '1'
        headers['content-length'] = '2'
        connection = SimpleNamespace(set_debuglevel=lambda _: None, request=lambda *a, **k: None,
            getresponse=lambda: SimpleNamespace(headers=headers, close=lambda: None), close=lambda: None)
        target = SimpleNamespace(hostname='export.admitad.com', connect_url='https://8.8.8.8/feed')
        with patch.object(download.http.client, 'HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(download.DownloadRejected, 'duplicate_framing'):
                with download._open_stream(target, 12):
                    self.fail('ambiguous HTTP framing admitted')

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership and mode contract')
    def test_group_readable_directory_rejected(self):
        self.root.chmod(0o750)
        with self.assertRaisesRegex(download.DownloadRejected, 'private_download_directory'):
            self.run_download()


if __name__ == '__main__':
    unittest.main()

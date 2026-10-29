"""Fresh, bounded exports for the four scheduled catalogue sources.

Saved access URLs stay in memory. Completed manifests contain observation times
and byte hashes, never provider URLs, user names or access codes. An incomplete
directory cannot be retried: a new HTTP attempt needs a new directory/run ID.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import ipaddress
from itertools import islice
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import threading
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from ..public_fetch import FetchDenied, resolve_public_target

FEEDS = {'baon': '19982', 'shoppinglive': '25461',
    'serginnetti': '26417', 'postmeridiem': '25200'}
MAX_BYTES = 256 * 1024 * 1024
DEADLINE_SECONDS = 600
MAX_AGE = timedelta(hours=72)
CHUNK_BYTES = 65536
_MANIFEST = 'download-manifest.json'
_STARTED = 'download-started.json'
_BASIS = 'real_download_request_start_utc'


class DownloadRejected(ValueError):
    """A fixed reason code; HTTP exception strings can contain credentials."""


def _require(value, reason):
    if not value:
        raise DownloadRejected(reason)


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    _require(isinstance(value, datetime) and value.tzinfo is not None,
        'aware_download_time_required')
    return value.astimezone(timezone.utc)


def _directory(directory):
    path = Path(directory).absolute()
    _require(path.is_dir() and not path.is_symlink() and path.resolve() == path,
        'canonical_download_directory_required')
    if os.name != 'nt':
        _require(path.stat().st_uid == os.geteuid() and path.stat().st_mode & 0o077 == 0,
            'owned_private_download_directory_required')
    return path


def _private_file(path, limit):
    _require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit,
        'bounded_download_file_required')
    if os.name != 'nt':
        _require(path.stat().st_uid == os.geteuid() and path.stat().st_mode & 0o077 == 0,
            'owned_private_download_file_required')


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _seal(path):
    if os.name != 'nt':
        path.chmod(0o400)
    _sync_directory(path.parent)


def _write_json(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    _require(len(raw) <= 32768, 'download_manifest_bound')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _seal(path)


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _run_id(value):
    try:
        _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value)
            and UUID(value).version == 4, 'download_uuid4_required')
    except (ValueError, AttributeError):
        raise DownloadRejected('download_uuid4_required') from None
    return value


def _source_url(source):
    code, raw = getattr(source, 'code', None), getattr(source, 'feed_url', None)
    _require(code in FEEDS and isinstance(raw, str) and 0 < len(raw) <= 8192
        and raw == raw.strip() and not any(ord(c) <= 32 or ord(c) == 127 for c in raw),
        'fixed_export_source_required')
    try:
        parsed = urlsplit(raw)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
            max_num_fields=4)
        _require(parsed.scheme == 'https' and parsed.netloc == 'export.admitad.com'
            and parsed.hostname == 'export.admitad.com' and parsed.port is None
            and parsed.username is None and parsed.password is None and not parsed.fragment
            and re.fullmatch('/ru/webmaster/websites/[0-9]+/products/export_adv_products/', parsed.path)
            and set(query) == {'user', 'code', 'feed_id', 'format'}
            and all(len(values) == 1 for values in query.values())
            and query['feed_id'] == [FEEDS[code]] and query['format'] == ['csv']
            and all(0 < len(query[name][0]) <= 1024 and query[name][0] == query[name][0].strip()
                and not any(ord(c) < 32 or ord(c) == 127 for c in query[name][0])
                for name in ('user', 'code')), 'exact_saved_export_url_required')
    except (ValueError, UnicodeError):
        raise DownloadRejected('exact_saved_export_url_required') from None
    return code, raw


@contextmanager
def _deadline_guard(seconds):
    # A socket read timeout alone does not bound tiny chunks or slow headers.
    _require(os.name == 'posix' and threading.current_thread() is threading.main_thread()
        and hasattr(signal, 'setitimer'), 'linux_main_thread_download_required')
    _require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
        'download_timer_already_in_use')
    old = signal.getsignal(signal.SIGALRM)

    def expired(*_):
        raise DownloadRejected('download_total_deadline')

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


@contextmanager
def _open_stream(target, timeout):
    """Pinned public IP, verified TLS SNI, no proxies, redirects or URL logging.

    http.client is used directly because HTTP client INFO request logs can expose
    an export access code. Its debug output is explicitly disabled.
    """
    parsed = urlsplit(target.connect_url)
    ipaddress.ip_address(parsed.hostname)
    _require(target.hostname == 'export.admitad.com' and parsed.scheme == 'https',
        'pinned_export_target_required')
    connection = http.client.HTTPSConnection('export.admitad.com', port=443,
        timeout=timeout, context=ssl.create_default_context())
    connection.set_debuglevel(0)
    connection._create_connection = lambda _address, timeout, source_address=None: socket.create_connection(
        (parsed.hostname, 443), timeout=timeout, source_address=source_address)
    response = None
    try:
        target_path = parsed.path + ('?' + parsed.query if parsed.query else '')
        connection.request('GET', target_path, headers={'Host': 'export.admitad.com',
            'Accept': 'text/csv', 'Accept-Encoding': 'identity', 'Connection': 'close'})
        response = connection.getresponse()
        for name in ('content-type', 'content-length', 'content-encoding', 'transfer-encoding'):
            _require(len(response.headers.get_all(name, [])) <= 1,
                'download_duplicate_framing_header')

        class Response:
            status_code = response.status
            headers = response.headers

            def iter_raw(self, chunk_size):
                while chunk := response.read(chunk_size):
                    yield chunk

        yield Response()
    finally:
        if response is not None:
            response.close()
        connection.close()


@dataclass(frozen=True)
class DownloadedFeed:
    code: str
    feed_id: str
    filename: str
    path: Path
    bytes: int
    sha256: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True)
class DownloadManifest:
    path: Path
    sha256: str
    run_id: str
    feeds: tuple[DownloadedFeed, ...]


def download_inputs(sources, directory, *, run_id, now_fn=_now,
        monotonic_fn=monotonic, opener=_open_stream, resolver=resolve_public_target,
        deadline_guard=_deadline_guard):
    """Download a new four-source batch, with a 600s total wall-clock limit.

    The optional callables support offline tests. Production callers use their
    defaults. A failure preserves private evidence and never mints a manifest.
    """
    _run_id(run_id)
    _require(not os.getenv('AUDIT_FIXED_NOW_UTC'), 'historical_download_clock_forbidden')
    directory = _directory(directory)
    _require(not any(directory.iterdir()), 'new_empty_download_directory_required')
    values = [_source_url(source) for source in islice(sources, 5)]
    _require(len(values) == 4 and {code for code, _ in values} == set(FEEDS),
        'exact_four_export_sources_required')
    urls = dict(values)
    started = _utc(now_fn())
    _write_json(directory / _STARTED, {'version': 1, 'run_id': run_id,
        'started_at': started.isoformat(), 'clock_basis': _BASIS,
        'max_bytes_per_source': MAX_BYTES, 'total_deadline_seconds': DEADLINE_SECONDS})
    deadline, entries = monotonic_fn() + DEADLINE_SECONDS, []
    try:
        with deadline_guard(DEADLINE_SECONDS):
            for code, feed_id in FEEDS.items():
                remaining = deadline - monotonic_fn()
                _require(remaining > 0, 'download_total_deadline')
                target = resolver(urls[code])
                remaining = deadline - monotonic_fn()
                _require(remaining > 0, 'download_total_deadline')
                partial, complete = directory / (code + '.csv.partial'), directory / (code + '.csv')
                observed = _utc(now_fn())  # Immediately before this actual HTTP attempt.
                size, digest = 0, hashlib.sha256()
                with opener(target, min(30.0, remaining)) as response:
                    _require(response.status_code == 200, 'download_http_status_rejected')
                    headers = response.headers
                    _require(headers.get('content-type', '').split(';')[0].strip().lower() == 'text/csv',
                        'download_csv_content_type_required')
                    _require(headers.get('content-encoding', 'identity').lower().strip() in ('', 'identity'),
                        'download_encoding_rejected')
                    length = headers.get('content-length')
                    _require(length is None or isinstance(length, str) and length.isascii()
                        and length.isdigit() and 0 < int(length) <= MAX_BYTES, 'download_length_rejected')
                    transfer = headers.get('transfer-encoding', '').strip().lower()
                    _require(transfer in ('', 'chunked') and not (transfer and length is not None),
                        'download_transfer_encoding_rejected')
                    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        for chunk in response.iter_raw(CHUNK_BYTES):
                            _require(isinstance(chunk, bytes) and len(chunk) <= CHUNK_BYTES,
                                'download_chunk_bound')
                            _require(monotonic_fn() < deadline, 'download_total_deadline')
                            size += len(chunk)
                            _require(size <= MAX_BYTES, 'download_byte_bound')
                            digest.update(chunk)
                            stream.write(chunk)
                        _require(size > 0 and (length is None or size == int(length)),
                            'download_empty_or_truncated')
                        stream.flush()
                        os.fsync(stream.fileno())
                finished = _utc(now_fn())
                _require(started <= observed <= finished and monotonic_fn() < deadline
                    and finished - started <= timedelta(seconds=DEADLINE_SECONDS),
                    'download_observation_time_rejected')
                os.link(partial, complete)  # Atomic exclusive publication; never overwrite.
                partial.unlink()
                _seal(complete)
                entries.append({'code': code, 'feed_id': feed_id, 'filename': complete.name,
                    'bytes': size, 'sha256': digest.hexdigest(),
                    'observed_at': observed.isoformat(), 'finished_at': finished.isoformat()})
            finished = _utc(now_fn())
            _require(monotonic_fn() < deadline and started <= finished
                and finished - started <= timedelta(seconds=DEADLINE_SECONDS),
                'download_total_deadline')
            report = {'version': 1, 'status': 'download_complete', 'run_id': run_id,
                'clock_basis': _BASIS, 'started_sha256': _sha(directory / _STARTED),
                'started_at': started.isoformat(), 'finished_at': finished.isoformat(),
                'max_bytes_per_source': MAX_BYTES, 'total_deadline_seconds': DEADLINE_SECONDS,
                'sources': entries}
            _write_json(directory / _MANIFEST, report)
            return load_downloads(directory, _sha(directory / _MANIFEST), now_fn=now_fn)
    except DownloadRejected:
        raise
    except (OSError, ValueError, FetchDenied, http.client.HTTPException):
        raise DownloadRejected('download_transport_or_storage_failed') from None
    finally:
        urls.clear()


def load_downloads(directory, expected_manifest_sha256, *, now_fn=_now,
        allow_expired_for_recovery=False):
    """Reopen evidence without replacing its original observation time.

    Only explicit abandonment/recovery may skip the age lower bound. This flag
    never admits a future observation or changes a file, hash or timestamp.
    Publication callers must retain its default.
    """
    _require(type(allow_expired_for_recovery) is bool, 'download_recovery_flag_required')
    directory = _directory(directory)
    _require(isinstance(expected_manifest_sha256, str)
        and re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256), 'download_manifest_sha256_required')
    manifest, started_path = directory / _MANIFEST, directory / _STARTED
    _private_file(manifest, 32768)
    _private_file(started_path, 32768)
    _require(_sha(manifest) == expected_manifest_sha256, 'download_manifest_hash_mismatch')
    try:
        value, started_value = json.loads(manifest.read_bytes()), json.loads(started_path.read_bytes())
        _require(set(value) == {'version', 'status', 'run_id', 'clock_basis', 'started_sha256',
            'started_at', 'finished_at', 'max_bytes_per_source', 'total_deadline_seconds', 'sources'}
            and set(started_value) == {'version', 'run_id', 'started_at', 'clock_basis',
                'max_bytes_per_source', 'total_deadline_seconds'}, 'download_manifest_schema_rejected')
        _run_id(value['run_id'])
        _require(value['status'] == 'download_complete' and value['started_sha256'] == _sha(started_path)
            and all(value[key] == started_value[key] for key in started_value)
            and value['version'] == 1 and value['clock_basis'] == _BASIS
            and value['max_bytes_per_source'] == MAX_BYTES
            and value['total_deadline_seconds'] == DEADLINE_SECONDS,
            'download_manifest_provenance_rejected')
        now, start, finish = _utc(now_fn()), _utc(datetime.fromisoformat(value['started_at'])), _utc(datetime.fromisoformat(value['finished_at']))
        _require((allow_expired_for_recovery or now - MAX_AGE <= start) and start <= finish <= now
            and finish - start <= timedelta(seconds=DEADLINE_SECONDS), 'download_manifest_expired_or_invalid')
        _require(isinstance(value['sources'], list) and len(value['sources']) == 4,
            'download_manifest_sources_rejected')
        feeds = []
        previous_finish = start
        for entry, (code, feed_id) in zip(value['sources'], FEEDS.items()):
            _require(set(entry) == {'code', 'feed_id', 'filename', 'bytes', 'sha256', 'observed_at', 'finished_at'}
                and entry['code'] == code and entry['feed_id'] == feed_id
                and entry['filename'] == code + '.csv'
                and type(entry['bytes']) is int and 0 < entry['bytes'] <= MAX_BYTES
                and isinstance(entry['sha256'], str) and re.fullmatch('[0-9a-f]{64}', entry['sha256']),
                'download_manifest_entry_rejected')
            observed = _utc(datetime.fromisoformat(entry['observed_at']))
            completed = _utc(datetime.fromisoformat(entry['finished_at']))
            _require(previous_finish <= observed <= completed <= finish,
                'download_manifest_observation_rejected')
            previous_finish = completed
            path = directory / entry['filename']
            _private_file(path, MAX_BYTES)
            _require(path.stat().st_size == entry['bytes'] and _sha(path) == entry['sha256'],
                'download_feed_hash_mismatch')
            feeds.append(DownloadedFeed(code, feed_id, path.name, path, entry['bytes'],
                entry['sha256'], observed, completed))
        _require({path.name for path in directory.iterdir()} == {_STARTED, _MANIFEST,
            *(code + '.csv' for code in FEEDS)}, 'download_directory_contents_rejected')
        return DownloadManifest(manifest, expected_manifest_sha256, value['run_id'], tuple(feeds))
    except DownloadRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise DownloadRejected('download_manifest_invalid') from None

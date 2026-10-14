"""Shared network admission and bounded decoding for server-side downloads.

Every hop is resolved once, all answers must be public, and HTTP connects to a
validated literal address. Host + TLS SNI keep the original origin. Callers use a
fresh client for every hop: pools must not reuse one IP's TLS session for a
different hostname. No ambient proxy, cookies or automatic redirects are used.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import ipaddress
import socket
import threading
from urllib.parse import urlsplit
import zlib

import httpx

STREAM_CHUNK_BYTES = 64 * 1024
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 4
_DNS_TIMEOUT_SECONDS = 5
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='public-download-dns')
_DNS_SLOTS = threading.BoundedSemaphore(8)


class FetchDenied(ValueError):
    """A fixed diagnostic, intentionally without URLs/credentials/body text."""


class BodyRejected(FetchDenied):
    """An upstream body failed the bounded decoding contract."""


def _public_address(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped and not _public_address(str(address.ipv4_mapped)):
            return False
        if address.sixtofour and not _public_address(str(address.sixtofour)):
            return False
        if address.teredo and not all(_public_address(str(ip)) for ip in address.teredo):
            return False
        if address in ipaddress.ip_network('64:ff9b::/96'):
            return _public_address(str(ipaddress.IPv4Address(int(address) & 0xffffffff)))
    return True


@dataclass(frozen=True)
class PublicTarget:
    # Query strings may contain partner access codes; never include them in repr.
    url: str = field(repr=False)
    connect_url: str = field(repr=False)
    hostname: str
    host_header: str

    @property
    def extensions(self) -> dict:
        return {'sni_hostname': self.hostname}


def _resolve_public_target(raw: str) -> PublicTarget:
    if not isinstance(raw, str) or len(raw) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in raw):
        raise FetchDenied('invalid_download_url')
    try:
        parsed = urlsplit(raw)
        url = httpx.URL(raw)
        host = url.raw_host.decode('ascii').lower().rstrip('.')
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        if (parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None
                or parsed.password is not None or port != (443 if parsed.scheme == 'https' else 80)
                or '%' in host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal'))):
            raise FetchDenied('invalid_download_url')
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            if '.' not in host:
                raise FetchDenied('nonpublic_download_host')
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            # Prefer IPv4 when both are available, keeping deterministic retries.
            records = sorted(records, key=lambda row: row[0] != socket.AF_INET)
            addresses = list(dict.fromkeys(row[4][0] for row in records))
        if not addresses or not all(_public_address(address) for address in addresses):
            raise FetchDenied('nonpublic_download_address')
        canonical = url.copy_with(host=host, fragment=None)
        return PublicTarget(
            str(canonical), str(canonical.copy_with(host=addresses[0])), host,
            f'[{host}]' if ':' in host else host,
        )
    except (ValueError, httpx.InvalidURL, socket.gaierror, UnicodeError) as exc:
        if isinstance(exc, FetchDenied):
            raise
        raise FetchDenied('invalid_download_target') from None


def _submit_resolution(raw: str):
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise FetchDenied('download_dns_busy')
    try:
        future = _DNS_POOL.submit(_resolve_public_target, raw)
    except RuntimeError:
        slots.release()
        raise FetchDenied('download_dns_busy') from None
    # A timed-out caller cannot cancel a running system DNS call. Keep its slot
    # charged until it actually finishes, so slow DNS cannot create an unbounded
    # queue or an unbounded number of resolver threads.
    future.add_done_callback(lambda _: slots.release())
    return future


def resolve_public_target(raw: str) -> PublicTarget:
    future = _submit_resolution(raw)
    try:
        return future.result(timeout=_DNS_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        raise FetchDenied('download_dns_timeout') from None


async def resolve_public_target_async(raw: str) -> PublicTarget:
    future = _submit_resolution(raw)
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=_DNS_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        raise FetchDenied('download_dns_timeout') from None


class BoundedBody:
    """Limit encoded AND decoded bytes before extending the retained buffer.

    Identity is requested. Gzip remains supported for servers that compress
    anyway; zlib's max_length stops a compressed bomb before large allocation.
    Unknown/chained encodings and truncated gzip fail closed.
    """
    def __init__(self, headers: httpx.Headers, limit: int):
        self.limit = limit
        self.body = bytearray()
        self.received = 0
        length = headers.get('content-length')
        if length is not None:
            try:
                if int(length) < 0 or int(length) > limit:
                    raise BodyRejected('download_too_large')
            except ValueError as exc:
                if isinstance(exc, FetchDenied):
                    raise
                raise BodyRejected('invalid_download_length') from None
        encoding = headers.get('content-encoding', 'identity').lower().strip()
        if encoding not in {'', 'identity', 'gzip'}:
            raise BodyRejected('unsupported_download_encoding')
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None

    def add(self, chunk: bytes) -> None:
        self.received += len(chunk)
        if self.received > self.limit:
            raise BodyRejected('download_too_large')
        remaining = self.limit - len(self.body)
        try:
            decoded = self.decoder.decompress(chunk, remaining + 1) if self.decoder else chunk
        except zlib.error:
            raise BodyRejected('invalid_download_encoding') from None
        if len(decoded) > remaining:
            raise BodyRejected('download_too_large')
        if self.decoder and self.decoder.unused_data:
            raise BodyRejected('invalid_download_encoding')
        self.body.extend(decoded)

    def finish(self) -> bytes:
        if self.decoder and not self.decoder.eof:
            raise BodyRejected('truncated_download_encoding')
        if not self.body:
            raise BodyRejected('empty_download_body')
        return bytes(self.body)

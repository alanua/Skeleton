"""Bounded public HTTPS acquisition. Results are private runtime evidence, not listings."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import ipaddress
import queue
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit


class WebExecutionError(ValueError):
    """Only fixed reason codes cross the adapter boundary."""


@dataclass(frozen=True)
class Limits:
    seconds: float = 10
    requests: int = 6
    bytes: int = 1_000_000
    text_chars: int = 100_000

    def __post_init__(self):
        if (type(self.seconds) not in (int, float) or not 0 < self.seconds <= 30
                or any(type(v) is not int or not 1 <= v <= cap for v, cap in
                       ((self.requests, 16), (self.bytes, 4_000_000), (self.text_chars, 200_000)))):
            raise WebExecutionError('INVALID_LIMITS')


@dataclass(frozen=True)
class Target:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


_DNS_SLOTS = threading.BoundedSemaphore(4)

def _dns(host, port, timeout):
    # libc resolution has no portable deadline; a daemon cannot hold up the caller.
    if not _DNS_SLOTS.acquire(blocking=False):
        raise WebExecutionError('DNS_BUSY')
    results = queue.Queue(maxsize=1)
    def resolve():
        try:
            results.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            results.put(None)
        finally:
            _DNS_SLOTS.release()
    threading.Thread(target=resolve, daemon=True).start()
    try:
        records = results.get(timeout=timeout)
    except queue.Empty:
        raise WebExecutionError('TIMEOUT') from None
    if not records:
        raise WebExecutionError('DNS_FAILED')
    return tuple(record[4][0] for record in records)


def target(url, *, timeout=10, resolver=_dns):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or len(url) > 220 or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
                or '\\' in url or '%' in parsed.netloc):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        addresses = tuple(resolver(host, 443, timeout))
        if not addresses or any((not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast
                                 or ipaddress.ip_address(a).is_reserved) for a in addresses):
            raise WebExecutionError('FORBIDDEN_TARGET')
        canonical = urlunsplit(('https', host, parsed.path or '/', parsed.query, ''))
        return Target(canonical, host, 443, addresses)
    except WebExecutionError:
        raise
    except Exception:
        raise WebExecutionError('INVALID_URL') from None


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, destination, timeout):
        super().__init__(destination.host, timeout=timeout, context=ssl.create_default_context())
        self.destination = destination

    def connect(self):
        # Numeric address is pinned to the validated DNS answer. TLS still verifies hostname.
        started = time.monotonic()
        sock = socket.create_connection((self.destination.addresses[0], 443), self.timeout)
        try:
            remaining = self.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise WebExecutionError('TIMEOUT')
            sock.settimeout(remaining)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
            self._wire_socket = self.sock
        except Exception:
            sock.close()
            raise


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = field(repr=False)


def https_transport(destination, remaining, max_bytes):
    connection = _PinnedHTTPS(destination, remaining)
    deadline = time.monotonic() + remaining
    def interrupt():
        sock = getattr(connection, '_wire_socket', connection.sock)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
    timer = threading.Timer(remaining, interrupt)
    timer.daemon = True
    timer.start()
    try:
        parts = urlsplit(destination.url)
        connection.request('GET', urlunsplit(('', '', parts.path, parts.query, '')),
                           headers={'Accept-Encoding': 'identity', 'User-Agent': 'Skeleton-Web/1'})
        response = connection.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise WebExecutionError('UNSUPPORTED_ENCODING')
        body = bytearray()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise WebExecutionError('TIMEOUT')
            wire = getattr(connection, '_wire_socket', connection.sock)
            if wire:
                wire.settimeout(left)
            chunk = response.read1(min(65536, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise WebExecutionError('CONTENT_TOO_LARGE')
        return Response(response.status, headers, bytes(body))
    except Exception:
        if time.monotonic() >= deadline:
            raise WebExecutionError('TIMEOUT') from None
        raise
    finally:
        timer.cancel()
        connection.close()


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'template'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@dataclass(frozen=True)
class Acquisition:
    source_url: str = field(repr=False)
    final_url: str = field(repr=False)
    checked_at: str
    status: int
    access: str
    method: str = 'http'
    text: str = field(default='', repr=False)
    body: bytes = field(default=b'', repr=False)
    # No lifecycle or stock conclusion is made by generic acquisition.


class Fetcher:
    def __init__(self, *, limits=Limits(), transport=https_transport, resolver=_dns, clock=time.monotonic):
        self.limits, self.transport, self.resolver, self.clock = limits, transport, resolver, clock

    def fetch(self, url):
        deadline = self.clock() + self.limits.seconds
        source, current, total = url, url, 0
        try:
            for _ in range(self.limits.requests):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise WebExecutionError('TIMEOUT')
                destination = target(current, timeout=remaining, resolver=self.resolver)
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise WebExecutionError('TIMEOUT')
                response = self.transport(destination, remaining, self.limits.bytes - total)
                if self.clock() >= deadline:
                    raise WebExecutionError('TIMEOUT')
                total += len(response.body)
                if total > self.limits.bytes:
                    raise WebExecutionError('CONTENT_TOO_LARGE')
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise WebExecutionError('INVALID_REDIRECT')
                    current = urljoin(destination.url, location)
                    continue
                access = {403: 'ACCESS_DENIED', 429: 'RATE_LIMITED', 401: 'LOGIN_REQUIRED'}.get(response.status)
                if access is None:
                    access = 'TRANSIENT_SERVER_ERROR' if response.status >= 500 else 'HTTP_OBSERVED'
                content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                if content_type not in ('text/html', 'text/plain', 'application/xhtml+xml'):
                    raise WebExecutionError('UNSUPPORTED_CONTENT_TYPE')
                decoded = response.body.decode('utf-8', errors='replace')
                parser = TextExtractor()
                parser.feed(decoded)
                text = ' '.join(parser.parts) if content_type != 'text/plain' else decoded
                text = ' '.join(text.split())[:self.limits.text_chars]
                if not text and access == 'HTTP_OBSERVED':
                    access = 'EMPTY_DOCUMENT'
                return Acquisition(source, destination.url,
                                   datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                   response.status, access, text=text, body=response.body)
            raise WebExecutionError('REQUEST_LIMIT')
        except WebExecutionError:
            raise
        except (TimeoutError, socket.timeout):
            raise WebExecutionError('TIMEOUT') from None
        except Exception:
            raise WebExecutionError('FETCH_FAILED') from None

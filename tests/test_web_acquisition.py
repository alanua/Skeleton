"""Synthetic injected transport only: these tests must never resolve or access a network."""
import socket
import pytest

from core.web_acquisition import Fetcher, Limits, Response, WebExecutionError, target


def public(host, port, timeout):
    return ('93.184.216.34',)


def response(body=b'<p>synthetic document</p>', status=200, **headers):
    return Response(status, {'content-type': 'text/html', **headers}, body)


def fetcher(reply, **kwargs):
    return Fetcher(transport=lambda *args: reply, resolver=public, **kwargs)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254',
                                    '::1', 'fc00::1', '::ffff:127.0.0.1', '224.0.0.1', '0.0.0.0'])
def test_ssrf(address):
    with pytest.raises(WebExecutionError, match='FORBIDDEN_TARGET'):
        target('https://fixture.test/ad/123', resolver=lambda *args: (address,))


@pytest.mark.parametrize('url', ['http://fixture.test', 'file:///etc/passwd',
                                'https://user:pass@fixture.test', 'https://fixture.test:444/',
                                'https://fixture.test\\@else.test/', 'https://fixture.test/%20\n'])
def test_invalid_urls(url):
    with pytest.raises(WebExecutionError, match='INVALID_URL'):
        target(url, resolver=public)


def test_mixed_dns_answers_fail_closed():
    with pytest.raises(WebExecutionError, match='FORBIDDEN_TARGET'):
        target('https://fixture.test', resolver=lambda *args: ('93.184.216.34', '10.0.0.1'))


def test_redirect_rechecks_dns_and_never_connects_private():
    calls = []
    answers = iter([('93.184.216.34',), ('127.0.0.1',)])
    def transport(destination, *args):
        calls.append(destination)
        return response(status=302, location='/again')
    f = Fetcher(transport=transport, resolver=lambda *args: next(answers))
    with pytest.raises(WebExecutionError, match='FORBIDDEN_TARGET'):
        f.fetch('https://fixture.test/ad/123')
    assert len(calls) == 1
    assert calls[0].addresses == ('93.184.216.34',)


def test_redirect_limit_and_aggregate_bytes():
    f = fetcher(response(status=302, location='/again'), limits=Limits(requests=2))
    with pytest.raises(WebExecutionError, match='REQUEST_LIMIT'):
        f.fetch('https://fixture.test')
    with pytest.raises(WebExecutionError, match='CONTENT_TOO_LARGE'):
        fetcher(response(b'01234567890'), limits=Limits(bytes=10)).fetch('https://fixture.test')


def test_extract_and_privacy():
    result = fetcher(response(b'<script>secret()</script><p>visible &amp; bounded</p>')).fetch('https://fixture.test/ad/123')
    assert result.text == 'visible & bounded'
    assert result.checked_at.endswith('Z')
    assert 'visible' not in repr(result) and 'fixture.test' not in repr(result)
    assert result.access == 'HTTP_OBSERVED'
    assert not hasattr(result, 'listing_status')


@pytest.mark.parametrize(('status', 'access'), [(403, 'ACCESS_DENIED'), (429, 'RATE_LIMITED'),
                                              (401, 'LOGIN_REQUIRED'), (503, 'TRANSIENT_SERVER_ERROR'),
                                              (404, 'HTTP_OBSERVED')])
def test_http_access_never_implies_listing_lifecycle(status, access):
    assert fetcher(response(status=status)).fetch('https://fixture.test/ad/123').access == access


def test_empty_javascript_shell():
    result = fetcher(response(b'<html><script>render()</script></html>')).fetch('https://fixture.test')
    assert result.access == 'EMPTY_DOCUMENT'


def test_timeout_and_sanitized_error():
    def timeout(*args):
        raise socket.timeout('private URL or token')
    with pytest.raises(WebExecutionError, match='^TIMEOUT$'):
        Fetcher(transport=timeout, resolver=public).fetch('https://fixture.test')
    def failure(*args):
        raise RuntimeError('private URL or token')
    with pytest.raises(WebExecutionError, match='^FETCH_FAILED$'):
        Fetcher(transport=failure, resolver=public).fetch('https://fixture.test')


def test_deadline_across_transport():
    ticks = iter([0, 0, 0, 11])
    with pytest.raises(WebExecutionError, match='TIMEOUT'):
        fetcher(response(), clock=lambda: next(ticks)).fetch('https://fixture.test')


@pytest.mark.parametrize('limits', [dict(seconds=0), dict(seconds=float('nan')), dict(bytes=True),
                                   dict(requests=100), dict(text_chars=0)])
def test_limits_cannot_be_unbounded(limits):
    with pytest.raises(WebExecutionError, match='INVALID_LIMITS'):
        Limits(**limits)


def test_final_identity_preserved_without_verification_claim():
    replies = iter([response(status=302, location='/category'), response()])
    f = Fetcher(transport=lambda *args: next(replies), resolver=public)
    evidence = f.fetch('https://fixture.test/ad/123')
    assert evidence.source_url.endswith('/ad/123')
    assert evidence.final_url.endswith('/category')
    assert evidence.access == 'HTTP_OBSERVED'


def test_pinned_tls_uses_validated_numeric_peer_and_original_hostname(monkeypatch):
    import core.web_acquisition as module
    calls = {}
    class Socket:
        def settimeout(self, value):
            calls['timeout'] = value
        def close(self):
            pass
    class TLS:
        def wrap_socket(self, sock, server_hostname):
            calls['hostname'] = server_hostname
            return sock
    def connect(peer, timeout):
        calls['peer'] = peer
        return Socket()
    monkeypatch.setattr(module.socket, 'create_connection', connect)
    monkeypatch.setattr(module.ssl, 'create_default_context', TLS)
    destination = target('https://fixture.test/ad/123', resolver=public)
    connection = module._PinnedHTTPS(destination, 1)
    connection.connect()
    assert calls['peer'] == ('93.184.216.34', 443)
    assert calls['hostname'] == 'fixture.test'
    assert 0 < calls['timeout'] <= 1


def test_redirect_aggregate_budget_and_nontext_rejected():
    replies = iter([response(b'123456', status=302, location='/again'), response(b'123456')])
    f = Fetcher(transport=lambda *args: next(replies), resolver=public, limits=Limits(bytes=10))
    with pytest.raises(WebExecutionError, match='CONTENT_TOO_LARGE'):
        f.fetch('https://fixture.test')
    with pytest.raises(WebExecutionError, match='UNSUPPORTED_CONTENT_TYPE'):
        fetcher(Response(200, {'content-type': 'application/octet-stream'}, b'fixture')).fetch('https://fixture.test')

"""Offline synthetic WBM fixtures; never contact a device or credential provider."""
import hashlib
import ssl
from email.message import Message
from urllib.parse import parse_qs

import pytest

from integrations import brother_wbm as wbm
from integrations import credential_runtime

PASSWORD = 'synthetic-password&+only'
CERTIFICATE = b'synthetic-leaf-certificate'
TITLE = '<title>Brother MFC-J4540DW</title>'
LOGIN = (TITLE + '<form method="post" action="/home/status.html">'
         '<input type="password" name="Baf9">'
         '<input type="hidden" name="loginurl" value="/general/print_head_cleaning.html">'
         '<input type="submit" id="login"></form>').encode()
AUTH = (TITLE + '<input type="submit" id="logout" value="Logout">').encode()


class Response:
    def __init__(self, status=200, data=b'', headers=()):
        self.status, self.data = status, data
        self.headers = Message()
        for key, value in headers:
            self.headers.add_header(key, value)
        self.read_limits = []

    def getheaders(self):
        return self.headers.items()

    def read(self, limit):
        self.read_limits.append(limit)
        return self.data[:limit]


class Socket:
    def getpeercert(self, binary_form=False):
        assert binary_form
        return CERTIFICATE


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Any unconfigured request fails locally, including accidental regressions.
    def forbidden(*args, **kwargs):
        pytest.fail('unconfigured network or credential provider')
    monkeypatch.setattr(wbm.http.client, 'HTTPSConnection', forbidden)
    monkeypatch.setattr(credential_runtime, 'build_bitwarden_credential_runtime', forbidden)


@pytest.fixture
def transport(monkeypatch):
    instances = []
    responses = []
    events = []

    class Connection:
        def __init__(self, host, port, *, timeout, context):
            self.host, self.port = host, port
            self.timeout, self.context = timeout, context
            self.sock = None
            self.closed = False
            self.requests = []
            self.error = None
            instances.append(self)

        def connect(self):
            events.append('tls')
            self.sock = Socket()

        def request(self, method, path, *, body, headers):
            assert self.sock is not None
            self.requests.append((method, path, body, headers))
            events.append('http')
            if self.error:
                raise self.error

        def getresponse(self):
            assert responses, 'unexpected request (retry or redirect)'
            return responses.pop(0)

        def close(self):
            self.closed = True
            self.sock = None

    monkeypatch.setattr(wbm.http.client, 'HTTPSConnection', Connection)
    return instances, responses, events


def config(**kwargs):
    return wbm.WBMConfig('https://printer.invalid', **kwargs)


def login_responses(responses, *, login=LOGIN, post_status=302, location='/home/status.html', auth=AUTH):
    responses.extend([
        Response(302, headers=[('Location', '/etc/loginerror.html'), ('Set-Cookie', 'session=synthetic; Secure')]),
        Response(data=login),
        Response(post_status, headers=[('Location', location)]),
        Response(data=auth),
    ])


@pytest.mark.parametrize('mode', ['ca', 'pin', 'encrypted_unverified'])
def test_tls_modes_and_successful_bounded_login(transport, mode):
    instances, responses, events = transport
    login_responses(responses)
    options = {'tls_mode': mode, 'timeout_seconds': 2, 'max_response_bytes': 4096}
    if mode == 'pin':
        options['certificate_sha256'] = hashlib.sha256(CERTIFICATE).hexdigest().upper()
    client = wbm.BrotherWBMClient(config(**options))
    result = client.read_authenticated_status(PASSWORD)
    assert result == {'authenticated': True, 'model': 'Brother MFC-J4540DW'}
    assert PASSWORD not in repr(result)
    conn, = instances
    assert events == ['tls', 'http', 'http', 'http', 'http']
    assert (conn.host, conn.port, conn.timeout) == ('printer.invalid', 443, 2)
    assert conn.context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert conn.context.check_hostname is (mode == 'ca')
    assert conn.context.verify_mode == (ssl.CERT_REQUIRED if mode == 'ca' else ssl.CERT_NONE)
    assert [(r[0], r[1]) for r in conn.requests] == [
        ('GET', client.protected_path), ('GET', client.login_error_path),
        ('POST', client.status_path), ('GET', client.status_path)]
    assert parse_qs(conn.requests[2][2].decode()) == {
        'Baf9': [PASSWORD], 'loginurl': [client.protected_path]}
    assert 'Cookie' not in conn.requests[0][3]
    assert conn.requests[1][3]['Cookie'] == 'session=synthetic'
    assert conn.closed and client._connection is None and not client._cookies
    assert not responses


def test_pin_mismatch_before_http_or_credential_resolution(transport, monkeypatch):
    instances, _, events = transport
    calls = []
    monkeypatch.setattr(credential_runtime, 'consume_registered_material_credential',
                        lambda **kwargs: calls.append(kwargs))
    with pytest.raises(wbm.BrotherWBMError, match='^certificate_pin_mismatch$'):
        wbm.read_brother_status_with_registered_credential(
            config=config(tls_mode='pin', certificate_sha256='0' * 64), authority_environment={})
    assert events == ['tls'] and not calls
    assert instances[0].closed and not instances[0].requests


def test_registered_boundary_tls_before_material(transport, monkeypatch):
    instances, responses, events = transport
    login_responses(responses)
    def consume(**kwargs):
        assert events == ['tls']
        assert kwargs['service_id'] == 'brother-mfc-j4540dw'
        assert kwargs['alias'] == 'wbm-admin'
        assert kwargs['action_id'] == 'read-authenticated-status'
        kwargs['consumer'](PASSWORD)
        return {'result': {'status': 'USED'}}
    monkeypatch.setattr(credential_runtime, 'consume_registered_material_credential', consume)
    result = wbm.read_brother_status_with_registered_credential(config=config(), authority_environment={})
    assert result['authenticated'] is True and PASSWORD not in repr(result)
    spec = credential_runtime._registered_material_credential(
        'brother-mfc-j4540dw', 'wbm-admin', 'read-authenticated-status')
    assert isinstance(spec, credential_runtime.RegisteredMaterialCredential)
    assert spec.bootstrap_required and spec.context.machine_identity == 'home-edge-01'
    assert instances[0].closed


@pytest.mark.parametrize('login', [
    LOGIN.replace(b'Baf9', b'other'), LOGIN.replace(b'method="post"', b'method="get"'),
    LOGIN.replace(b'/home/status.html', b'https://other.invalid/home/status.html'),
    LOGIN.replace(b'id="login"', b'id="other"'), LOGIN.replace(b'loginurl', b'other'),
    LOGIN.replace(b'</form>', b''),
    LOGIN.replace(b'<input type="password"', b'</form><input type="password"'),
    LOGIN.replace(b'</form>', b'<input type="hidden" name="loginurl" value="wrong"></form>'),
    LOGIN + b'<form method="post" action="/home/status.html"></form>',
    LOGIN.replace(b'Brother MFC-J4540DW', b'Other model'),
])
def test_login_contract_rejected_before_password_post(transport, login):
    instances, responses, _ = transport
    login_responses(responses, login=login)
    client = wbm.BrotherWBMClient(config())
    with pytest.raises(wbm.BrotherWBMError, match='^unrecognized_login_form$'):
        client.read_authenticated_status(PASSWORD)
    assert len(instances[0].requests) == 2
    assert instances[0].closed and not client._cookies


@pytest.mark.parametrize('status,data', [
    (302, AUTH), (403, AUTH), (200, b''), (200, LOGIN),
    (200, AUTH.replace(b'Brother MFC-J4540DW', b'Other')),
    (200, AUTH.replace(b'logout', b'notlogout').replace(b'Logout', b'Notlogout')),
    (200, AUTH + b'<input type="password">'),
    (200, AUTH + b'<input id="login">'),
    (200, AUTH + b'<form method="post" action="/home/status.html"></form>'),
    (200, AUTH + b'<form>'),
])
def test_authenticated_status_fails_closed(transport, status, data):
    instances, responses, _ = transport
    responses.extend([Response(), Response(status, data)])
    client = wbm.BrotherWBMClient(config())
    with pytest.raises(wbm.BrotherWBMError, match='^authenticated_status_unverified$'):
        client.read_authenticated_status(PASSWORD)
    assert len(instances[0].requests) == 2 and instances[0].closed


@pytest.mark.parametrize('post_status', [200, 302, 303])
def test_post_success_requires_independent_status(transport, post_status):
    instances, responses, _ = transport
    login_responses(responses, post_status=post_status, auth=LOGIN)
    with pytest.raises(wbm.BrotherWBMError, match='^authenticated_status_unverified$'):
        wbm.BrotherWBMClient(config()).read_authenticated_status(PASSWORD)
    assert len(instances[0].requests) == 4


@pytest.mark.parametrize('location', [
    'http://printer.invalid/etc/loginerror.html', 'https://other.invalid/etc/loginerror.html',
    'https://printer.invalid/etc/loginerror.html', '//other.invalid/etc/loginerror.html',
    '/etc/loginerror.html?next=other', '/etc/loginerror.html#fragment',
    '/etc/loginerror.html\\other', '\n/etc/loginerror.html', 'https://[malformed', '',
])
@pytest.mark.parametrize('stage', ['login', 'post'])
def test_redirects_are_fixed_same_origin_and_never_followed(transport, location, stage):
    instances, responses, _ = transport
    if stage == 'login':
        responses.append(Response(302, headers=[('Location', location)]))
    else:
        login_responses(responses, location=location)
    client = wbm.BrotherWBMClient(config())
    with pytest.raises(wbm.BrotherWBMError, match=f'^unexpected_{stage}_redirect$'):
        client.read_authenticated_status(PASSWORD)
    assert len(instances[0].requests) == (1 if stage == 'login' else 3)
    assert instances[0].closed and not client._cookies


@pytest.mark.parametrize('failure', ['request', 'oversize', 'tls'])
def test_redacted_errors_no_retries_and_cleanup(transport, monkeypatch, failure):
    instances, responses, _ = transport
    client = wbm.BrotherWBMClient(config(max_response_bytes=32))
    client.prepare_tls()
    conn = instances[0]
    client._cookies['session'] = 'synthetic'
    if failure == 'request':
        conn.error = OSError(PASSWORD)
        code = 'https_request_failed'
    elif failure == 'oversize':
        response = Response(data=b'x' * 33)
        responses.append(response)
        code = 'response_too_large'
    else:
        client.close()
        def bad_connect(self):
            raise ssl.SSLError(PASSWORD)
        monkeypatch.setattr(type(conn), 'connect', bad_connect)
        code = 'tls_connection_failed'
    with pytest.raises(wbm.BrotherWBMError) as error:
        client.read_authenticated_status(PASSWORD)
    assert str(error.value) == code and PASSWORD not in str(error.value)
    assert error.value.__suppress_context__ or failure == 'oversize'
    assert client._connection is None and not client._cookies
    assert all(c.closed for c in instances)
    assert sum(len(c.requests) for c in instances) <= 1
    if failure == 'oversize':
        assert response.read_limits == [33]


@pytest.mark.parametrize('url', [
    'http://printer.invalid', 'https://user:synthetic@printer.invalid',
    'https://printer.invalid/path', 'https://printer.invalid?query',
    'https://printer.invalid#fragment', 'https://printer.invalid:bad',
    'https://printer.invalid:0', 'https://[broken', 'https://printer.invalid\n',
])
def test_invalid_origin_is_redacted(url):
    with pytest.raises(wbm.BrotherWBMError, match='^invalid_https_origin$'):
        wbm.WBMConfig(url)


@pytest.mark.parametrize('kwargs', [
    {'tls_mode': 'http'}, {'tls_mode': 'pin'},
    {'tls_mode': 'pin', 'certificate_sha256': 'bad'},
    {'certificate_sha256': '0' * 64}, {'timeout_seconds': 0},
    {'timeout_seconds': 31}, {'max_response_bytes': 0}, {'max_response_bytes': 8_388_609},
])
def test_invalid_config(kwargs):
    with pytest.raises(wbm.BrotherWBMError):
        config(**kwargs)


@pytest.mark.parametrize('receipt', [
    {}, {'result': {'status': 'DENIED'}}, {'result': {'status': 'USED'}},
])
def test_broker_failure_or_missing_consumption_fails_closed(transport, monkeypatch, receipt):
    instances, _, _ = transport
    monkeypatch.setattr(credential_runtime, 'consume_registered_material_credential',
                        lambda **kwargs: receipt)
    with pytest.raises(wbm.BrotherWBMError, match='^credential_broker_use_failed$'):
        wbm.read_brother_status_with_registered_credential(config=config(), authority_environment={})
    assert instances[0].closed and not instances[0].requests


def test_reconnected_tls_pin_checked_before_sending_material(transport, monkeypatch):
    instances, responses, _ = transport
    client = wbm.BrotherWBMClient(config(
        tls_mode='pin', certificate_sha256=hashlib.sha256(CERTIFICATE).hexdigest()))
    client.prepare_tls()
    # Simulate a server closing the TLS socket before the next request.
    instances[0].close()
    monkeypatch.setattr(Socket, 'getpeercert', lambda *args, **kwargs: b'different-synthetic-leaf')
    with pytest.raises(wbm.BrotherWBMError, match='^certificate_pin_mismatch$'):
        client.read_authenticated_status(PASSWORD)
    assert len(instances) == 2
    assert all(c.closed and not c.requests for c in instances)
    assert not client._cookies


def test_existing_authenticated_session_requires_status_check(transport):
    instances, responses, _ = transport
    responses.extend([Response(), Response(data=AUTH)])
    client = wbm.BrotherWBMClient(config())
    assert client.read_authenticated_status(PASSWORD)['authenticated'] is True
    assert [r[0] for r in instances[0].requests] == ['GET', 'GET']
    assert instances[0].closed


@pytest.mark.parametrize('path', ['//other.invalid/', 'https://other.invalid/', '/bad\\path', '/bad\r\nheader'])
def test_unsafe_request_path_rejected_before_transport(path):
    client = wbm.BrotherWBMClient(config())
    with pytest.raises(wbm.BrotherWBMError, match='^invalid_same_origin_path$'):
        client._request('GET', path)


def test_cookie_parse_failure_is_redacted_and_cleared(transport, monkeypatch):
    instances, responses, _ = transport
    responses.append(Response(headers=[('Set-Cookie', 'session=synthetic')]))
    def bad_cookie(self, raw):
        raise wbm.CookieError(PASSWORD)
    monkeypatch.setattr(wbm.SimpleCookie, 'load', bad_cookie)
    client = wbm.BrotherWBMClient(config())
    with pytest.raises(wbm.BrotherWBMError, match='^https_request_failed$'):
        client.read_authenticated_status(PASSWORD)
    assert instances[0].closed and not client._cookies


def test_broker_exception_closes_prepared_tls(transport, monkeypatch):
    instances, _, _ = transport
    def fail(**kwargs):
        raise credential_runtime.RegisteredCredentialRuntimeError('registered_credential_resolution_failed')
    monkeypatch.setattr(credential_runtime, 'consume_registered_material_credential', fail)
    with pytest.raises(credential_runtime.RegisteredCredentialRuntimeError):
        wbm.read_brother_status_with_registered_credential(config=config(), authority_environment={})
    assert instances[0].closed and not instances[0].requests

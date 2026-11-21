import hashlib
import json
import ssl
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.secret_store import ResolvedSecret, SecretOutOfScope
from integrations import bitwarden_credential_runtime, bitwarden_secret_store, brother_wbm, credential_runtime


SECRET = "synthetic-brother-password"
PEER = b"synthetic-enrolled-certificate"
EVIDENCE = brother_wbm.StatusEvidence(
    "https://printer.example/status", 200, "MFC-J4540DW", True, False
)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("Brother tests must never open a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class Session:
    closed = False
    logins = 0

    def __init__(self, evidence=EVIDENCE, failure=None):
        self.evidence = evidence
        self.failure = failure

    def connect(self, origin, context):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert origin == "https://printer.example"
        if self.failure:
            raise self.failure
        return PEER

    def login_and_read_status(self, password):
        self.logins += 1
        assert password == SECRET
        return self.evidence

    def close(self):
        self.closed = True


@pytest.fixture
def authority(tmp_path, monkeypatch):
    (tmp_path / "skeleton-secret-reference-index").write_text(json.dumps({
        "schema": "skeleton.secret_reference_index.v1",
        "registrations": [{"service_id": "brother-mfc-j4540dw", "alias": "wbm-admin",
                           "provider": "bitwarden", "reference_id": "synthetic-existing-ref"}],
    }))
    class Store:
        provider = "bitwarden"
        calls = []
        failure = None

        def resolve(self, reference, context):
            self.calls.append((reference, context))
            if self.failure:
                raise self.failure
            return ResolvedSecret(SECRET)

    store = Store()
    monkeypatch.setattr(bitwarden_credential_runtime.BwsCliSecretsManagerStore,
                        "from_systemd_credentials", classmethod(lambda cls, env: store))
    # Use the real default verifying context offline; no network or CA file needed.
    monkeypatch.setattr(brother_wbm.BrotherTrustBinding, "tls_context",
                        lambda self: ssl.create_default_context())
    return {"CREDENTIALS_DIRECTORY": str(tmp_path)}, store


def run(authority, session, fingerprint=None):
    env, _ = authority
    trust = brother_wbm.BrotherTrustBinding("printer.example", fingerprint or hashlib.sha256(PEER).hexdigest(),
                                            "/operator-enrolled-ca.pem")
    result = brother_wbm._read_authenticated_status(trust=trust, session=session,
                                                   authority_environment=env)
    assert session.closed
    assert SECRET not in json.dumps(result)
    return result


def test_status_uses_registered_index_and_broker(authority):
    session = Session()
    assert run(authority, session)["status"] == "OK"
    reference, context = authority[1].calls[0]
    assert reference.reference_id == "synthetic-existing-ref"
    assert context.machine_identity == "home-edge-01"
    assert context.audience == "brother-mfc-j4540dw-wbm"
    assert context.task_kind == "bounded_maintenance_status"
    assert session.logins == 1


def test_status_through_existing_bws_store_offline(authority, monkeypatch):
    store_type = bitwarden_secret_store.BwsCliSecretsManagerStore
    store = store_type("/trusted/bws", "synthetic-authority-token", "/trusted/home")
    factories = []
    commands = []

    def from_credentials(cls, environment):
        factories.append(environment)
        return store

    def fake_run(argv, **kwargs):
        commands.append((argv, kwargs))
        assert argv == ["/trusted/bws", "secret", "get", "synthetic-existing-ref", "--output", "json"]
        assert SECRET not in json.dumps(argv) + json.dumps(kwargs["env"])
        assert "input" not in kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "id": "synthetic-existing-ref", "value": SECRET,
        }))

    monkeypatch.setattr(store_type, "from_systemd_credentials", classmethod(from_credentials))
    monkeypatch.setattr(bitwarden_secret_store.subprocess, "run", fake_run)
    assert run(authority, Session())["status"] == "OK"
    assert factories == [authority[0]]
    assert len(commands) == 1


def test_only_one_brother_status_binding_is_registered():
    assert [item for item in credential_runtime.registered_credential_capabilities()
            if item["service_id"] == "brother-mfc-j4540dw"] == [{
        "service_id": "brother-mfc-j4540dw", "alias": "wbm-admin",
        "action_id": "read-authenticated-status", "delivery": "registered_in_process",
    }]


@pytest.mark.parametrize("evidence", [
    replace(EVIDENCE, http_status=302),
    replace(EVIDENCE, redirected=True),
    replace(EVIDENCE, url="https://other.example/status"),
    replace(EVIDENCE, url="http://printer.example/status"),
    replace(EVIDENCE, login_form_present=True),
    replace(EVIDENCE, logout_control_present=False),
    replace(EVIDENCE, model="unknown"),
    replace(EVIDENCE, url="https://user:password@printer.example/status"),
    replace(EVIDENCE, url="https://printer.example:8443/status"),
    replace(EVIDENCE, http_status=401),
    {"http_status": 200},
])
def test_redirects_and_login_fallthrough_are_blocked(authority, evidence):
    assert run(authority, Session(evidence))["status"] == "BLOCKED"


def test_tls_failure_precedes_resolution(authority):
    session = Session(failure=ssl.SSLError(SECRET))
    assert run(authority, session)["status"] == "BLOCKED"
    assert not authority[1].calls
    assert session.logins == 0


def test_changed_certificate_precedes_resolution(authority):
    session = Session()
    assert run(authority, session, "0" * 64)["status"] == "BLOCKED"
    assert not authority[1].calls
    assert session.logins == 0


def test_out_of_scope_provider_denial_is_redacted(authority):
    authority[1].failure = SecretOutOfScope(SECRET)
    session = Session()
    assert run(authority, session)["status"] == "BLOCKED"
    assert session.logins == 0


def test_missing_reference_index_never_falls_back(authority, tmp_path):
    (tmp_path / "skeleton-secret-reference-index").unlink()
    session = Session()
    assert run(authority, session)["status"] == "BLOCKED"
    assert not authority[1].calls


def test_no_caller_selected_reference_or_action(authority):
    with pytest.raises(credential_runtime.RegisteredCredentialRuntimeError):
        credential_runtime.consume_registered_material_credential(
            service_id="brother-mfc-j4540dw", alias="wbm-admin", action_id="clean",
            consumer=lambda value: pytest.fail("must not resolve"), authority_environment=authority[0])
    assert not authority[1].calls


def test_trust_requires_explicit_valid_enrollment():
    for host, pin, ca in [("printer.example", "", "/ca.pem"),
                          ("printer.example/path", "0" * 64, "/ca.pem"),
                          ("printer.example", "0" * 64, "")]:
        with pytest.raises(ValueError, match="brother_trust_binding_invalid"):
            brother_wbm.BrotherTrustBinding(host, pin, ca).tls_context()


@pytest.mark.parametrize("host", [
    "printer.example\n", "printer.example:443", "user@printer.example",
    "printer.example?", "printer.example#", "https://printer.example",
    "printer..example", "-printer.example", "Printer.example", "",
])
def test_trust_rejects_noncanonical_hostnames(host):
    with pytest.raises(ValueError, match="brother_trust_binding_invalid"):
        brother_wbm.BrotherTrustBinding(host, "0" * 64, "/ca.pem").tls_context()


def test_trust_context_enforces_chain_hostname_and_tls_minimum(monkeypatch):
    context = ssl.create_default_context()
    calls = []

    def create_context(*, cafile):
        calls.append(cafile)
        return context

    monkeypatch.setattr(brother_wbm.ssl, "create_default_context", create_context)
    trust = brother_wbm.BrotherTrustBinding("printer.example", "0" * 64, "/enrolled.pem")
    assert trust.tls_context() is context
    assert calls == ["/enrolled.pem"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_disabled_tls_verification_blocks_before_connection(authority, monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    monkeypatch.setattr(brother_wbm.BrotherTrustBinding, "tls_context", lambda self: context)
    session = Session()
    assert run(authority, session)["status"] == "BLOCKED"
    assert not authority[1].calls
    assert session.logins == 0


@pytest.mark.parametrize("peer", [None, b"", "not DER bytes"])
def test_unknown_certificate_blocks_before_resolution(authority, peer):
    class UnknownPeer(Session):
        def connect(self, origin, context):
            return peer

    session = UnknownPeer()
    assert run(authority, session)["status"] == "BLOCKED"
    assert not authority[1].calls
    assert session.logins == 0


@pytest.mark.parametrize("field,value", [
    ("machine_identity", "other-node"),
    ("audience", "other-service"),
    ("task_kind", "cleaning"),
])
def test_broker_gate_rejects_context_mismatch_before_provider(authority, monkeypatch, field, value):
    build = credential_runtime.build_bitwarden_credential_runtime

    def mismatched_runtime(**kwargs):
        registration, = kwargs["registrations"]
        kwargs["registrations"] = (replace(
            registration, context=replace(registration.context, **{field: value})),)
        return build(**kwargs)

    monkeypatch.setattr(credential_runtime, "build_bitwarden_credential_runtime", mismatched_runtime)
    session = Session()
    assert run(authority, session)["status"] == "BLOCKED"
    assert not authority[1].calls
    assert session.logins == 0


def test_missing_index_ignores_legacy_reference_file(authority, tmp_path):
    (tmp_path / "skeleton-secret-reference-index").unlink()
    (tmp_path / "brother-wbm-secret-ref").write_text("must-not-use-legacy-reference")
    session = Session()
    assert run(authority, session)["status"] == "BLOCKED"
    assert not authority[1].calls


def test_receipts_and_output_do_not_contain_material(authority, monkeypatch, capsys, caplog):
    consume = brother_wbm.consume_registered_material_credential
    receipts = []

    def record_receipt(**kwargs):
        receipt = consume(**kwargs)
        receipts.append(receipt)
        return receipt

    monkeypatch.setattr(brother_wbm, "consume_registered_material_credential", record_receipt)
    assert run(authority, Session())["status"] == "OK"
    assert receipts[0]["result"]["status"] == "USED"
    captured = capsys.readouterr()
    assert SECRET not in json.dumps(receipts) + captured.out + captured.err + caplog.text


def test_browser_exception_is_redacted_and_session_closed(authority):
    class FailingSession(Session):
        def login_and_read_status(self, password):
            raise RuntimeError(password)

    assert run(authority, FailingSession())["status"] == "BLOCKED"


def test_close_failure_blocks_success(authority):
    class FailingClose(Session):
        def close(self):
            self.closed = True
            raise RuntimeError(SECRET)

    assert run(authority, FailingClose())["status"] == "BLOCKED"

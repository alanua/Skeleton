import pytest
from core.home_edge.device_form_session import (
    DeviceSessionError, FormSessionProfile, acquire_device_session,
)
from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext

REF = SecretReference(provider="bitwarden", reference_id="5acff9bc-1292-42a5-b961-b4ee00cd824c")
CTX = SecretResolutionContext(machine_identity="home-edge-01", audience="brother_printer", task_kind="print_head_clean_black_normal")


def profile(**kw):
    values = dict(
        device_id="brother_printer", ip_address="192.168.1.165",
        login_path="/home/status.html",
        protected_path="/general/print_head_cleaning.html",
        password_field="Baf9",
        hidden_fields=(("loginurl", "/home/status.html"),),
    )
    values.update(kw)
    return FormSessionProfile(**values)


class Vault:
    def resolve(self, reference, context):
        assert reference == REF and context == CTX
        return ResolvedSecret("opaque-password")


class Response:
    status = 200
    def __init__(self, content): self.content = content
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.content[:size]


class Opener:
    def __init__(self, protected=b"<html>Print Head Cleaning</html>"):
        self.protected = protected
        self.requests = []
    def open(self, req, timeout):
        self.requests.append(req)
        if len(self.requests) == 1:
            return Response(b"ok")
        return Response(self.protected)


def test_brother_form_login_then_protected_read():
    opener = Opener()
    assert acquire_device_session(profile(), secret_ref=REF, secrets=Vault(), context=CTX, opener=opener)
    assert opener.requests[0].full_url == "https://192.168.1.165/home/status.html"
    assert b"Baf9=opaque-password" in opener.requests[0].data
    assert opener.requests[1].full_url.endswith("/general/print_head_cleaning.html")


def test_login_page_returned_is_not_success():
    opener = Opener(protected=b"<h2>Please&#32;Login</h2>")
    assert not acquire_device_session(profile(), secret_ref=REF, secrets=Vault(), context=CTX, opener=opener)


@pytest.mark.parametrize("field,value", [
    ("ip_address", "8.8.8.8"),
    ("login_path", "//outside.example/path"),
    ("protected_path", "https://outside.example/"),
    ("password_field", "bad field"),
])
def test_invalid_profiles_fail_closed(field, value):
    with pytest.raises(DeviceSessionError):
        acquire_device_session(profile(**{field:value}), secret_ref=REF, secrets=Vault(), context=CTX, opener=Opener())


def test_scope_mismatch_fails_before_secret_lookup():
    class BadVault:
        def resolve(self, *a): raise AssertionError("should never resolve")
    badctx = SecretResolutionContext(machine_identity="home-edge-01", audience="another_device", task_kind="clean")
    with pytest.raises(DeviceSessionError, match="out_of_scope"):
        acquire_device_session(profile(), secret_ref=REF, secrets=BadVault(), context=badctx, opener=Opener())


def test_transport_exception_is_redacted():
    class BadOpener:
        def open(self, *args, **kwargs):
            raise RuntimeError("internal-secret-data")
    with pytest.raises(DeviceSessionError) as exc:
        acquire_device_session(profile(), secret_ref=REF, secrets=Vault(), context=CTX, opener=BadOpener())
    assert "internal-secret-data" not in str(exc.value)

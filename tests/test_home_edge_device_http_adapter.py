from io import BytesIO
import pytest

from core.home_edge.device_http_adapter import (
    DeviceHttpError, DeviceHttpOperation, perform_device_http,
)
from core.secret_store import (
    ResolvedSecret, SecretReference, SecretResolutionContext,
)

CTX = SecretResolutionContext(
    machine_identity="home-edge-01",
    audience="brother_printer",
    task_kind="print_head_clean_black_normal",
)
REF = SecretReference(
    provider="bitwarden",
    reference_id="5acff9bc-1292-42a5-b961-b4ee00cd824c",
)


class FakeVault:
    def resolve(self, reference, context):
        assert reference == REF
        assert context == CTX
        return ResolvedSecret("private-password")


class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, count): return b"ok"


class FakeOpener:
    def __init__(self): self.requests = []
    def open(self, request, timeout=0):
        self.requests.append((request, timeout))
        return FakeResponse()


def test_successful_bounded_scoped_operation():
    opener = FakeOpener()
    result = perform_device_http(
        DeviceHttpOperation("brother_printer", "192.168.1.165",
                            "/home/status.html", "GET", auth="basic", secret_ref=REF),
        secrets=FakeVault(), context=CTX, opener=opener,
    )
    assert result.accepted
    assert result.response_size == 2
    assert opener.requests[0][0].full_url == "http://192.168.1.165/home/status.html"
    assert "private-password" not in repr(result)


@pytest.mark.parametrize("ip,path", [
    ("127.0.0.1", "/"),
    ("8.8.8.8", "/"),
    ("169.254.1.4", "/"),
    ("192.168.1.165", "//attacker.example/"),
    ("192.168.1.165", "http://attacker.example/"),
])
def test_reject_unsafe_destinations(ip,path):
    with pytest.raises(DeviceHttpError):
        perform_device_http(
            DeviceHttpOperation("brother_printer",ip,path,"GET"),
            secrets=FakeVault(), context=CTX, opener=FakeOpener(),
        )


def test_reject_unauthorized_secret_context():
    with pytest.raises(DeviceHttpError, match="out_of_scope"):
        perform_device_http(
            DeviceHttpOperation("brother_printer","192.168.1.165","/","GET",auth="basic",secret_ref=REF),
            secrets=FakeVault(),
            context=SecretResolutionContext("home-edge-01","another_device","test"),
            opener=FakeOpener(),
        )


def test_transport_exception_redacted():
    class BadOpener:
        def open(self,*args,**kw):
            raise RuntimeError("secret-and-internal-http-details")
    with pytest.raises(DeviceHttpError) as exc:
        perform_device_http(
            DeviceHttpOperation("brother_printer","192.168.1.165","/","GET"),
            secrets=FakeVault(),context=CTX,opener=BadOpener(),
        )
    assert "secret-and-internal-http-details" not in str(exc.value)

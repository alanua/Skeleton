from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sys
import types

import pytest

from integrations.openhands_subscription_credentials import (
    OPENAI_SERVICE,
    OPENAI_SUBSCRIPTION_VENDOR,
    OpenHandsCredentialStoreError,
    OpenHandsSubscriptionCredentialStore,
    SubscriptionCredentialRecord,
)


@dataclass(repr=False)
class FakeOAuthCredentials:
    vendor: str
    access_token: str
    refresh_token: str
    expires_at: int
    type: str = "oauth"

    def __repr__(self) -> str:
        return "FakeOAuthCredentials(<redacted>)"


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("openhands.sdk.llm.auth.credentials")
    module.OAuthCredentials = FakeOAuthCredentials
    monkeypatch.setitem(sys.modules, "openhands.sdk.llm.auth.credentials", module)


class MemoryTransport:
    def __init__(self) -> None:
        self.record: SubscriptionCredentialRecord | None = None
        self.fail: str | None = None
        self.calls: list[object] = []

    def load(self, *, service: str, vendor: str) -> SubscriptionCredentialRecord | None:
        self.calls.append(("load", service, vendor))
        if self.fail == "load":
            raise PermissionError("locked: secret-token")
        return self.record

    def store_atomic(self, record: SubscriptionCredentialRecord) -> None:
        self.calls.append("store_atomic")
        if self.fail == "store":
            raise RuntimeError("denied: secret-token")
        self.record = replace(record, revision="r1")

    def delete(self, *, service: str, vendor: str) -> bool:
        self.calls.append(("delete", service, vendor))
        if self.fail == "delete":
            raise RuntimeError("denied: secret-token")
        existed = self.record is not None
        self.record = None
        return existed

    def rotate_atomic(
        self, record: SubscriptionCredentialRecord, *, expected_revision: str
    ) -> bool:
        self.calls.append(("rotate_atomic", expected_revision))
        if self.fail == "rotate":
            return False
        if self.fail == "rotate_exception":
            raise RuntimeError("failed with new-access")
        if self.record is None or self.record.revision != expected_revision:
            return False
        self.record = replace(record, revision="r2")
        return True


def store(transport: MemoryTransport) -> OpenHandsSubscriptionCredentialStore:
    return OpenHandsSubscriptionCredentialStore(transport, clock=lambda: 1_000.25)


def credential(access: str = "access", refresh: str = "refresh", expires: int = 2_000_000) -> FakeOAuthCredentials:
    return FakeOAuthCredentials("openai", access, refresh, expires)


def test_true_sdk_call_conventions_and_millisecond_values() -> None:
    transport = MemoryTransport()
    adapter = store(transport)
    adapter.save(credential())
    assert transport.calls == ["store_atomic"]
    loaded = adapter.get("openai")
    assert isinstance(loaded, FakeOAuthCredentials)
    assert (loaded.vendor, loaded.access_token, loaded.refresh_token, loaded.expires_at) == (
        "openai", "access", "refresh", 2_000_000
    )
    assert transport.calls[-1] == ("load", OPENAI_SERVICE, OPENAI_SUBSCRIPTION_VENDOR)
    updated = adapter.update_tokens("openai", "new-access", None, 60)
    assert isinstance(updated, FakeOAuthCredentials)
    assert (updated.vendor, updated.access_token, updated.refresh_token, updated.expires_at) == (
        "openai", "new-access", "refresh", 1_060_250
    )
    assert transport.record == SubscriptionCredentialRecord(
        OPENAI_SERVICE, "openai", "new-access", "refresh", 1_060_250, "r2"
    )
    assert transport.calls[-2:] == [
        ("load", OPENAI_SERVICE, OPENAI_SUBSCRIPTION_VENDOR),
        ("rotate_atomic", "r1"),
    ]
    assert adapter.delete("openai") is True
    assert adapter.delete("openai") is False
    assert transport.calls[-2:] == [
        ("delete", OPENAI_SERVICE, OPENAI_SUBSCRIPTION_VENDOR),
        ("delete", OPENAI_SERVICE, OPENAI_SUBSCRIPTION_VENDOR),
    ]


@pytest.mark.parametrize("method", ["get", "delete", "update_tokens"])
def test_any_other_vendor_fails_before_transport(method: str) -> None:
    transport = MemoryTransport()
    adapter = store(transport)
    args = {"get": (), "delete": (), "update_tokens": ("a", None, 60)}[method]
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_vendor_unsupported"):
        getattr(adapter, method)("anthropic", *args)
    assert transport.calls == []


def test_save_rejects_wrong_vendor_and_non_sdk_objects_before_transport() -> None:
    transport = MemoryTransport()
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_vendor_unsupported"):
        store(transport).save(FakeOAuthCredentials("other", "a", "r", 2_000_000))
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_record_invalid"):
        store(transport).save({"vendor": "openai", "access_token": "a"})
    assert transport.calls == []


def test_missing_sdk_and_transport_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_transport_unavailable"):
        OpenHandsSubscriptionCredentialStore(None)
    monkeypatch.delitem(sys.modules, "openhands.sdk.llm.auth.credentials")
    monkeypatch.setattr("importlib.import_module", lambda _: (_ for _ in ()).throw(ImportError()))
    with pytest.raises(OpenHandsCredentialStoreError, match="openhands_sdk_unavailable"):
        store(MemoryTransport()).save(credential())


def test_missing_record_update_returns_none_and_refresh_none_is_retained() -> None:
    transport = MemoryTransport()
    assert store(transport).update_tokens("openai", "new", None, 30) is None
    transport.record = SubscriptionCredentialRecord(
        OPENAI_SERVICE, "openai", "old", "kept", 2_000_000, "r1"
    )
    result = store(transport).update_tokens("openai", "new", None, 30)
    assert isinstance(result, FakeOAuthCredentials)
    assert (result.vendor, result.access_token, result.refresh_token, result.expires_at) == (
        "openai", "new", "kept", 1_030_250
    )


def test_rotation_is_cas_and_failure_preserves_original() -> None:
    transport = MemoryTransport()
    original = SubscriptionCredentialRecord(
        OPENAI_SERVICE, "openai", "old-access", "old-refresh", 2_000_000, "r1"
    )
    transport.record = original
    transport.fail = "rotate"
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_rotation_failed"):
        store(transport).update_tokens("openai", "new-access", "new-refresh", 60)
    assert transport.record is original
    assert transport.calls[-1] == ("rotate_atomic", "r1")


def test_validation_precedes_transport() -> None:
    transport = MemoryTransport()
    adapter = store(transport)
    for bad in (0, -1, True, 1.5, "60"):
        with pytest.raises(OpenHandsCredentialStoreError, match="credential_expiration_invalid"):
            adapter.update_tokens("openai", "new", None, bad)  # type: ignore[arg-type]
    assert transport.calls == []


def test_broker_scope_mismatch_fails_closed() -> None:
    transport = MemoryTransport()
    transport.record = SubscriptionCredentialRecord(
        "general_openai", "openai", "access", "refresh", 2_000_000, "r1"
    )
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_scope_mismatch"):
        store(transport).get("openai")


def test_tokens_never_appear_in_repr_or_exception_chains() -> None:
    record = SubscriptionCredentialRecord(
        OPENAI_SERVICE, "openai", "secret-access", "secret-refresh", 2_000_000, "r1"
    )
    assert "secret" not in repr(record)
    transport = MemoryTransport()
    transport.record = record
    returned = store(transport).get("openai")
    assert isinstance(returned, FakeOAuthCredentials)
    assert returned.access_token == "secret-access"
    assert returned.refresh_token == "secret-refresh"
    assert "secret-access" not in repr(returned)
    assert "secret-refresh" not in repr(returned)
    assert "secret-access" not in str(returned)
    assert "secret-refresh" not in str(returned)
    transport.fail = "rotate_exception"
    with pytest.raises(OpenHandsCredentialStoreError) as caught:
        store(transport).update_tokens("openai", "new-access", "new-refresh", 60)
    assert "access" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_save_rejects_expired_but_get_allows_refreshable_expired_record() -> None:
    transport = MemoryTransport()
    adapter = store(transport)
    with pytest.raises(OpenHandsCredentialStoreError, match="credential_expiration_invalid"):
        adapter.save(credential(expires=1_000_250))
    assert transport.calls == []

    transport.record = SubscriptionCredentialRecord(
        OPENAI_SERVICE, "openai", "access", "refresh", 1, "r1"
    )
    loaded = adapter.get("openai")
    assert isinstance(loaded, FakeOAuthCredentials)
    assert loaded.expires_at == 1


def test_no_file_or_environment_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    transport = MemoryTransport()
    adapter = store(transport)
    adapter.save(credential())
    adapter.get("openai")
    adapter.update_tokens("openai", "next", None, 60)
    adapter.delete("openai")
    assert list(tmp_path.iterdir()) == []

from __future__ import annotations

import pytest

from core.secret_store import SecretProviderUnavailable, SecretReference, SecretResolutionContext
from integrations.bitwarden_secret_store import BitwardenSecretsManagerStore


class FakeBitwardenClient:
    def __init__(self, payload: dict[str, object] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def read_secret(
        self,
        *,
        secret_id: str,
        version: str | None,
        machine_identity: str,
        audience: str,
        task_kind: str,
    ) -> object:
        self.calls.append(
            {
                "secret_id": secret_id,
                "version": version,
                "machine_identity": machine_identity,
                "audience": audience,
                "task_kind": task_kind,
            }
        )
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_bitwarden_store_reads_exact_reference_and_scope_via_injected_client() -> None:
    client = FakeBitwardenClient(
        {
            "secret_id": "openhands/openrouter/api-key",
            "version": "ver1",
            "value": "synthetic-openrouter-token",
            "status": "active",
            "allowed_machine_identities": ["runner-host-01"],
            "allowed_audiences": ["openhands"],
            "allowed_task_kinds": ["repair-pr"],
        }
    )
    store = BitwardenSecretsManagerStore.from_production_client(client)
    reference = SecretReference(provider="bitwarden", reference_id="openhands/openrouter/api-key", version="ver1")
    context = SecretResolutionContext(
        machine_identity="runner-host-01",
        audience="openhands",
        task_kind="repair-pr",
    )

    record = store.read(reference, context)

    assert record.value == "synthetic-openrouter-token"
    assert record.reference == reference
    assert client.calls == [
        {
            "secret_id": "openhands/openrouter/api-key",
            "version": "ver1",
            "machine_identity": "runner-host-01",
            "audience": "openhands",
            "task_kind": "repair-pr",
        }
    ]


def test_bitwarden_store_has_no_environment_or_systemd_credential_fallback() -> None:
    with pytest.raises(SecretProviderUnavailable):
        BitwardenSecretsManagerStore.from_environment()

    store = BitwardenSecretsManagerStore(None)
    with pytest.raises(SecretProviderUnavailable):
        store.read(
            SecretReference(provider="bitwarden", reference_id="openhands/openrouter/api-key"),
            SecretResolutionContext(
                machine_identity="runner-host-01",
                audience="openhands",
                task_kind="repair-pr",
            ),
        )


def test_bitwarden_store_fails_closed_when_provider_raises() -> None:
    store = BitwardenSecretsManagerStore(FakeBitwardenClient(RuntimeError("network unavailable")))

    with pytest.raises(SecretProviderUnavailable):
        store.read(
            SecretReference(provider="bitwarden", reference_id="openhands/openrouter/api-key"),
            SecretResolutionContext(
                machine_identity="runner-host-01",
                audience="openhands",
                task_kind="repair-pr",
            ),
        )

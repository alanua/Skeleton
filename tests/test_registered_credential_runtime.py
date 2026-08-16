from __future__ import annotations

import json

import pytest

from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext
from integrations import bitwarden_credential_runtime as bitwarden_runtime
from integrations import credential_runtime


SYNTHETIC_SECRET = "synthetic-openrouter-value"


class FakeStore:
    provider = "bitwarden"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(
        self,
        reference: SecretReference,
        context: SecretResolutionContext,
    ) -> ResolvedSecret:
        assert context.machine_identity == "hetzner-agent-runner-1"
        assert context.audience == "openhands-openrouter"
        assert context.task_kind == "code_generation"
        self.calls.append(reference.reference_id)
        return ResolvedSecret(SYNTHETIC_SECRET)


def _install_fake_provider(monkeypatch):
    store = FakeStore()
    reference_calls: list[str] = []

    def fake_reference(_authority, credential_name: str) -> SecretReference:
        reference_calls.append(credential_name)
        return SecretReference(provider="bitwarden", reference_id="synthetic-ref")

    monkeypatch.setattr(
        credential_runtime,
        "bitwarden_reference_from_systemd_credential",
        fake_reference,
    )
    monkeypatch.setattr(
        bitwarden_runtime.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(lambda cls, authority: store),
    )
    return store, reference_calls


def test_runner_openhands_uses_registered_broker_binding(monkeypatch) -> None:
    store, reference_calls = _install_fake_provider(monkeypatch)
    environment = {"PATH": "/synthetic/bin", "UNRELATED": "keep"}

    receipt = credential_runtime.bind_registered_environment_credential(
        service_id="runner-openhands",
        alias="openrouter-api",
        action_id="bind-openrouter-fallback",
        environment=environment,
        authority_environment={},
    )

    assert receipt["result"]["status"] == "USED"
    assert environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == SYNTHETIC_SECRET
    assert environment["UNRELATED"] == "keep"
    assert reference_calls == ["openrouter-secret-ref"]
    assert store.calls == ["synthetic-ref"]
    assert SYNTHETIC_SECRET not in json.dumps(receipt, sort_keys=True)


def test_unregistered_action_rejected_before_provider_resolution(monkeypatch) -> None:
    provider_calls: list[bool] = []
    monkeypatch.setattr(
        credential_runtime,
        "bitwarden_reference_from_systemd_credential",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    with pytest.raises(
        credential_runtime.RegisteredCredentialRuntimeError,
        match="registered_credential_action_mismatch",
    ):
        credential_runtime.bind_registered_environment_credential(
            service_id="runner-openhands",
            alias="openrouter-api",
            action_id="arbitrary-shell",
            environment={},
            authority_environment={},
        )

    assert provider_calls == []


def test_unregistered_service_rejected_before_provider_resolution(monkeypatch) -> None:
    provider_calls: list[bool] = []
    monkeypatch.setattr(
        credential_runtime,
        "bitwarden_reference_from_systemd_credential",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    with pytest.raises(
        credential_runtime.RegisteredCredentialRuntimeError,
        match="registered_credential_unavailable",
    ):
        credential_runtime.bind_registered_environment_credential(
            service_id="other-service",
            alias="openrouter-api",
            action_id="bind-openrouter-fallback",
            environment={},
            authority_environment={},
        )

    assert provider_calls == []


def test_registration_metadata_is_public_safe() -> None:
    capabilities = credential_runtime.registered_credential_capabilities()
    serialized = json.dumps(capabilities, sort_keys=True)
    assert "runner-openhands" in serialized
    assert "openrouter-api" in serialized
    assert "openrouter-secret-ref" not in serialized
    assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in serialized

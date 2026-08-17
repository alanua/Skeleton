from __future__ import annotations

import json

import pytest

from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext
from integrations import bitwarden_credential_runtime as bitwarden_runtime
from integrations import credential_runtime


SYNTHETIC_SECRET = "synthetic-openrouter-value"
GMAIL_SECRET = "synthetic-gmail-oauth-bundle"


class FakeStore:
    provider = "bitwarden"

    def __init__(self, value: str = SYNTHETIC_SECRET) -> None:
        self.value = value
        self.calls: list[tuple[str, SecretResolutionContext]] = []

    def resolve(
        self,
        reference: SecretReference,
        context: SecretResolutionContext,
    ) -> ResolvedSecret:
        self.calls.append((reference.reference_id, context))
        return ResolvedSecret(self.value)


def _install_fake_provider(monkeypatch, *, value: str = SYNTHETIC_SECRET):
    store = FakeStore(value)
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
    assert store.calls[0][0] == "synthetic-ref"
    assert store.calls[0][1].machine_identity == "hetzner-agent-runner-1"
    assert store.calls[0][1].audience == "openhands-openrouter"
    assert store.calls[0][1].task_kind == "code_generation"
    assert SYNTHETIC_SECRET not in json.dumps(receipt, sort_keys=True)


def test_gmail_material_is_consumed_in_process_and_not_returned(monkeypatch) -> None:
    store, reference_calls = _install_fake_provider(monkeypatch, value=GMAIL_SECRET)
    consumed: list[str] = []

    receipt = credential_runtime.consume_registered_material_credential(
        service_id="mail-gmail",
        alias="acct:gmail-primary",
        action_id="use-gmail-readonly-oauth",
        consumer=consumed.append,
        authority_environment={},
    )

    assert receipt["result"]["status"] == "USED"
    assert consumed == [GMAIL_SECRET]
    assert reference_calls == ["gmail-primary-oauth-secret-ref"]
    assert store.calls[0][1].audience == "mail-gmail-readonly"
    assert store.calls[0][1].task_kind == "mail_poll"
    assert GMAIL_SECRET not in json.dumps(receipt, sort_keys=True)


def test_gmail_accounts_use_distinct_code_owned_reference_credentials(monkeypatch) -> None:
    _store, reference_calls = _install_fake_provider(monkeypatch, value=GMAIL_SECRET)

    for alias in ("acct:gmail-primary", "acct:gmail-secondary"):
        credential_runtime.consume_registered_material_credential(
            service_id="mail-gmail",
            alias=alias,
            action_id="use-gmail-readonly-oauth",
            consumer=lambda _value: None,
            authority_environment={},
        )

    assert reference_calls == [
        "gmail-primary-oauth-secret-ref",
        "gmail-secondary-oauth-secret-ref",
    ]


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


def test_unregistered_gmail_alias_rejected_before_provider_resolution(monkeypatch) -> None:
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
        credential_runtime.consume_registered_material_credential(
            service_id="mail-gmail",
            alias="acct:caller-selected",
            action_id="use-gmail-readonly-oauth",
            consumer=lambda _value: None,
            authority_environment={},
        )

    assert provider_calls == []


def test_registration_metadata_is_public_safe() -> None:
    capabilities = credential_runtime.registered_credential_capabilities()
    serialized = json.dumps(capabilities, sort_keys=True)
    assert "runner-openhands" in serialized
    assert "openrouter-api" in serialized
    assert "mail-gmail" in serialized
    assert "acct:gmail-primary" in serialized
    assert "acct:gmail-secondary" in serialized
    assert "openrouter-secret-ref" not in serialized
    assert "gmail-primary-oauth-secret-ref" not in serialized
    assert "gmail-secondary-oauth-secret-ref" not in serialized
    assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in serialized

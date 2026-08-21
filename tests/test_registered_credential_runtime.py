from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext
from core.secret_reference import (
    REFERENCE_BOOTSTRAP_REQUIRED,
    registered_bitwarden_reference_from_systemd_index,
)
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

    def fake_reference(
        _authority,
        *,
        service_id: str,
        alias: str,
        bootstrap_required: bool,
        fallback_credential_name: str,
    ) -> SecretReference:
        del service_id, alias, bootstrap_required
        credential_name = fallback_credential_name
        reference_calls.append(f"direct:{credential_name}")
        return SecretReference(provider="bitwarden", reference_id="synthetic-ref")

    monkeypatch.setattr(
        credential_runtime,
        "registered_bitwarden_reference_from_systemd_index",
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
    assert reference_calls == ["direct:openrouter-secret-ref"]
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
    assert reference_calls == ["direct:gmail-primary-oauth-secret-ref"]
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
        "direct:gmail-primary-oauth-secret-ref",
        "direct:gmail-secondary-oauth-secret-ref",
    ]


def _authority_with_index(tmp_path, payload: dict[str, object]) -> dict[str, str]:
    credentials = tmp_path / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "skeleton-secret-reference-index").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return {"CREDENTIALS_DIRECTORY": str(credentials)}


def test_registered_reference_index_requires_bootstrap_when_absent(tmp_path) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir()

    with pytest.raises(Exception, match=REFERENCE_BOOTSTRAP_REQUIRED):
        registered_bitwarden_reference_from_systemd_index(
            {"CREDENTIALS_DIRECTORY": str(credentials)},
            service_id="mail-gmail",
            alias="acct:gmail-primary",
            bootstrap_required=True,
        )


def test_registered_reference_index_returns_exactly_one_opaque_reference(tmp_path) -> None:
    sentinel = "SECRET_VALUE_SENTINEL"
    authority = _authority_with_index(
        tmp_path,
        {
            "schema": "skeleton.secret_reference_index.v1",
            "registrations": [
                {
                    "service_id": "mail-gmail",
                    "alias": "acct:gmail-primary",
                    "provider": "bitwarden",
                    "reference_id": "11111111-2222-3333-4444-555555555555",
                }
            ],
            "ignored_private_value": sentinel,
        },
    )

    with pytest.raises(Exception, match="REFERENCE_INDEX_INVALID"):
        registered_bitwarden_reference_from_systemd_index(
            authority,
            service_id="mail-gmail",
            alias="acct:gmail-primary",
        )

    authority = _authority_with_index(
        tmp_path / "valid",
        {
            "schema": "skeleton.secret_reference_index.v1",
            "registrations": [
                {
                    "service_id": "mail-gmail",
                    "alias": "acct:gmail-primary",
                    "provider": "bitwarden",
                    "reference_id": "11111111-2222-3333-4444-555555555555",
                }
            ],
        },
    )
    reference = registered_bitwarden_reference_from_systemd_index(
        authority,
        service_id="mail-gmail",
        alias="acct:gmail-primary",
    )

    assert reference == SecretReference(
        provider="bitwarden",
        reference_id="11111111-2222-3333-4444-555555555555",
    )
    assert sentinel not in repr(reference)


def test_registered_reference_index_rejects_none_and_ambiguous(tmp_path) -> None:
    none_authority = _authority_with_index(
        tmp_path / "none",
        {"schema": "skeleton.secret_reference_index.v1", "registrations": []},
    )
    with pytest.raises(Exception, match=REFERENCE_BOOTSTRAP_REQUIRED):
        registered_bitwarden_reference_from_systemd_index(
            none_authority,
            service_id="mail-gmail",
            alias="acct:gmail-primary",
        )

    ambiguous_authority = _authority_with_index(
        tmp_path / "ambiguous",
        {
            "schema": "skeleton.secret_reference_index.v1",
            "registrations": [
                {
                    "service_id": "mail-gmail",
                    "alias": "acct:gmail-primary",
                    "provider": "bitwarden",
                    "reference_id": "11111111-2222-3333-4444-555555555555",
                },
                {
                    "service_id": "mail-gmail",
                    "alias": "acct:gmail-primary",
                    "provider": "bitwarden",
                    "reference_id": "66666666-7777-8888-9999-000000000000",
                },
            ],
        },
    )
    with pytest.raises(Exception, match="REFERENCE_REGISTRATION_AMBIGUOUS"):
        registered_bitwarden_reference_from_systemd_index(
            ambiguous_authority,
            service_id="mail-gmail",
            alias="acct:gmail-primary",
        )


def test_gmail_primary_missing_reference_index_blocks_before_store_bootstrap(
    tmp_path,
    monkeypatch,
) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    store_calls: list[bool] = []
    monkeypatch.setattr(
        bitwarden_runtime.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(lambda cls, authority: store_calls.append(True)),
    )

    with pytest.raises(
        credential_runtime.RegisteredCredentialRuntimeError,
        match=REFERENCE_BOOTSTRAP_REQUIRED,
    ):
        credential_runtime.consume_registered_material_credential(
            service_id="mail-gmail",
            alias="acct:gmail-primary",
            action_id="use-gmail-readonly-oauth",
            consumer=lambda _value: None,
            authority_environment={"CREDENTIALS_DIRECTORY": str(credentials)},
        )

    assert store_calls == []


def test_unregistered_action_rejected_before_provider_resolution(monkeypatch) -> None:
    provider_calls: list[bool] = []
    monkeypatch.setattr(
        credential_runtime,
        "registered_bitwarden_reference_from_systemd_index",
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
        "registered_bitwarden_reference_from_systemd_index",
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
        "registered_bitwarden_reference_from_systemd_index",
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


def test_mail_operations_systemd_service_uses_only_credential_boundary() -> None:
    service = Path("ops/systemd/skeleton-mail-operations.service").read_text(
        encoding="utf-8"
    )

    assert "LoadCredentialEncrypted=bitwarden-access-token:" in service
    assert "LoadCredentialEncrypted=skeleton-secret-reference-index:" in service
    assert "BWS_ACCESS_TOKEN=" not in service
    assert "gmail-primary-oauth" not in service
    assert "Environment=BITWARDEN" not in service


def test_bitwarden_sdk_install_is_pinned_isolated_and_version_checked() -> None:
    installer = Path("scripts/install_bitwarden_sdk_runtime.sh").read_text(
        encoding="utf-8"
    )

    assert "-m venv" in installer
    assert "bitwarden-sdk==${PINNED_VERSION}" in installer
    assert "--only-binary=:all:" in installer
    assert "metadata.version(\"bitwarden-sdk\")" in installer
    assert "sudo" not in installer
    assert "pip install bitwarden-sdk" not in installer

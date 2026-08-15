from __future__ import annotations

import pytest

from core.secret_store import (
    InvalidSecretReference,
    ResolvedSecret,
    SecretAccessPolicy,
    SecretOutOfScope,
    SecretProviderUnavailable,
    SecretReference,
    SecretResolutionContext,
    SecretStoreGate,
)


class FakeStore:
    provider = "bitwarden"

    def __init__(self, value: str = "synthetic-secret", *, unavailable: bool = False) -> None:
        self.value = value
        self.unavailable = unavailable
        self.calls: list[tuple[SecretReference, SecretResolutionContext]] = []

    def resolve(self, reference: SecretReference, context: SecretResolutionContext) -> ResolvedSecret:
        self.calls.append((reference, context))
        if self.unavailable:
            raise SecretProviderUnavailable("provider_down")
        return ResolvedSecret(self.value)


def _reference() -> SecretReference:
    return SecretReference(provider="bitwarden", reference_id="openhands-openrouter-key")


def _context() -> SecretResolutionContext:
    return SecretResolutionContext(
        machine_identity="runner-host-01",
        audience="openhands-openrouter",
        task_kind="code_generation",
    )


def _policy() -> SecretAccessPolicy:
    return SecretAccessPolicy(
        allowed_machine_identities=frozenset({"runner-host-01"}),
        allowed_audiences=frozenset({"openhands-openrouter"}),
        allowed_task_kinds=frozenset({"code_generation"}),
    )


def test_secret_store_gate_resolves_only_exact_policy_scope() -> None:
    store = FakeStore()
    reference = _reference()
    gate = SecretStoreGate(
        {"bitwarden": store},
        {(reference.provider, reference.reference_id): _policy()},
    )

    material = gate.resolve(reference, _context())
    child = material.inject({"SAFE": "1"}, "OPENROUTER_API_KEY")

    assert child == {"SAFE": "1", "OPENROUTER_API_KEY": "synthetic-secret"}
    assert repr(material) == "<ResolvedSecret redacted>"
    assert str(material) == "<redacted>"
    assert store.calls == [(reference, _context())]


def test_secret_store_gate_denies_before_provider_access_when_scope_is_wrong() -> None:
    store = FakeStore()
    reference = _reference()
    gate = SecretStoreGate(
        {"bitwarden": store},
        {(reference.provider, reference.reference_id): _policy()},
    )
    wrong = SecretResolutionContext(
        machine_identity="other-host",
        audience="openhands-openrouter",
        task_kind="code_generation",
    )

    with pytest.raises(SecretOutOfScope):
        gate.resolve(reference, wrong)

    assert store.calls == []


def test_secret_store_gate_fails_closed_for_missing_policy_or_provider() -> None:
    reference = _reference()
    with pytest.raises(SecretOutOfScope):
        SecretStoreGate({"bitwarden": FakeStore()}, {}).resolve(reference, _context())

    with pytest.raises(SecretProviderUnavailable):
        SecretStoreGate({}, {(reference.provider, reference.reference_id): _policy()}).resolve(reference, _context())


def test_secret_reference_round_trip_and_unknown_fields_fail_closed() -> None:
    reference = SecretReference.from_mapping(
        {"provider": "bitwarden", "reference_id": "openhands-openrouter-key", "version": "version-001"}
    )
    assert reference.to_mapping() == {
        "provider": "bitwarden",
        "reference_id": "openhands-openrouter-key",
        "version": "version-001",
    }

    with pytest.raises(InvalidSecretReference):
        SecretReference.from_mapping({"provider": "bitwarden", "reference_id": "valid-ref", "value": "secret"})

    with pytest.raises(InvalidSecretReference):
        SecretReference(provider="../bitwarden", reference_id="valid-ref")

    with pytest.raises(InvalidSecretReference):
        SecretReference(provider="bitwarden", reference_id="../private/path")

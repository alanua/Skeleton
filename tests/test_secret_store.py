from __future__ import annotations

import pytest

from core.secret_store import (
    InvalidSecretReference,
    SecretMissing,
    SecretOutOfScope,
    SecretProviderUnavailable,
    SecretRecord,
    SecretReference,
    SecretResolutionContext,
    SecretRevoked,
    SecretStatus,
    SecretStoreGate,
)


class FakeStore:
    provider = "bitwarden"

    def __init__(self, record: SecretRecord | None = None, *, unavailable: bool = False) -> None:
        self.record = record
        self.unavailable = unavailable
        self.calls: list[tuple[SecretReference, SecretResolutionContext]] = []

    def read(self, reference: SecretReference, context: SecretResolutionContext) -> SecretRecord:
        self.calls.append((reference, context))
        if self.unavailable:
            raise SecretProviderUnavailable("provider_down")
        if self.record is None:
            raise SecretMissing("missing")
        return self.record


def _reference() -> SecretReference:
    return SecretReference(provider="bitwarden", reference_id="openhands/openrouter/api-key", version="ver1")


def _context() -> SecretResolutionContext:
    return SecretResolutionContext(
        machine_identity="runner-host-01",
        audience="openhands",
        task_kind="repair-pr",
    )


def _record(**overrides: object) -> SecretRecord:
    values = {
        "reference": _reference(),
        "value": "synthetic-openrouter-token",
        "status": SecretStatus.ACTIVE,
        "allowed_machine_identities": frozenset({"runner-host-01"}),
        "allowed_audiences": frozenset({"openhands"}),
        "allowed_task_kinds": frozenset({"repair-pr"}),
    }
    values.update(overrides)
    return SecretRecord(**values)


def test_secret_store_gate_resolves_only_exact_reference_and_scope() -> None:
    store = FakeStore(_record())
    gate = SecretStoreGate({"bitwarden": store})

    assert gate.resolve(_reference(), _context()) == "synthetic-openrouter-token"
    assert store.calls == [(_reference(), _context())]


def test_secret_store_gate_fails_closed_for_provider_down() -> None:
    gate = SecretStoreGate({"bitwarden": FakeStore(unavailable=True)})

    with pytest.raises(SecretProviderUnavailable):
        gate.resolve(_reference(), _context())


@pytest.mark.parametrize(
    ("record", "error"),
    [
        (_record(status=SecretStatus.REVOKED), SecretRevoked),
        (_record(status=SecretStatus.MISSING), SecretMissing),
        (_record(reference=SecretReference(provider="bitwarden", reference_id="other/ref")), SecretOutOfScope),
        (_record(allowed_machine_identities=frozenset({"other-host"})), SecretOutOfScope),
        (_record(allowed_audiences=frozenset({"codex"})), SecretOutOfScope),
        (_record(allowed_task_kinds=frozenset({"other-task"})), SecretOutOfScope),
    ],
)
def test_secret_store_gate_fails_closed_for_revoked_missing_or_out_of_scope(
    record: SecretRecord,
    error: type[Exception],
) -> None:
    gate = SecretStoreGate({"bitwarden": FakeStore(record)})

    with pytest.raises(error):
        gate.resolve(_reference(), _context())


def test_secret_reference_rejects_ambient_or_invalid_provider_names() -> None:
    with pytest.raises(InvalidSecretReference):
        SecretReference(provider="../bitwarden", reference_id="openhands/openrouter/api-key")

    with pytest.raises(InvalidSecretReference):
        SecretReference(provider="bitwarden", reference_id="../private/path")

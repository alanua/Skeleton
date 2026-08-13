from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import re
from typing import Protocol


_REFERENCE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{2,255}$")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class SecretResolutionError(RuntimeError):
    """Base class for fail-closed secret resolution errors."""


class InvalidSecretReference(SecretResolutionError):
    pass


class SecretProviderUnavailable(SecretResolutionError):
    pass


class SecretMissing(SecretResolutionError):
    pass


class SecretRevoked(SecretResolutionError):
    pass


class SecretOutOfScope(SecretResolutionError):
    pass


class SecretStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class SecretReference:
    provider: str
    reference_id: str
    version: str | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        reference_id = self.reference_id.strip()
        version = self.version.strip() if self.version is not None else None
        if not _PROVIDER_RE.fullmatch(provider):
            raise InvalidSecretReference("invalid_secret_provider")
        if not _REFERENCE_ID_RE.fullmatch(reference_id):
            raise InvalidSecretReference("invalid_secret_reference_id")
        if version is not None and not _REFERENCE_ID_RE.fullmatch(version):
            raise InvalidSecretReference("invalid_secret_version")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "reference_id", reference_id)
        object.__setattr__(self, "version", version)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SecretReference:
        provider = value.get("provider")
        reference_id = value.get("reference_id")
        version = value.get("version")
        if not isinstance(provider, str) or not isinstance(reference_id, str):
            raise InvalidSecretReference("secret_reference_requires_provider_and_reference_id")
        if version is not None and not isinstance(version, str):
            raise InvalidSecretReference("secret_reference_version_must_be_string")
        return cls(provider=provider, reference_id=reference_id, version=version)


@dataclass(frozen=True, slots=True)
class SecretResolutionContext:
    machine_identity: str
    audience: str
    task_kind: str

    def __post_init__(self) -> None:
        for field in ("machine_identity", "audience", "task_kind"):
            value = getattr(self, field).strip()
            if not value or len(value) > 128 or not _REFERENCE_ID_RE.fullmatch(value):
                raise SecretOutOfScope(f"invalid_{field}")
            object.__setattr__(self, field, value)


@dataclass(frozen=True, slots=True)
class SecretRecord:
    reference: SecretReference
    value: str
    status: SecretStatus
    allowed_machine_identities: frozenset[str]
    allowed_audiences: frozenset[str]
    allowed_task_kinds: frozenset[str]

    def __post_init__(self) -> None:
        if not self.value:
            raise SecretMissing("empty_secret_value")
        object.__setattr__(self, "status", SecretStatus(self.status))
        object.__setattr__(self, "allowed_machine_identities", frozenset(self.allowed_machine_identities))
        object.__setattr__(self, "allowed_audiences", frozenset(self.allowed_audiences))
        object.__setattr__(self, "allowed_task_kinds", frozenset(self.allowed_task_kinds))


class SecretStore(Protocol):
    provider: str

    def read(self, reference: SecretReference, context: SecretResolutionContext) -> SecretRecord:
        """Return a secret record for exactly this reference and context."""


class SecretStoreGate:
    def __init__(self, stores: Mapping[str, SecretStore]) -> None:
        self._stores = dict(stores)

    def resolve(self, reference: SecretReference, context: SecretResolutionContext) -> str:
        store = self._stores.get(reference.provider)
        if store is None:
            raise SecretProviderUnavailable("secret_provider_unavailable")
        if store.provider != reference.provider:
            raise SecretProviderUnavailable("secret_provider_mismatch")
        record = store.read(reference, context)
        if record.reference != reference:
            raise SecretOutOfScope("secret_reference_mismatch")
        if record.status is SecretStatus.MISSING:
            raise SecretMissing("secret_missing")
        if record.status is SecretStatus.REVOKED:
            raise SecretRevoked("secret_revoked")
        if record.status is not SecretStatus.ACTIVE:
            raise SecretRevoked("secret_not_active")
        if context.machine_identity not in record.allowed_machine_identities:
            raise SecretOutOfScope("secret_machine_identity_out_of_scope")
        if context.audience not in record.allowed_audiences:
            raise SecretOutOfScope("secret_audience_out_of_scope")
        if context.task_kind not in record.allowed_task_kinds:
            raise SecretOutOfScope("secret_task_kind_out_of_scope")
        return record.value

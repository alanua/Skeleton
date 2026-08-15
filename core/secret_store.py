from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Protocol


_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{2,255}$")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_CONTEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}$")


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
    def from_mapping(cls, value: Mapping[str, object]) -> "SecretReference":
        if set(value) - {"provider", "reference_id", "version"}:
            raise InvalidSecretReference("secret_reference_contains_unknown_fields")
        provider = value.get("provider")
        reference_id = value.get("reference_id")
        version = value.get("version")
        if not isinstance(provider, str) or not isinstance(reference_id, str):
            raise InvalidSecretReference("secret_reference_requires_provider_and_reference_id")
        if version is not None and not isinstance(version, str):
            raise InvalidSecretReference("secret_reference_version_must_be_string")
        return cls(provider=provider, reference_id=reference_id, version=version)

    def to_mapping(self) -> dict[str, str]:
        result = {"provider": self.provider, "reference_id": self.reference_id}
        if self.version is not None:
            result["version"] = self.version
        return result


@dataclass(frozen=True, slots=True)
class SecretResolutionContext:
    machine_identity: str
    audience: str
    task_kind: str

    def __post_init__(self) -> None:
        for field_name in ("machine_identity", "audience", "task_kind"):
            value = getattr(self, field_name).strip()
            if not _CONTEXT_RE.fullmatch(value):
                raise SecretOutOfScope(f"invalid_{field_name}")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class SecretAccessPolicy:
    allowed_machine_identities: frozenset[str]
    allowed_audiences: frozenset[str]
    allowed_task_kinds: frozenset[str]

    def permits(self, context: SecretResolutionContext) -> bool:
        return (
            context.machine_identity in self.allowed_machine_identities
            and context.audience in self.allowed_audiences
            and context.task_kind in self.allowed_task_kinds
        )


class ResolvedSecret:
    """Ephemeral secret material with a deliberately redacted representation."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise SecretMissing("empty_secret_value")
        self.__value = value

    def __repr__(self) -> str:
        return "<ResolvedSecret redacted>"

    def __str__(self) -> str:
        return "<redacted>"

    def inject(self, environment: Mapping[str, str], variable: str) -> dict[str, str]:
        if not isinstance(variable, str) or not variable or "=" in variable or "\x00" in variable:
            raise ValueError("invalid_environment_variable")
        child = dict(environment)
        child[variable] = self.__value
        return child


class SecretStore(Protocol):
    provider: str

    def resolve(self, reference: SecretReference, context: SecretResolutionContext) -> ResolvedSecret:
        """Resolve exactly one reference without persisting or logging plaintext."""


class SecretStoreGate:
    def __init__(
        self,
        stores: Mapping[str, SecretStore],
        policies: Mapping[tuple[str, str], SecretAccessPolicy],
    ) -> None:
        self._stores = dict(stores)
        self._policies = dict(policies)

    def resolve(self, reference: SecretReference, context: SecretResolutionContext) -> ResolvedSecret:
        if not isinstance(reference, SecretReference):
            raise InvalidSecretReference("typed_secret_reference_required")
        if not isinstance(context, SecretResolutionContext):
            raise SecretOutOfScope("typed_secret_resolution_context_required")
        policy = self._policies.get((reference.provider, reference.reference_id))
        if policy is None or not policy.permits(context):
            raise SecretOutOfScope("secret_resolution_out_of_scope")
        store = self._stores.get(reference.provider)
        if store is None or store.provider != reference.provider:
            raise SecretProviderUnavailable("secret_provider_unavailable")
        material = store.resolve(reference, context)
        if not isinstance(material, ResolvedSecret):
            raise SecretProviderUnavailable("secret_provider_contract_mismatch")
        return material

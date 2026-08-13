from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.secret_store import (
    SecretMissing,
    SecretProviderUnavailable,
    SecretRecord,
    SecretReference,
    SecretResolutionContext,
    SecretStatus,
    SecretStore,
)


class BitwardenSecretsManagerClient(Protocol):
    def read_secret(
        self,
        *,
        secret_id: str,
        version: str | None,
        machine_identity: str,
        audience: str,
        task_kind: str,
    ) -> object:
        """Read one Bitwarden Secrets Manager secret by exact id and caller scope."""


@dataclass(frozen=True, slots=True)
class BitwardenSecretPayload:
    secret_id: str
    value: str | None
    status: str
    allowed_machine_identities: frozenset[str]
    allowed_audiences: frozenset[str]
    allowed_task_kinds: frozenset[str]
    version: str | None = None


class BitwardenSecretsManagerStore(SecretStore):
    provider = "bitwarden"

    def __init__(self, client: BitwardenSecretsManagerClient | None) -> None:
        self._client = client

    @classmethod
    def from_production_client(cls, client: BitwardenSecretsManagerClient) -> BitwardenSecretsManagerStore:
        return cls(client)

    @classmethod
    def from_environment(cls) -> BitwardenSecretsManagerStore:
        # Ambient env/systemd credential discovery is intentionally unsupported.
        # Production wiring must pass an authenticated Bitwarden client explicitly.
        raise SecretProviderUnavailable("bitwarden_client_required")

    def read(self, reference: SecretReference, context: SecretResolutionContext) -> SecretRecord:
        if reference.provider != self.provider:
            raise SecretProviderUnavailable("bitwarden_reference_provider_mismatch")
        if self._client is None:
            raise SecretProviderUnavailable("bitwarden_client_unavailable")
        try:
            payload = self._client.read_secret(
                secret_id=reference.reference_id,
                version=reference.version,
                machine_identity=context.machine_identity,
                audience=context.audience,
                task_kind=context.task_kind,
            )
        except SecretProviderUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - defensive contract boundary
            raise SecretProviderUnavailable("bitwarden_provider_down") from exc
        normalized = self._normalize_payload(payload)
        if normalized.value is None:
            raise SecretMissing("bitwarden_secret_missing")
        return SecretRecord(
            reference=SecretReference(provider=self.provider, reference_id=normalized.secret_id, version=normalized.version),
            value=normalized.value,
            status=SecretStatus(normalized.status),
            allowed_machine_identities=normalized.allowed_machine_identities,
            allowed_audiences=normalized.allowed_audiences,
            allowed_task_kinds=normalized.allowed_task_kinds,
        )

    @staticmethod
    def _normalize_payload(payload: object) -> BitwardenSecretPayload:
        if isinstance(payload, BitwardenSecretPayload):
            return payload
        if isinstance(payload, dict):
            return BitwardenSecretPayload(
                secret_id=str(payload.get("secret_id", "")),
                value=payload.get("value") if isinstance(payload.get("value"), str) else None,
                status=str(payload.get("status", "missing")),
                allowed_machine_identities=frozenset(str(v) for v in payload.get("allowed_machine_identities", ())),
                allowed_audiences=frozenset(str(v) for v in payload.get("allowed_audiences", ())),
                allowed_task_kinds=frozenset(str(v) for v in payload.get("allowed_task_kinds", ())),
                version=str(payload["version"]) if payload.get("version") is not None else None,
            )
        raise SecretProviderUnavailable("bitwarden_payload_contract_mismatch")

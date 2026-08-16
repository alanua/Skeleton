from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Protocol

from core.secret_store import (
    ResolvedSecret,
    SecretAccessPolicy,
    SecretMissing,
    SecretOutOfScope,
    SecretProviderUnavailable,
    SecretResolutionContext,
    SecretResolutionError,
    SecretRevoked,
    SecretStore,
    SecretStoreGate,
)
from core.service_credentials import (
    ServiceCredentialBinding,
    ServiceCredentialBindingError,
    ServiceCredentialCatalog,
)


_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


class CredentialBrokerError(RuntimeError):
    pass


class CredentialRequestError(CredentialBrokerError):
    pass


class CredentialDeliveryError(CredentialBrokerError):
    pass


@dataclass(frozen=True, slots=True)
class CredentialReceipt:
    operation: str
    service_id: str
    alias: str
    action_id: str
    adapter_id: str
    provider: str
    reference_id: str
    status: str
    reason_class: str
    receipt_hash: str

    def to_public_mapping(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "service_id": self.service_id,
            "alias": self.alias,
            "action_id": self.action_id,
            "adapter_id": self.adapter_id,
            "provider": self.provider,
            "reference_id": self.reference_id,
            "status": self.status,
            "reason_class": self.reason_class,
            "receipt_hash": self.receipt_hash,
        }


class CredentialDeliveryAdapter(Protocol):
    adapter_id: str

    def deliver(self, material: ResolvedSecret, binding: ServiceCredentialBinding) -> None:
        """Consume secret material inside a pre-registered trusted target boundary."""


TrustedCredentialConsumer = Callable[[ResolvedSecret, ServiceCredentialBinding], None]


class InProcessCredentialAdapter:
    adapter_id = "in_process"

    def __init__(self, targets: Mapping[str, TrustedCredentialConsumer]) -> None:
        self._targets = dict(targets)

    def deliver(self, material: ResolvedSecret, binding: ServiceCredentialBinding) -> None:
        consumer = self._targets.get(binding.target_id)
        if consumer is None:
            raise CredentialDeliveryError("registered_in_process_target_unavailable")
        try:
            result = consumer(material, binding)
        except Exception:
            raise CredentialDeliveryError("registered_in_process_target_failed") from None
        if result is not None:
            raise CredentialDeliveryError("registered_in_process_target_contract_violation")


@dataclass(frozen=True, slots=True)
class ProcessCredentialTarget:
    target_id: str
    argv: tuple[str, ...]
    environment_variable: str
    cwd: str | None = None
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.target_id or not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{1,127}", self.target_id):
            raise CredentialDeliveryError("invalid_process_target_id")
        if not self.argv or any(not isinstance(item, str) or not item or "\x00" in item for item in self.argv):
            raise CredentialDeliveryError("invalid_process_target_argv")
        executable = Path(self.argv[0])
        if not executable.is_absolute():
            raise CredentialDeliveryError("process_target_executable_must_be_absolute")
        if not _ENV_RE.fullmatch(self.environment_variable):
            raise CredentialDeliveryError("invalid_process_target_environment_variable")
        if self.cwd is not None and not Path(self.cwd).is_absolute():
            raise CredentialDeliveryError("process_target_cwd_must_be_absolute")
        if not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= 600:
            raise CredentialDeliveryError("invalid_process_target_timeout")


class ProcessCredentialAdapter:
    adapter_id = "process"

    def __init__(
        self,
        targets: Mapping[str, ProcessCredentialTarget],
        *,
        base_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._targets = dict(targets)
        self._base_environment = dict(base_environment or {})

    def deliver(self, material: ResolvedSecret, binding: ServiceCredentialBinding) -> None:
        target = self._targets.get(binding.target_id)
        if target is None:
            raise CredentialDeliveryError("registered_process_target_unavailable")
        child_environment = material.inject(
            self._base_environment,
            target.environment_variable,
        )
        try:
            result = subprocess.run(
                list(target.argv),
                cwd=target.cwd,
                env=child_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=target.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise CredentialDeliveryError("registered_process_target_failed") from None
        if result.returncode != 0:
            raise CredentialDeliveryError("registered_process_target_nonzero")


class CredentialBroker:
    """Provider-neutral broker using trusted runtime identity, never caller-supplied policy context."""

    def __init__(
        self,
        *,
        catalog: ServiceCredentialCatalog,
        stores: Mapping[str, SecretStore],
        adapters: Mapping[str, CredentialDeliveryAdapter],
        runtime_contexts: Mapping[str, SecretResolutionContext],
    ) -> None:
        if not isinstance(catalog, ServiceCredentialCatalog):
            raise CredentialBrokerError("typed_service_credential_catalog_required")
        self._catalog = catalog
        self._stores = dict(stores)
        self._adapters = dict(adapters)
        self._runtime_contexts = dict(runtime_contexts)
        for provider, store in self._stores.items():
            if not isinstance(provider, str) or not provider or getattr(store, "provider", None) != provider:
                raise CredentialBrokerError("secret_store_registry_contract_mismatch")
        for adapter_id, adapter in self._adapters.items():
            if not isinstance(adapter_id, str) or getattr(adapter, "adapter_id", None) != adapter_id:
                raise CredentialBrokerError("credential_adapter_registry_contract_mismatch")
        for service_id, context in self._runtime_contexts.items():
            if not isinstance(service_id, str) or not service_id:
                raise CredentialBrokerError("invalid_runtime_service_identity")
            if not isinstance(context, SecretResolutionContext):
                raise CredentialBrokerError("typed_runtime_context_required")

    def _binding(self, service_id: str, alias: str) -> ServiceCredentialBinding:
        try:
            return self._catalog.get(service_id, alias)
        except ServiceCredentialBindingError:
            raise CredentialRequestError("service_credential_binding_unavailable") from None

    def _runtime_context(self, service_id: str) -> SecretResolutionContext:
        context = self._runtime_contexts.get(service_id)
        if context is None:
            raise CredentialRequestError("trusted_runtime_context_unavailable")
        return context

    def _resolve(self, binding: ServiceCredentialBinding) -> ResolvedSecret:
        policy = SecretAccessPolicy(
            allowed_machine_identities=frozenset({binding.context.machine_identity}),
            allowed_audiences=frozenset({binding.context.audience}),
            allowed_task_kinds=frozenset({binding.context.task_kind}),
        )
        gate = SecretStoreGate(
            stores=self._stores,
            policies={(binding.reference.provider, binding.reference.reference_id): policy},
        )
        return gate.resolve(binding.reference, self._runtime_context(binding.service_id))

    @staticmethod
    def _resolution_reason(exc: SecretResolutionError) -> str:
        if isinstance(exc, SecretMissing):
            return "SECRET_MISSING"
        if isinstance(exc, SecretRevoked):
            return "SECRET_REVOKED"
        if isinstance(exc, SecretOutOfScope):
            return "SECRET_OUT_OF_SCOPE"
        if isinstance(exc, SecretProviderUnavailable):
            return "SECRET_PROVIDER_UNAVAILABLE"
        return "SECRET_RESOLUTION_FAILED"

    @staticmethod
    def _receipt(
        *,
        operation: str,
        binding: ServiceCredentialBinding,
        status: str,
        reason_class: str,
    ) -> CredentialReceipt:
        safe = {
            "operation": operation,
            "service_id": binding.service_id,
            "alias": binding.alias,
            "action_id": binding.action_id,
            "adapter_id": binding.adapter_id,
            "provider": binding.reference.provider,
            "reference_id": binding.reference.reference_id,
            "status": status,
            "reason_class": reason_class,
        }
        receipt_hash = hashlib.sha256(
            json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return CredentialReceipt(**safe, receipt_hash=receipt_hash)

    def probe(self, *, service_id: str, alias: str) -> CredentialReceipt:
        binding = self._binding(service_id, alias)
        try:
            self._resolve(binding)
        except CredentialRequestError:
            raise
        except SecretResolutionError as exc:
            return self._receipt(
                operation="credential_probe",
                binding=binding,
                status="BLOCKED" if binding.required else "DEGRADED",
                reason_class=self._resolution_reason(exc),
            )
        return self._receipt(
            operation="credential_probe",
            binding=binding,
            status="AVAILABLE",
            reason_class="NONE",
        )

    def use(
        self,
        *,
        service_id: str,
        alias: str,
        action_id: str,
    ) -> CredentialReceipt:
        binding = self._binding(service_id, alias)
        if action_id != binding.action_id:
            raise CredentialRequestError("credential_action_not_registered_for_binding")
        adapter = self._adapters.get(binding.adapter_id)
        if adapter is None:
            raise CredentialRequestError("credential_delivery_adapter_unavailable")
        try:
            material = self._resolve(binding)
        except CredentialRequestError:
            raise
        except SecretResolutionError as exc:
            return self._receipt(
                operation="credential_use",
                binding=binding,
                status="BLOCKED" if binding.required else "DEGRADED",
                reason_class=self._resolution_reason(exc),
            )
        try:
            adapter.deliver(material, binding)
        except CredentialDeliveryError:
            return self._receipt(
                operation="credential_use",
                binding=binding,
                status="BLOCKED",
                reason_class="DELIVERY_FAILED",
            )
        return self._receipt(
            operation="credential_use",
            binding=binding,
            status="USED",
            reason_class="NONE",
        )

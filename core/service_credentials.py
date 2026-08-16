from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

from core.secret_store import SecretReference, SecretResolutionContext


_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")
_ACTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")
_ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")
_TARGET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")
_RELOAD_MODES = frozenset({"per_use", "restart"})
CATALOG_SCHEMA = "skeleton.service_credentials.v1"


class ServiceCredentialBindingError(ValueError):
    pass


def _validated(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str):
        raise ServiceCredentialBindingError(f"{field}_must_be_string")
    normalized = value.strip()
    if not pattern.fullmatch(normalized):
        raise ServiceCredentialBindingError(f"invalid_{field}")
    return normalized


@dataclass(frozen=True, slots=True)
class ServiceCredentialBinding:
    service_id: str
    alias: str
    reference: SecretReference
    context: SecretResolutionContext
    action_id: str
    adapter_id: str
    target_id: str
    required: bool = True
    reload_mode: str = "per_use"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "service_id",
            _validated(self.service_id, field="service_id", pattern=_SERVICE_ID_RE),
        )
        object.__setattr__(
            self,
            "alias",
            _validated(self.alias, field="credential_alias", pattern=_ALIAS_RE),
        )
        object.__setattr__(
            self,
            "action_id",
            _validated(self.action_id, field="action_id", pattern=_ACTION_ID_RE),
        )
        object.__setattr__(
            self,
            "adapter_id",
            _validated(self.adapter_id, field="adapter_id", pattern=_ADAPTER_ID_RE),
        )
        object.__setattr__(
            self,
            "target_id",
            _validated(self.target_id, field="target_id", pattern=_TARGET_ID_RE),
        )
        if not isinstance(self.reference, SecretReference):
            raise ServiceCredentialBindingError("typed_secret_reference_required")
        if not isinstance(self.context, SecretResolutionContext):
            raise ServiceCredentialBindingError("typed_secret_resolution_context_required")
        if not isinstance(self.required, bool):
            raise ServiceCredentialBindingError("required_must_be_boolean")
        reload_mode = self.reload_mode.strip() if isinstance(self.reload_mode, str) else ""
        if reload_mode not in _RELOAD_MODES:
            raise ServiceCredentialBindingError("invalid_reload_mode")
        object.__setattr__(self, "reload_mode", reload_mode)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ServiceCredentialBinding":
        allowed = {
            "service_id",
            "alias",
            "reference",
            "context",
            "action_id",
            "adapter_id",
            "target_id",
            "required",
            "reload_mode",
        }
        if set(value) - allowed:
            raise ServiceCredentialBindingError("service_binding_contains_unknown_fields")
        reference = value.get("reference")
        context = value.get("context")
        if not isinstance(reference, Mapping):
            raise ServiceCredentialBindingError("service_binding_reference_required")
        if not isinstance(context, Mapping):
            raise ServiceCredentialBindingError("service_binding_context_required")
        context_allowed = {"machine_identity", "audience", "task_kind"}
        if set(context) - context_allowed:
            raise ServiceCredentialBindingError("service_binding_context_contains_unknown_fields")
        try:
            typed_context = SecretResolutionContext(
                machine_identity=str(context.get("machine_identity") or ""),
                audience=str(context.get("audience") or ""),
                task_kind=str(context.get("task_kind") or ""),
            )
        except Exception as exc:
            raise ServiceCredentialBindingError("invalid_service_binding_context") from exc
        return cls(
            service_id=_validated(value.get("service_id"), field="service_id", pattern=_SERVICE_ID_RE),
            alias=_validated(value.get("alias"), field="credential_alias", pattern=_ALIAS_RE),
            reference=SecretReference.from_mapping(reference),
            context=typed_context,
            action_id=_validated(value.get("action_id"), field="action_id", pattern=_ACTION_ID_RE),
            adapter_id=_validated(value.get("adapter_id"), field="adapter_id", pattern=_ADAPTER_ID_RE),
            target_id=_validated(value.get("target_id"), field="target_id", pattern=_TARGET_ID_RE),
            required=value.get("required", True),
            reload_mode=str(value.get("reload_mode", "per_use")),
        )

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "service_id": self.service_id,
            "alias": self.alias,
            "reference": self.reference.to_mapping(),
            "context": {
                "machine_identity": self.context.machine_identity,
                "audience": self.context.audience,
                "task_kind": self.context.task_kind,
            },
            "action_id": self.action_id,
            "adapter_id": self.adapter_id,
            "target_id": self.target_id,
            "required": self.required,
            "reload_mode": self.reload_mode,
        }


class ServiceCredentialCatalog:
    def __init__(self, bindings: Sequence[ServiceCredentialBinding]) -> None:
        self._bindings: dict[tuple[str, str], ServiceCredentialBinding] = {}
        for binding in bindings:
            if not isinstance(binding, ServiceCredentialBinding):
                raise ServiceCredentialBindingError("typed_service_binding_required")
            key = (binding.service_id, binding.alias)
            if key in self._bindings:
                raise ServiceCredentialBindingError("duplicate_service_credential_binding")
            self._bindings[key] = binding

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ServiceCredentialCatalog":
        if set(value) - {"schema", "bindings"}:
            raise ServiceCredentialBindingError("service_catalog_contains_unknown_fields")
        if value.get("schema") != CATALOG_SCHEMA:
            raise ServiceCredentialBindingError("unsupported_service_credential_catalog_schema")
        bindings = value.get("bindings")
        if not isinstance(bindings, list):
            raise ServiceCredentialBindingError("service_catalog_bindings_must_be_list")
        typed: list[ServiceCredentialBinding] = []
        for binding in bindings:
            if not isinstance(binding, Mapping):
                raise ServiceCredentialBindingError("service_catalog_binding_must_be_mapping")
            typed.append(ServiceCredentialBinding.from_mapping(binding))
        return cls(typed)

    def get(self, service_id: str, alias: str) -> ServiceCredentialBinding:
        service = _validated(service_id, field="service_id", pattern=_SERVICE_ID_RE)
        credential_alias = _validated(alias, field="credential_alias", pattern=_ALIAS_RE)
        binding = self._bindings.get((service, credential_alias))
        if binding is None:
            raise ServiceCredentialBindingError("service_credential_binding_unavailable")
        return binding

    def bindings_for_service(self, service_id: str) -> tuple[ServiceCredentialBinding, ...]:
        service = _validated(service_id, field="service_id", pattern=_SERVICE_ID_RE)
        return tuple(
            binding
            for (candidate_service, _alias), binding in sorted(self._bindings.items())
            if candidate_service == service
        )

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "schema": CATALOG_SCHEMA,
            "bindings": [
                binding.to_public_mapping()
                for _key, binding in sorted(self._bindings.items())
            ],
        }

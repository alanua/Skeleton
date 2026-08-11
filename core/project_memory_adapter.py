from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_policy import MemoryGatewayPolicyError, validate_public_payload
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    canonical_dedupe_key,
    canonical_idempotency_key,
    stable_hash,
)


PROJECT_MEMORY_REQUEST_SCHEMA = "skeleton.project_memory.request.v1"
PROJECT_MEMORY_RECEIPT_SCHEMA = "skeleton.project_memory.receipt.v1"
PROJECT_MEMORY_CONTRACT_VERSION = "1.0.0"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PUBLIC_CLASSES = frozenset({"CANON", "REVIEW", "BACKLOG", "REJECTED", "TEMPORARY"})
_PRIVATE_RUNTIME_CLASS = "PRIVATE_RUNTIME_ONLY"


class ProjectMemoryClass(StrEnum):
    CANON = "CANON"
    REVIEW = "REVIEW"
    BACKLOG = "BACKLOG"
    REJECTED = "REJECTED"
    PRIVATE = "PRIVATE"
    TEMPORARY = "TEMPORARY"


class ProjectMemoryAdapterError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProjectMemoryBinding:
    namespace: str
    project_id: str
    capability_class: str = "PUBLIC_SAFE"
    dataset_id: str = "default"
    actor_ref: str = "operator"
    approval_ref: str = "project-memory-adapter"


def project_memory_binding(
    *,
    namespace: str = "skeleton",
    project_id: str = "skeleton",
    capability_class: str = "PUBLIC_SAFE",
    dataset_id: str = "default",
    actor_ref: str = "operator",
    approval_ref: str = "project-memory-adapter",
) -> ProjectMemoryBinding:
    return ProjectMemoryBinding(
        namespace=_safe_id(namespace, "namespace"),
        project_id=_safe_id(project_id, "project_id"),
        capability_class=_capability_class(capability_class),
        dataset_id=_safe_id(dataset_id, "dataset_id"),
        actor_ref=_safe_id(actor_ref, "actor_ref"),
        approval_ref=_safe_id(approval_ref, "approval_ref"),
    )


class ProjectMemoryGatewayAdapter:
    """Universal project-memory facade backed only by MemoryGateway."""

    def __init__(self, gateway: MemoryGateway, binding: ProjectMemoryBinding | Mapping[str, Any]) -> None:
        self.gateway = gateway
        self.binding = _normalize_binding(binding)

    def read_context(self, request: Mapping[str, Any]) -> dict[str, object]:
        normalized = self._request(request, expected_operation="read_context")
        memory_class = normalized["memory_class"]
        if memory_class == ProjectMemoryClass.PRIVATE:
            self._require_private_runtime(normalized)
            canonical_ref = str(normalized.get("canonical_ref") or normalized.get("key") or "")
            response = self._gateway_execute(
                "memory.private_read_exact",
                {
                    "project_id": self.binding.project_id,
                    "dataset_id": normalized["dataset_id"],
                    "canonical_ref": canonical_ref,
                },
            )
            return self._receipt(normalized, response["payload"], private_payload=True)
        if memory_class == ProjectMemoryClass.CANON:
            response = self._gateway_execute(
                "memory.lookup_exact",
                {"project_id": self.binding.project_id, "key": normalized["key"]},
            )
            return self._receipt(normalized, response["payload"])
        response = self._gateway_execute(
            "memory.get_index_freshness",
            {"project_id": self.binding.project_id},
        )
        return self._receipt(
            normalized,
            {
                "status": "READ_CONTEXT_AVAILABLE",
                "memory_class": memory_class.value,
                "project_id": self.binding.project_id,
                "freshness": response["payload"],
            },
        )

    def propose_fact(self, request: Mapping[str, Any]) -> dict[str, object]:
        return self._propose(request, expected_operation="propose_fact", fact_type="fact")

    def propose_task(self, request: Mapping[str, Any]) -> dict[str, object]:
        return self._propose(request, expected_operation="propose_task", fact_type="task")

    def propose_preference(self, request: Mapping[str, Any]) -> dict[str, object]:
        return self._propose(request, expected_operation="propose_preference", fact_type="preference")

    def list_pending_review(self, request: Mapping[str, Any]) -> dict[str, object]:
        normalized = self._request(request, expected_operation="list_pending_review")
        response = self._gateway_execute("memory.get_conflicts", {"project_id": self.binding.project_id})
        payload = response["payload"]
        return self._receipt(
            normalized,
            {
                "status": "PENDING_REVIEW_LISTED",
                "project_id": self.binding.project_id,
                "memory_classes": ["REVIEW", "BACKLOG"],
                "conflict_count": len(payload.get("conflicts", [])) if isinstance(payload.get("conflicts"), list) else 0,
                "conflicts": payload.get("conflicts", []),
            },
        )

    def _propose(
        self,
        request: Mapping[str, Any],
        *,
        expected_operation: str,
        fact_type: str,
    ) -> dict[str, object]:
        normalized = self._request(request, expected_operation=expected_operation)
        memory_class = normalized["memory_class"]
        if memory_class == ProjectMemoryClass.PRIVATE:
            self._require_private_runtime(normalized)
            mutation = self._private_mutation(normalized, fact_type=fact_type)
            response = self._gateway_execute("memory.private_mutate", mutation)
            return self._receipt(normalized, response["payload"])
        if memory_class == ProjectMemoryClass.CANON:
            proposal = self._canonical_patch_proposal(normalized, fact_type=fact_type)
            response = self._gateway_execute("memory.propose_patch", {"project_id": self.binding.project_id, "proposal": proposal})
            payload = deepcopy(response["payload"])
            payload["canonical_write_performed"] = False
            payload["operator_approval_required"] = True
            return self._receipt(normalized, payload)
        payload = {
            "status": "PENDING_REVIEW",
            "project_id": self.binding.project_id,
            "memory_class": memory_class.value,
            "canonical_write_performed": False,
            "operator_approval_required": memory_class in {ProjectMemoryClass.REVIEW, ProjectMemoryClass.BACKLOG},
            "proposal_status": "QUEUED_FOR_OPERATOR_REVIEW",
            "idempotency_key": normalized["idempotency_key"],
            "value_hash": stable_hash(normalized.get("value")),
        }
        return self._receipt(normalized, payload)

    def _request(self, request: Mapping[str, Any], *, expected_operation: str) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_REQUEST", "request must be an object")
        if request.get("schema") != PROJECT_MEMORY_REQUEST_SCHEMA:
            raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_SCHEMA", "request schema is invalid")
        operation = _safe_id(request.get("operation"), "operation")
        if operation != expected_operation:
            raise ProjectMemoryAdapterError("PROJECT_MEMORY_OPERATION_MISMATCH", "request operation mismatch")
        request_project_id = _safe_id(request.get("project_id", self.binding.project_id), "project_id")
        if request_project_id != self.binding.project_id:
            raise ProjectMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "request project_id does not match binding")
        memory_class = _memory_class(request.get("memory_class"))
        dataset_id = _safe_id(request.get("dataset_id", self.binding.dataset_id), "dataset_id")
        capability_class = _capability_class(request.get("capability_class", "PUBLIC_SAFE"))
        key = request.get("key")
        normalized: dict[str, Any] = {
            "schema": PROJECT_MEMORY_REQUEST_SCHEMA,
            "operation": operation,
            "project_id": request_project_id,
            "dataset_id": dataset_id,
            "memory_class": memory_class,
            "capability_class": capability_class,
            "key": _safe_id(key, "key") if key is not None else "primary_fact",
            "value": deepcopy(request.get("value")),
            "idempotency_key": _safe_id(request.get("idempotency_key", f"{operation}-{request_project_id}-{memory_class.value}"), "idempotency_key"),
            "reason_code": _safe_id(request.get("reason_code", operation), "reason_code"),
            "actor_ref": _safe_id(request.get("actor_ref", self.binding.actor_ref), "actor_ref"),
            "approval_ref": _safe_id(request.get("approval_ref", self.binding.approval_ref), "approval_ref"),
        }
        if request.get("canonical_ref") is not None:
            normalized["canonical_ref"] = _safe_canonical_ref(request.get("canonical_ref"))
        json.dumps(normalized, allow_nan=False, sort_keys=True, default=str)
        return normalized

    def _canonical_patch_proposal(self, request: Mapping[str, Any], *, fact_type: str) -> dict[str, object]:
        exact = self._gateway_execute(
            "memory.lookup_exact",
            {"project_id": self.binding.project_id, "key": request["key"]},
        )["payload"]
        provenance_refs = exact.get("provenance_refs")
        if not isinstance(provenance_refs, list) or not provenance_refs or not isinstance(provenance_refs[0], Mapping):
            raise ProjectMemoryAdapterError("CANON_EXACT_CONFIRMATION_REQUIRED", "canonical proposal requires exact provenance")
        exact_ref = provenance_refs[0]
        source_hash = str(exact_ref.get("evidence_hash", ""))
        proposal: dict[str, object] = {
            "schema": PATCH_PROPOSAL_SCHEMA,
            "namespace": self.binding.namespace,
            "project_id": self.binding.project_id,
            "object_id": str(request["key"]),
            "entity_scope": "project_memory",
            "fact_type": fact_type,
            "normalized_target": str(request["key"]),
            "source_evidence_hash": source_hash,
            "proposed_value": deepcopy(request.get("value")),
            "provenance_refs": [dict(exact_ref)],
            "actor_ref": request["actor_ref"],
            "reason_code": request["reason_code"],
            "approval_tier": "operator",
            "approval_ref": request["approval_ref"],
            "confirmed_via_exact_ref": str(exact_ref.get("ref")),
            "confirmed_canonical_revision": exact.get("canonical_revision"),
        }
        proposal["dedupe_key"] = canonical_dedupe_key(proposal)
        proposal["idempotency_key"] = canonical_idempotency_key({**proposal, "adapter_idempotency_key": request["idempotency_key"]})
        return proposal

    def _private_mutation(self, request: Mapping[str, Any], *, fact_type: str) -> dict[str, object]:
        fact_id = str(request["key"])
        value = deepcopy(request.get("value"))
        return {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "project_id": self.binding.project_id,
            "dataset_id": request["dataset_id"],
            "operation": "put",
            "fact_namespace": f"project_memory.{fact_type}",
            "fact_id": fact_id,
            "value": value,
            "actor_ref": request["actor_ref"],
            "reason_code": request["reason_code"],
            "approval_ref": request["approval_ref"],
            "idempotency_key": request["idempotency_key"],
        }

    def _gateway_execute(self, suffix: str, payload: Mapping[str, object]) -> dict[str, object]:
        return self.gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": self.binding.namespace,
                "command": f"{self.binding.namespace}.{suffix}",
                "payload": dict(payload),
            }
        )

    def _require_private_runtime(self, request: Mapping[str, Any]) -> None:
        if self.binding.capability_class != _PRIVATE_RUNTIME_CLASS:
            raise ProjectMemoryAdapterError("PRIVATE_BINDING_REQUIRED", "PRIVATE requires private-runtime binding")
        if request["capability_class"] != _PRIVATE_RUNTIME_CLASS:
            raise ProjectMemoryAdapterError("PRIVATE_REQUEST_CAPABILITY_REQUIRED", "PRIVATE requires private-runtime request capability")
        if not getattr(self.gateway, "private_runtime_capable", False):
            raise ProjectMemoryAdapterError("PRIVATE_GATEWAY_CAPABILITY_REQUIRED", "PRIVATE requires private-runtime MemoryGateway capability")

    def _receipt(
        self,
        request: Mapping[str, Any],
        payload: Mapping[str, object],
        *,
        private_payload: bool = False,
    ) -> dict[str, object]:
        public_payload = deepcopy(payload) if private_payload else validate_public_payload(payload)
        receipt = {
            "schema": PROJECT_MEMORY_RECEIPT_SCHEMA,
            "contract_version": PROJECT_MEMORY_CONTRACT_VERSION,
            "project_id": self.binding.project_id,
            "dataset_id": request["dataset_id"],
            "operation": request["operation"],
            "memory_class": request["memory_class"].value,
            "capability_class": request["capability_class"],
            "gateway": "MemoryGateway",
            "payload": public_payload,
        }
        json.dumps(receipt, allow_nan=False, sort_keys=True)
        return receipt


def _normalize_binding(binding: ProjectMemoryBinding | Mapping[str, Any]) -> ProjectMemoryBinding:
    if isinstance(binding, ProjectMemoryBinding):
        return project_memory_binding(
            namespace=binding.namespace,
            project_id=binding.project_id,
            capability_class=binding.capability_class,
            dataset_id=binding.dataset_id,
            actor_ref=binding.actor_ref,
            approval_ref=binding.approval_ref,
        )
    if not isinstance(binding, Mapping):
        raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_BINDING", "binding must be an object")
    return project_memory_binding(
        namespace=str(binding.get("namespace", "skeleton")),
        project_id=str(binding.get("project_id", "skeleton")),
        capability_class=str(binding.get("capability_class", "PUBLIC_SAFE")),
        dataset_id=str(binding.get("dataset_id", "default")),
        actor_ref=str(binding.get("actor_ref", "operator")),
        approval_ref=str(binding.get("approval_ref", "project-memory-adapter")),
    )


def _memory_class(value: object) -> ProjectMemoryClass:
    try:
        return ProjectMemoryClass(str(value))
    except ValueError as exc:
        raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_CLASS", "memory_class is invalid") from exc


def _capability_class(value: object) -> str:
    candidate = str(value)
    if candidate in _PUBLIC_CLASSES or candidate == "PUBLIC_SAFE":
        return "PUBLIC_SAFE"
    if candidate == _PRIVATE_RUNTIME_CLASS:
        return candidate
    raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_CAPABILITY", "capability_class is invalid")


def _safe_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_FIELD", f"{field} is malformed")
    return value


def _safe_canonical_ref(value: object) -> str:
    if not isinstance(value, str) or ":" not in value:
        raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_FIELD", "canonical_ref is malformed")
    namespace, fact_id = value.split(":", 1)
    return f"{_safe_id(namespace, 'canonical_ref namespace')}:{_safe_id(fact_id, 'canonical_ref fact_id')}"


__all__ = [
    "PROJECT_MEMORY_CONTRACT_VERSION",
    "PROJECT_MEMORY_RECEIPT_SCHEMA",
    "PROJECT_MEMORY_REQUEST_SCHEMA",
    "ProjectMemoryAdapterError",
    "ProjectMemoryBinding",
    "ProjectMemoryClass",
    "ProjectMemoryGatewayAdapter",
    "project_memory_binding",
]

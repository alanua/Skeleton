from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_policy import command_name
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


PROJECT_MEMORY_REQUEST_SCHEMA = "skeleton.project_memory_request.v1"
PROJECT_MEMORY_RECEIPT_SCHEMA = "skeleton.project_memory_receipt.v1"
PROJECT_MEMORY_CONTRACT_VERSION = "1.0.0"

PROJECT_MEMORY_OPERATIONS = frozenset(
    {
        "read_context",
        "propose_fact",
        "propose_task",
        "propose_preference",
        "list_pending_review",
    }
)
PROJECT_MEMORY_CLASSIFICATIONS = frozenset(
    {"CANON", "REVIEW", "BACKLOG", "REJECTED", "PRIVATE", "TEMPORARY"}
)
PROJECT_MEMORY_PRIVACY_BOUNDARIES = frozenset(
    {
        "PUBLIC_SAFE_CODE_TESTS_ONLY",
        "PRIVATE_RUNTIME_ONLY",
        "SECRET_REFERENCE_ONLY",
    }
)
_PROPOSAL_OPERATIONS = frozenset({"propose_fact", "propose_task", "propose_preference"})
_DURABLE_PROPOSAL_CLASSIFICATIONS = frozenset({"CANON", "REVIEW", "BACKLOG"})
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PRIVATE_MARKERS = (
    "budget",
    "credential",
    "document",
    "drive",
    "gmail",
    "identifier",
    "invoice",
    "passport",
    "password",
    "private",
    "secret",
    "sqlite",
    "tax",
    "ticket",
    "token",
    "watchlist",
)


class ProjectMemoryAdapterError(ValueError):
    """Raised when a project memory request fails before gateway execution."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProjectMemoryBinding:
    project_id: str
    namespace: str
    privacy_boundary: str


class ProjectMemoryAdapter:
    """Universal project-to-MemoryGateway adapter with public-safe receipts."""

    def __init__(
        self,
        *,
        gateway: MemoryGateway,
        project_id: str,
        namespace: str,
        privacy_boundary: str = "PUBLIC_SAFE_CODE_TESTS_ONLY",
    ) -> None:
        self._gateway = gateway
        self._binding = ProjectMemoryBinding(
            project_id=_safe_token(project_id, "project_id"),
            namespace=_safe_token(namespace, "namespace"),
            privacy_boundary=_privacy_boundary(privacy_boundary),
        )

    @property
    def binding(self) -> ProjectMemoryBinding:
        return self._binding

    def run(self, request: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(request, Mapping):
            raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_REQUEST", "request must be an object")
        if request.get("schema") != PROJECT_MEMORY_REQUEST_SCHEMA:
            raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_REQUEST", "request schema is invalid")

        namespace = _safe_token(request.get("namespace"), "namespace")
        project_id = _safe_token(request.get("project_id"), "project_id")
        privacy_boundary = _privacy_boundary(request.get("privacy_boundary"))
        if namespace != self._binding.namespace or project_id != self._binding.project_id:
            raise ProjectMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "request binding mismatch")
        if privacy_boundary != self._binding.privacy_boundary:
            raise ProjectMemoryAdapterError("PRIVACY_BOUNDARY_MISMATCH", "request privacy boundary mismatch")

        operation = request.get("operation")
        if operation not in PROJECT_MEMORY_OPERATIONS:
            raise ProjectMemoryAdapterError("OPERATION_NOT_ALLOWLISTED", "operation is not allowlisted")
        classification = _classification(request.get("classification"))
        evidence = _evidence(request.get("evidence"))
        parameters = request.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_REQUEST", "parameters must be an object")

        if operation == "read_context":
            return self._read_context(parameters=parameters, classification=classification, evidence=evidence)
        if operation == "list_pending_review":
            return self._list_pending_review(classification=classification, evidence=evidence)
        return self._propose(
            operation=str(operation),
            parameters=parameters,
            classification=classification,
            evidence=evidence,
        )

    def _read_context(
        self,
        *,
        parameters: Mapping[str, Any],
        classification: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, object]:
        key = _safe_token(parameters.get("key"), "key")
        gateway_response = self._gateway_execute("memory.lookup_exact", {"project_id": self._binding.project_id, "key": key})
        payload = gateway_response.get("payload", {})
        if not isinstance(payload, Mapping):
            raise ProjectMemoryAdapterError("INVALID_GATEWAY_RESPONSE", "gateway payload is invalid")
        return _receipt(
            binding=self._binding,
            operation="read_context",
            classification=classification,
            evidence=evidence,
            status="DONE",
            reason_code="CONTEXT_READ_THROUGH_GATEWAY",
            gateway_response=gateway_response,
            aggregates={
                "context_count": 1,
                "pending_review_count": 0,
                "gateway_mutation_count": 0,
            },
            payload={
                "authoritative": payload.get("authoritative"),
                "authority_classification": payload.get("authority_classification"),
                "source_kind": payload.get("source_kind"),
                "canonical_ref": payload.get("canonical_ref"),
                "canonical_revision": payload.get("canonical_revision"),
            },
        )

    def _list_pending_review(
        self,
        *,
        classification: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, object]:
        conflicts_response = self._gateway_execute("memory.get_conflicts", {"project_id": self._binding.project_id})
        audit_response = self._gateway_execute("memory.get_audit_log", {"project_id": self._binding.project_id})
        conflicts = _payload_list(conflicts_response, "conflicts")
        events = _payload_list(audit_response, "events")
        return _receipt(
            binding=self._binding,
            operation="list_pending_review",
            classification=classification,
            evidence=evidence,
            status="DONE",
            reason_code="PENDING_REVIEW_LISTED_THROUGH_GATEWAY",
            gateway_response=audit_response,
            aggregates={
                "context_count": 0,
                "pending_review_count": len(conflicts) + len(events),
                "conflict_count": len(conflicts),
                "audit_event_count": len(events),
                "gateway_mutation_count": 0,
            },
            payload={
                "conflict_count": len(conflicts),
                "audit_event_count": len(events),
            },
        )

    def _propose(
        self,
        *,
        operation: str,
        parameters: Mapping[str, Any],
        classification: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, object]:
        if classification == "PRIVATE":
            if self._binding.privacy_boundary != "PRIVATE_RUNTIME_ONLY":
                return _blocked_receipt(
                    binding=self._binding,
                    operation=operation,
                    classification=classification,
                    evidence=evidence,
                    reason_code="PRIVATE_RECORD_REQUIRES_PRIVATE_RUNTIME_BOUNDARY",
                )
        if classification == "REJECTED":
            return _blocked_receipt(
                binding=self._binding,
                operation=operation,
                classification=classification,
                evidence=evidence,
                reason_code="CANDIDATE_CLASSIFIED_REJECTED",
            )
        if classification == "TEMPORARY":
            return _receipt(
                binding=self._binding,
                operation=operation,
                classification=classification,
                evidence=evidence,
                status="TEMPORARY_ONLY",
                reason_code="TEMPORARY_RECORD_NOT_DURABLE",
                gateway_response=None,
                aggregates={"proposal_count": 0, "gateway_mutation_count": 0},
                payload={},
            )
        if classification not in _DURABLE_PROPOSAL_CLASSIFICATIONS and classification != "PRIVATE":
            raise ProjectMemoryAdapterError("CLASSIFICATION_NOT_SUPPORTED", "classification is not proposal-capable")
        if classification != "PRIVATE":
            _reject_private_markers(parameters)

        proposal = _proposal_payload(
            binding=self._binding,
            operation=operation,
            parameters=parameters,
            evidence=evidence,
            classification=classification,
        )
        gateway_response = self._gateway_execute(
            "memory.propose_patch",
            {"project_id": self._binding.project_id, "proposal": proposal},
        )
        payload = gateway_response.get("payload", {})
        event = payload.get("proposal_event") if isinstance(payload, Mapping) else None
        status = "OPERATOR_APPROVAL_REQUIRED"
        reason_code = "CANON_CHANGE_PROPOSAL_REQUIRES_OPERATOR_APPROVAL"
        if isinstance(payload, Mapping) and payload.get("idempotency_classification") == "DUPLICATE_EXISTING":
            status = "DUPLICATE_EXISTING"
            reason_code = "PROPOSAL_ALREADY_PENDING_REVIEW"
        return _receipt(
            binding=self._binding,
            operation=operation,
            classification=classification,
            evidence=evidence,
            status=status,
            reason_code=reason_code,
            gateway_response=gateway_response,
            aggregates={
                "proposal_count": 1,
                "pending_review_count": 1,
                "gateway_mutation_count": 0,
                "canonical_write_count": 0,
            },
            payload={
                "proposal_status": event.get("status") if isinstance(event, Mapping) else None,
                "event_ref": event.get("event_ref") if isinstance(event, Mapping) else None,
                "idempotency_classification": (
                    payload.get("idempotency_classification") if isinstance(payload, Mapping) else None
                ),
                "operator_approval_required": True,
                "canonical_write_performed": False,
            },
        )

    def _gateway_execute(self, suffix: str, payload: Mapping[str, object]) -> dict[str, object]:
        return self._gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": self._binding.namespace,
                "command": command_name(self._binding.namespace, suffix),
                "payload": dict(payload),
            }
        )


def blocked_result(
    reason_code: str,
    *,
    namespace: object = None,
    project_id: object = None,
    privacy_boundary: object = None,
) -> dict[str, object]:
    result = {
        "schema": PROJECT_MEMORY_RECEIPT_SCHEMA,
        "contract_version": PROJECT_MEMORY_CONTRACT_VERSION,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "namespace": namespace if isinstance(namespace, str) else None,
        "project_id": project_id if isinstance(project_id, str) else None,
        "privacy_boundary": privacy_boundary if isinstance(privacy_boundary, str) else None,
        "operation": None,
        "classification": None,
        "aggregates": {"gateway_mutation_count": 0},
        "payload": {},
    }
    return _sanitize_receipt(result)


def _proposal_payload(
    *,
    binding: ProjectMemoryBinding,
    operation: str,
    parameters: Mapping[str, Any],
    evidence: Mapping[str, Any],
    classification: str,
) -> dict[str, object]:
    proposed_value = parameters.get("proposed_value")
    _strict_json(proposed_value, "INVALID_PROPOSED_VALUE")
    proposal: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": binding.namespace,
        "project_id": binding.project_id,
        "object_id": _safe_token(parameters.get("object_id"), "object_id"),
        "entity_scope": _safe_token(parameters.get("entity_scope", "project"), "entity_scope"),
        "fact_type": _fact_type(operation, parameters),
        "normalized_target": _safe_token(parameters.get("normalized_target"), "normalized_target"),
        "source_evidence_hash": _safe_hash(evidence.get("source_evidence_hash")),
        "proposed_value": proposed_value,
        "provenance_refs": _provenance_refs(evidence.get("provenance_refs")),
        "actor_ref": _safe_token(evidence.get("actor_ref"), "actor_ref"),
        "reason_code": _proposal_reason_code(operation, classification, evidence),
        "approval_tier": _safe_token(evidence.get("approval_tier", "operator"), "approval_tier"),
        "approval_ref": _safe_token(evidence.get("approval_ref"), "approval_ref"),
        "confirmed_via_exact_ref": _safe_token(evidence.get("confirmed_via_exact_ref"), "confirmed_via_exact_ref"),
        "confirmed_canonical_revision": _revision(evidence.get("confirmed_canonical_revision")),
        "classification": classification,
    }
    proposal["dedupe_key"] = canonical_dedupe_key(proposal)
    proposal["idempotency_key"] = canonical_idempotency_key(proposal)
    return proposal


def _fact_type(operation: str, parameters: Mapping[str, Any]) -> str:
    default = {
        "propose_fact": "project_fact",
        "propose_task": "project_task",
        "propose_preference": "project_preference",
    }[operation]
    return _safe_token(parameters.get("fact_type", default), "fact_type")


def _proposal_reason_code(operation: str, classification: str, evidence: Mapping[str, Any]) -> str:
    raw = evidence.get("reason_code", f"{operation.lower()}_{classification.lower()}_proposal")
    return _safe_token(raw, "reason_code")


def _receipt(
    *,
    binding: ProjectMemoryBinding,
    operation: str,
    classification: str,
    evidence: Mapping[str, Any],
    status: str,
    reason_code: str,
    gateway_response: Mapping[str, Any] | None,
    aggregates: Mapping[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    gateway = None
    if gateway_response is not None:
        gateway = {
            "schema": gateway_response.get("schema"),
            "command": gateway_response.get("command"),
            "contract_version": gateway_response.get("contract_version"),
        }
    result = {
        "schema": PROJECT_MEMORY_RECEIPT_SCHEMA,
        "contract_version": PROJECT_MEMORY_CONTRACT_VERSION,
        "status": status,
        "reason_code": _safe_token(reason_code, "reason_code"),
        "namespace": binding.namespace,
        "project_id": binding.project_id,
        "operation": operation,
        "classification": classification,
        "privacy_boundary": binding.privacy_boundary,
        "evidence": _evidence_summary(evidence),
        "gateway": gateway,
        "aggregates": dict(aggregates),
        "payload": dict(payload),
    }
    return _sanitize_receipt(result)


def _blocked_receipt(
    *,
    binding: ProjectMemoryBinding,
    operation: str,
    classification: str,
    evidence: Mapping[str, Any],
    reason_code: str,
) -> dict[str, object]:
    return _receipt(
        binding=binding,
        operation=operation,
        classification=classification,
        evidence=evidence,
        status="BLOCKED",
        reason_code=reason_code,
        gateway_response=None,
        aggregates={"proposal_count": 0, "gateway_mutation_count": 0},
        payload={},
    )


def _sanitize_receipt(result: Mapping[str, object]) -> dict[str, object]:
    _strict_json(result, "INVALID_PROJECT_MEMORY_RECEIPT")
    sanitized = _public_project_value(result)
    if not isinstance(sanitized, dict):
        raise ProjectMemoryAdapterError("INVALID_PROJECT_MEMORY_RECEIPT", "receipt must be an object")
    return sanitized


def _public_project_value(value: object) -> object:
    if isinstance(value, Mapping):
        allowed = {
            "schema",
            "contract_version",
            "status",
            "reason_code",
            "namespace",
            "project_id",
            "operation",
            "classification",
            "privacy_boundary",
            "evidence",
            "gateway",
            "aggregates",
            "payload",
            "source_evidence_hash",
            "provenance_refs",
            "actor_ref",
            "approval_ref",
            "command",
            "context_count",
            "pending_review_count",
            "gateway_mutation_count",
            "conflict_count",
            "audit_event_count",
            "proposal_count",
            "canonical_write_count",
            "authoritative",
            "authority_classification",
            "source_kind",
            "canonical_ref",
            "canonical_revision",
            "proposal_status",
            "event_ref",
            "idempotency_classification",
            "operator_approval_required",
            "canonical_write_performed",
            "ref",
            "kind",
            "evidence_hash",
        }
        return {
            key: _public_project_value(child)
            for key, child in value.items()
            if key in allowed and child is not None
        }
    if isinstance(value, list):
        return [_public_project_value(child) for child in value]
    return value


def _evidence(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ProjectMemoryAdapterError("EVIDENCE_REQUIRED", "evidence must be an object")
    return dict(value)


def _evidence_summary(evidence: Mapping[str, Any]) -> dict[str, object]:
    provenance_refs = _provenance_refs(evidence.get("provenance_refs"))
    return {
        "source_evidence_hash": _safe_hash(evidence.get("source_evidence_hash")),
        "provenance_refs": provenance_refs,
        "actor_ref": _safe_token(evidence.get("actor_ref"), "actor_ref"),
        "approval_ref": (
            _safe_token(evidence.get("approval_ref"), "approval_ref")
            if evidence.get("approval_ref") is not None
            else None
        ),
        "reason_code": (
            _safe_token(evidence.get("reason_code"), "reason_code")
            if evidence.get("reason_code") is not None
            else None
        ),
    }


def _provenance_refs(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ProjectMemoryAdapterError("EVIDENCE_REQUIRED", "provenance_refs are required")
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ProjectMemoryAdapterError("EVIDENCE_REQUIRED", "provenance ref must be an object")
        result.append(
            {
                "ref": _safe_token(item.get("ref"), "provenance_ref"),
                "kind": _safe_token(item.get("kind"), "provenance_kind"),
                "evidence_hash": _safe_hash(item.get("evidence_hash")),
            }
        )
    return result


def _payload_list(gateway_response: Mapping[str, Any], key: str) -> list[object]:
    payload = gateway_response.get("payload")
    if not isinstance(payload, Mapping):
        return []
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _classification(value: object) -> str:
    if value not in PROJECT_MEMORY_CLASSIFICATIONS:
        raise ProjectMemoryAdapterError("CLASSIFICATION_REQUIRED", "classification is mandatory")
    return str(value)


def _privacy_boundary(value: object) -> str:
    if value not in PROJECT_MEMORY_PRIVACY_BOUNDARIES:
        raise ProjectMemoryAdapterError("PRIVACY_BOUNDARY_REQUIRED", "privacy boundary is mandatory")
    return str(value)


def _safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise ProjectMemoryAdapterError(f"{name.upper()}_REQUIRED", f"{name} is mandatory")
    return value


def _safe_hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProjectMemoryAdapterError("SOURCE_EVIDENCE_HASH_REQUIRED", "source evidence hash is mandatory")
    return value


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectMemoryAdapterError("CONFIRMED_REVISION_REQUIRED", "confirmed canonical revision is mandatory")
    return value


def _strict_json(value: object, reason_code: str) -> None:
    try:
        json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ProjectMemoryAdapterError(reason_code, "value must be strict JSON") from exc


def _reject_private_markers(parameters: Mapping[str, Any]) -> None:
    serialized = json.dumps(parameters, allow_nan=False, sort_keys=True).casefold()
    if any(marker in serialized for marker in _PRIVATE_MARKERS):
        raise ProjectMemoryAdapterError(
            "PRIVATE_MARKER_NOT_PUBLIC_SAFE",
            "public project memory proposals cannot contain private markers",
        )

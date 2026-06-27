from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from core.memory_gateway import (
    MEMORY_GATEWAY_REQUEST_SCHEMA,
    MemoryGateway,
    capability_token,
)
from core.memory_gateway_policy import (
    ALLOWED_NAMESPACES,
    MemoryGatewayPolicyError,
    command_name,
    validate_namespace,
    validate_public_payload,
)
from core.memory_patch_proposal import (
    MemoryPatchProposalIdempotencyError,
    MemoryPatchProposalValidationError,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


HERMES_MEMORY_REQUEST_SCHEMA = "skeleton.hermes_memory.request.v1"
HERMES_MEMORY_RESULT_SCHEMA = "skeleton.hermes_memory.result.v1"

HERMES_MEMORY_CAPABILITIES = frozenset(
    {
        "memory.lookup_exact",
        "memory.get_conflicts",
        "memory.get_override_history",
        "memory.get_audit_log",
        "memory.get_index_freshness",
        "memory.propose_patch",
    }
)

WRITE_GATE_OUTCOMES = frozenset(
    {
        "APPROVED_FOR_OPERATOR",
        "REVIEW_REQUIRED",
        "BLOCKED",
        "DUPLICATE_EXISTING",
    }
)

_FORBIDDEN_DIRECT_STORAGE_MARKERS = (
    "sqlite",
    "graphify",
    "mempalace",
    "filesystem",
    "local_registry",
    "local_path",
    "raw_path",
    "storage_api",
)


class HermesMemoryAdapterError(ValueError):
    """Raised when Hermes memory access violates the gateway-only contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class HermesMemoryAdapter:
    """Hermes-facing policy layer over the namespaced Memory Gateway."""

    def __init__(self, gateway: MemoryGateway | None = None) -> None:
        self._gateway = gateway
        self._seen_proposal_events: set[str] = set()

    def execute(self, request: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(request, Mapping):
            raise HermesMemoryAdapterError("INVALID_REQUEST", "request must be an object")
        if request.get("schema") != HERMES_MEMORY_REQUEST_SCHEMA:
            raise HermesMemoryAdapterError("INVALID_REQUEST_SCHEMA", "request schema is invalid")
        _reject_direct_storage_markers(request)

        namespace = validate_namespace(
            request.get("namespace"),
            allowed_namespaces=ALLOWED_NAMESPACES,
        )
        project_id = _safe_project_id(request.get("project_id"))
        if project_id != namespace:
            raise HermesMemoryAdapterError("PROJECT_NAMESPACE_MISMATCH", "project must match bound namespace")

        capability = request.get("capability")
        if not isinstance(capability, str) or capability not in HERMES_MEMORY_CAPABILITIES:
            raise HermesMemoryAdapterError("CAPABILITY_NOT_ALLOWED", "Hermes memory capability is not allowed")

        payload = request.get("payload", {})
        if not isinstance(payload, Mapping):
            raise HermesMemoryAdapterError("INVALID_REQUEST", "payload must be an object")
        if payload.get("namespace") not in {None, namespace} or payload.get("project_id") not in {None, project_id}:
            raise HermesMemoryAdapterError("CROSS_PROJECT_REQUEST_BLOCKED", "payload crosses bound project")

        gateway = self._gateway or MemoryGateway(capability_token(namespaces=(namespace,), public_mode=True))
        gateway_payload = _gateway_payload(capability, namespace, project_id, payload)
        response = _gateway_execute(
            gateway,
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": namespace,
                "command": command_name(namespace, capability),
                "payload": gateway_payload,
            },
        )
        result_payload = _sanitize_gateway_payload(capability, response["payload"])
        write_gate = _write_gate_result(capability, result_payload, self._seen_proposal_events)
        result = {
            "schema": HERMES_MEMORY_RESULT_SCHEMA,
            "namespace": namespace,
            "project_id": project_id,
            "capability": capability,
            "authoritative": bool(result_payload.get("authoritative", False)),
            "canonical_write_performed": False,
            "write_gate": write_gate,
            "payload": result_payload,
        }
        return validate_public_payload(result)


def run_hermes_memory_request(
    request: Mapping[str, Any],
    *,
    gateway: MemoryGateway | None = None,
) -> dict[str, object]:
    return HermesMemoryAdapter(gateway=gateway).execute(request)


def _gateway_execute(gateway: MemoryGateway, request: Mapping[str, Any]) -> dict[str, object]:
    try:
        return gateway.execute(request)
    except MemoryGatewayPolicyError as exc:
        raise HermesMemoryAdapterError(exc.reason_code, str(exc)) from exc
    except MemoryPatchProposalValidationError as exc:
        raise HermesMemoryAdapterError("PATCH_PROPOSAL_BLOCKED", str(exc)) from exc
    except MemoryPatchProposalIdempotencyError as exc:
        raise HermesMemoryAdapterError("IDEMPOTENCY_CONFLICT_BLOCKED", str(exc)) from exc


def _gateway_payload(
    capability: str,
    namespace: str,
    project_id: str,
    payload: Mapping[str, Any],
) -> dict[str, object]:
    if capability == "memory.lookup_exact":
        key = payload.get("key")
        if not isinstance(key, str):
            raise HermesMemoryAdapterError("INVALID_LOOKUP_KEY", "lookup key is required")
        return {"key": key}
    if capability == "memory.get_override_history":
        override_ref = payload.get("override_ref")
        if not isinstance(override_ref, str):
            raise HermesMemoryAdapterError("INVALID_OVERRIDE_REF", "override ref is required")
        return {"override_ref": override_ref}
    if capability == "memory.propose_patch":
        proposal = payload.get("proposal")
        if not isinstance(proposal, Mapping):
            raise HermesMemoryAdapterError("INVALID_PATCH_PROPOSAL", "proposal is required")
        _validate_hermes_proposal(namespace, project_id, proposal)
        return {"proposal": deepcopy(dict(proposal))}
    return {}


def _validate_hermes_proposal(namespace: str, project_id: str, proposal: Mapping[str, Any]) -> None:
    if proposal.get("namespace") != namespace or proposal.get("project_id") != project_id:
        raise HermesMemoryAdapterError("CROSS_PROJECT_REQUEST_BLOCKED", "proposal crosses bound project")
    approval_tier = proposal.get("approval_tier")
    if approval_tier == "override_operator" and proposal.get("override_intent") is not True:
        raise HermesMemoryAdapterError("OVERRIDE_INTENT_REQUIRED", "override proposal requires explicit intent")
    if proposal.get("override_intent") is True and approval_tier != "override_operator":
        raise HermesMemoryAdapterError("OVERRIDE_APPROVAL_TIER_REQUIRED", "override approval tier is distinct")
    try:
        if proposal.get("dedupe_key") != canonical_dedupe_key(proposal):
            raise HermesMemoryAdapterError("DETERMINISTIC_KEYS_REQUIRED", "dedupe key is not canonical")
        if proposal.get("idempotency_key") != canonical_idempotency_key(proposal):
            raise HermesMemoryAdapterError("DETERMINISTIC_KEYS_REQUIRED", "idempotency key is not canonical")
    except MemoryPatchProposalValidationError as exc:
        raise HermesMemoryAdapterError("DETERMINISTIC_KEYS_REQUIRED", str(exc)) from exc


def _sanitize_gateway_payload(capability: str, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise HermesMemoryAdapterError("INVALID_GATEWAY_RESULT", "gateway payload must be an object")
    sanitized = _sanitize_hermes_report_markers(deepcopy(dict(payload)))
    if capability == "memory.propose_patch":
        event = sanitized.get("proposal_event")
        if isinstance(event, Mapping):
            sanitized["proposal_event"] = {
                "schema": event.get("schema"),
                "status": event.get("status"),
                "event_ref": event.get("event_ref"),
                "dedupe_key": event.get("dedupe_key"),
                "idempotency_key": event.get("idempotency_key"),
                "conflict_ref": event.get("conflict_ref"),
                "approval_ref": event.get("approval_ref"),
                "confirmed_canonical_revision": event.get("confirmed_canonical_revision"),
                "canonical_ref": None,
            }
            sanitized["verified_fields"] = {
                "dedupe_key": event.get("dedupe_key"),
                "idempotency_key": event.get("idempotency_key"),
                "provenance": "exact_canonical_confirmed",
                "confirmation": {
                    "confirmed_canonical_revision": event.get("confirmed_canonical_revision"),
                    "approval_ref": event.get("approval_ref"),
                },
            }
    return validate_public_payload(sanitized)


def _sanitize_hermes_report_markers(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _sanitize_hermes_report_markers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_sanitize_hermes_report_markers(child) for child in value]
    if isinstance(value, str):
        return value.replace("private_project_memory", "hermes_memory")
    return value


def _write_gate_result(
    capability: str,
    payload: Mapping[str, object],
    seen_proposal_events: set[str],
) -> dict[str, object]:
    if capability != "memory.propose_patch":
        return {"outcome": "BLOCKED", "reason": "read_only_capability"}
    event = payload.get("proposal_event")
    if not isinstance(event, Mapping):
        return {"outcome": "BLOCKED", "reason": "missing_proposal_event"}
    event_ref = event.get("event_ref")
    status = event.get("status")
    if isinstance(event_ref, str) and event_ref in seen_proposal_events:
        outcome = "DUPLICATE_EXISTING"
        reason = "proposal_is_idempotent_duplicate"
    elif status == "REVIEW_REQUIRED":
        outcome = "REVIEW_REQUIRED"
        reason = "proposal_requires_review"
    elif status == "ACCEPTED":
        outcome = "APPROVED_FOR_OPERATOR"
        reason = "operator_approval_required_before_canonical_write"
    else:
        outcome = "BLOCKED"
        reason = "proposal_not_gate_eligible"
    if isinstance(event_ref, str):
        seen_proposal_events.add(event_ref)
    return {
        "outcome": outcome,
        "reason": reason,
        "operator_required": outcome in {"APPROVED_FOR_OPERATOR", "REVIEW_REQUIRED"},
        "canonical_commit_allowed": False,
    }


def _safe_project_id(value: object) -> str:
    return validate_namespace(value, allowed_namespaces=ALLOWED_NAMESPACES)


def _reject_direct_storage_markers(value: object) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str).lower()
    if any(marker in serialized for marker in _FORBIDDEN_DIRECT_STORAGE_MARKERS):
        raise HermesMemoryAdapterError("DIRECT_STORAGE_ACCESS_BLOCKED", "Hermes must use Memory Gateway only")

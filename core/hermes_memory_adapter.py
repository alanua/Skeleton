from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_policy import MemoryGatewayPolicyError, command_name, validate_public_payload
from core.memory_patch_proposal import PATCH_PROPOSAL_SCHEMA


HERMES_MEMORY_REQUEST_SCHEMA = "skeleton.hermes_memory_request.v1"
HERMES_MEMORY_RESULT_SCHEMA = "skeleton.hermes_memory_result.v1"
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

HERMES_MEMORY_OPERATIONS = frozenset(
    {
        "memory.lookup_exact",
        "memory.search_semantic",
        "memory.get_index_freshness",
        "graph.query_code",
        "graph.get_index_freshness",
        "memory.propose_patch",
    }
)


class HermesMemoryAdapterError(ValueError):
    """Raised when a Hermes memory request fails closed before gateway execution."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class HermesMemoryBinding:
    namespace: str
    project_id: str


class HermesMemoryAdapter:
    """Gateway-only adapter for public-safe Hermes memory tasks."""

    def __init__(self, *, gateway: MemoryGateway, namespace: str, project_id: str) -> None:
        self._gateway = gateway
        self._binding = HermesMemoryBinding(
            namespace=_safe_identifier(namespace, "namespace"),
            project_id=_safe_identifier(project_id, "project_id"),
        )

    @property
    def binding(self) -> HermesMemoryBinding:
        return self._binding

    def run(self, request: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(request, Mapping):
            raise HermesMemoryAdapterError("INVALID_HERMES_MEMORY_REQUEST", "request must be an object")
        if request.get("schema") != HERMES_MEMORY_REQUEST_SCHEMA:
            raise HermesMemoryAdapterError("INVALID_HERMES_MEMORY_REQUEST", "request schema is invalid")

        namespace = _safe_identifier(request.get("namespace"), "namespace")
        project_id = _safe_identifier(request.get("project_id"), "project_id")
        if namespace != self._binding.namespace or project_id != self._binding.project_id:
            raise HermesMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "request binding mismatch")

        operation = request.get("operation")
        if operation not in HERMES_MEMORY_OPERATIONS:
            raise HermesMemoryAdapterError("OPERATION_NOT_ALLOWLISTED", "operation is not allowlisted")
        parameters = request.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise HermesMemoryAdapterError("INVALID_HERMES_MEMORY_REQUEST", "parameters must be an object")

        payload = self._gateway_payload(operation=str(operation), parameters=parameters)
        gateway_response = self._gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": namespace,
                "command": command_name(namespace, str(operation)),
                "payload": payload,
            }
        )
        return self._result(operation=str(operation), gateway_response=gateway_response)

    def _gateway_payload(self, *, operation: str, parameters: Mapping[str, Any]) -> dict[str, object]:
        project_id = self._binding.project_id
        if operation == "memory.lookup_exact":
            return {"project_id": project_id, "key": _safe_identifier(parameters.get("key"), "key")}
        if operation == "memory.search_semantic":
            return {"project_id": project_id, "query": _safe_identifier(parameters.get("query"), "query")}
        if operation == "graph.query_code":
            return {"project_id": project_id, "query": _safe_identifier(parameters.get("query"), "query")}
        if operation in {"memory.get_index_freshness", "graph.get_index_freshness"}:
            return {"project_id": project_id}
        if operation == "memory.propose_patch":
            proposal = parameters.get("proposal")
            if not isinstance(proposal, Mapping):
                raise HermesMemoryAdapterError("INVALID_PATCH_PROPOSAL", "proposal must be an object")
            if proposal.get("schema", PATCH_PROPOSAL_SCHEMA) != PATCH_PROPOSAL_SCHEMA:
                raise HermesMemoryAdapterError("INVALID_PATCH_PROPOSAL", "proposal schema is invalid")
            if proposal.get("namespace") != self._binding.namespace:
                raise HermesMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "proposal namespace mismatch")
            if proposal.get("project_id") != self._binding.project_id:
                raise HermesMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "proposal project_id mismatch")
            return {"project_id": project_id, "proposal": dict(proposal)}
        raise HermesMemoryAdapterError("OPERATION_NOT_ALLOWLISTED", "operation is not allowlisted")

    def _result(self, *, operation: str, gateway_response: Mapping[str, Any]) -> dict[str, object]:
        payload = gateway_response.get("payload")
        if not isinstance(payload, Mapping):
            raise HermesMemoryAdapterError("INVALID_GATEWAY_RESPONSE", "gateway payload is invalid")

        status = "DRY_RUN_OK"
        decision: dict[str, object] = {
            "allowed": True,
            "reason": "gateway_read_completed",
        }
        proposal_event = payload.get("proposal_event")
        if isinstance(proposal_event, Mapping):
            if payload.get("idempotency_classification") == "DUPLICATE_EXISTING":
                status = "DUPLICATE_EXISTING"
                decision = {"allowed": False, "reason": "proposal_already_exists"}
            else:
                status = "OPERATOR_APPROVAL_REQUIRED"
                decision = {"allowed": False, "reason": "canonical_write_requires_operator_approval"}

        result = {
            "schema": HERMES_MEMORY_RESULT_SCHEMA,
            "status": status,
            "namespace": self._binding.namespace,
            "project_id": self._binding.project_id,
            "operation": operation,
            "decision": decision,
            "gateway": {
                "schema": gateway_response.get("schema"),
                "command": gateway_response.get("command"),
                "contract_version": gateway_response.get("contract_version"),
            },
            "payload": _public_payload_summary(operation=operation, payload=payload),
        }
        return validate_public_payload(result)


def blocked_result(reason_code: str, *, namespace: object = None, project_id: object = None) -> dict[str, object]:
    result = {
        "schema": HERMES_MEMORY_RESULT_SCHEMA,
        "status": "BLOCKED",
        "namespace": namespace if isinstance(namespace, str) else None,
        "project_id": project_id if isinstance(project_id, str) else None,
        "decision": {"allowed": False, "reason": reason_code},
        "payload": {},
    }
    return validate_public_payload(result)


def _public_payload_summary(*, operation: str, payload: Mapping[str, Any]) -> dict[str, object]:
    if operation == "memory.lookup_exact":
        return {
            "authoritative": payload.get("authoritative"),
            "authority_classification": payload.get("authority_classification"),
            "source_kind": payload.get("source_kind"),
            "canonical_ref": payload.get("canonical_ref"),
            "canonical_revision": payload.get("canonical_revision"),
        }
    if operation in {"memory.search_semantic", "graph.query_code"}:
        results = payload.get("results")
        return {"result_count": len(results) if isinstance(results, list) else 0}
    if operation in {"memory.get_index_freshness", "graph.get_index_freshness"}:
        return {"freshness_checked": True}
    if operation == "memory.propose_patch":
        event = payload.get("proposal_event")
        if not isinstance(event, Mapping):
            return {"proposal_status": "UNKNOWN"}
        return {
            "proposal_status": event.get("status"),
            "event_ref": event.get("event_ref"),
            "classification": payload.get("idempotency_classification"),
        }
    return {}


def _safe_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise HermesMemoryAdapterError(f"{name.upper()}_REQUIRED", f"{name} is mandatory")
    validate_public_payload({name: value})
    return value

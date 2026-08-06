from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_gateway import (
    MEMORY_GATEWAY_REQUEST_SCHEMA,
    PRIVATE_RUNTIME_ONLY,
    PUBLIC_SAFE_CODE_TESTS_ONLY,
    SECRET_REFERENCE_ONLY,
    MemoryGateway,
)
from core.memory_gateway_policy import MemoryGatewayPolicyError, command_name, validate_public_payload
from core.memory_patch_proposal import PATCH_PROPOSAL_SCHEMA


PROJECT_MEMORY_REQUEST_SCHEMA = "skeleton.project_memory.request.v1"
PROJECT_MEMORY_RECEIPT_SCHEMA = "skeleton.project_memory.receipt.v1"
PROJECT_MEMORY_OPERATION = "memory.propose_patch"
PROJECT_MEMORY_PRIVACY_BOUNDARIES = frozenset(
    {
        PUBLIC_SAFE_CODE_TESTS_ONLY,
        SECRET_REFERENCE_ONLY,
        PRIVATE_RUNTIME_ONLY,
    }
)
PROJECT_MEMORY_RECORD_CLASSIFICATIONS = frozenset({"PUBLIC", "PRIVATE", "CANON"})
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ProjectMemoryAdapterError(ValueError):
    """Raised when project memory input is blocked before MemoryGateway access."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProjectMemoryBinding:
    namespace: str
    project_id: str
    privacy_boundary: str = PUBLIC_SAFE_CODE_TESTS_ONLY


class ProjectMemoryAdapter:
    """MemoryGateway-only project memory proposal adapter."""

    def __init__(
        self,
        *,
        gateway: MemoryGateway,
        namespace: str,
        project_id: str,
        privacy_boundary: str = PUBLIC_SAFE_CODE_TESTS_ONLY,
    ) -> None:
        self._gateway = gateway
        self._binding = ProjectMemoryBinding(
            namespace=_safe_identifier(namespace, "namespace"),
            project_id=_safe_identifier(project_id, "project_id"),
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
        namespace = _safe_identifier(request.get("namespace"), "namespace")
        project_id = _safe_identifier(request.get("project_id"), "project_id")
        if namespace != self._binding.namespace or project_id != self._binding.project_id:
            raise ProjectMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "request binding mismatch")
        if request.get("operation", PROJECT_MEMORY_OPERATION) != PROJECT_MEMORY_OPERATION:
            raise ProjectMemoryAdapterError("OPERATION_NOT_ALLOWLISTED", "operation is not allowlisted")

        request_boundary = _privacy_boundary(request.get("privacy_boundary", self._binding.privacy_boundary))
        if request_boundary != self._binding.privacy_boundary:
            raise ProjectMemoryAdapterError("PRIVACY_BOUNDARY_MISMATCH", "request privacy boundary mismatch")
        classification = _record_classification(request)
        _authorize_record_boundary(classification=classification, privacy_boundary=request_boundary)

        gateway_boundary = getattr(self._gateway, "privacy_boundary", PUBLIC_SAFE_CODE_TESTS_ONLY)
        if classification == "PRIVATE" and gateway_boundary != PRIVATE_RUNTIME_ONLY:
            raise ProjectMemoryAdapterError(
                "PRIVATE_RECORD_REQUIRES_PRIVATE_RUNTIME_BOUNDARY",
                "private records require a private-runtime MemoryGateway token",
            )

        proposal = _proposal(request)
        if proposal.get("schema", PATCH_PROPOSAL_SCHEMA) != PATCH_PROPOSAL_SCHEMA:
            raise ProjectMemoryAdapterError("INVALID_PATCH_PROPOSAL", "proposal schema is invalid")
        if proposal.get("namespace") != namespace or proposal.get("project_id") != project_id:
            raise ProjectMemoryAdapterError("PROJECT_NOT_AUTHORIZED", "proposal binding mismatch")

        gateway_response = self._gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": namespace,
                "command": command_name(namespace, PROJECT_MEMORY_OPERATION),
                "payload": {"project_id": project_id, "proposal": dict(proposal)},
            }
        )
        return _receipt(
            gateway_response=gateway_response,
            namespace=namespace,
            project_id=project_id,
            classification=classification,
        )


def blocked_receipt(
    reason_code: str,
    *,
    namespace: object = None,
    project_id: object = None,
) -> dict[str, object]:
    return validate_public_payload(
        {
            "schema": PROJECT_MEMORY_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "namespace": namespace if isinstance(namespace, str) else None,
            "project_id": project_id if isinstance(project_id, str) else None,
            "decision": {"allowed": False, "reason": reason_code},
            "payload": {},
        }
    )


def _receipt(
    *,
    gateway_response: Mapping[str, Any],
    namespace: str,
    project_id: str,
    classification: str,
) -> dict[str, object]:
    payload = gateway_response.get("payload")
    if not isinstance(payload, Mapping):
        raise ProjectMemoryAdapterError("INVALID_GATEWAY_RESPONSE", "gateway payload is invalid")
    event = payload.get("proposal_event")
    if not isinstance(event, Mapping):
        raise ProjectMemoryAdapterError("INVALID_GATEWAY_RESPONSE", "proposal event is missing")
    proposal_status = str(event.get("status", "UNKNOWN"))
    duplicate = payload.get("idempotency_classification") == "DUPLICATE_EXISTING"
    status = "DUPLICATE_EXISTING" if duplicate else "OPERATOR_APPROVAL_REQUIRED"
    reason = "proposal_already_exists" if duplicate else "canonical_write_requires_operator_approval"
    canonical_write_performed = bool(event.get("canonical_write_performed", False))
    operator_approval_required = bool(
        event.get("operator_approval_required", proposal_status in {"ACCEPTED", "REVIEW_REQUIRED"})
    )
    result = {
        "schema": PROJECT_MEMORY_RECEIPT_SCHEMA,
        "status": status,
        "namespace": namespace,
        "project_id": project_id,
        "classification": classification,
        "decision": {"allowed": False, "reason": reason},
        "gateway": {
            "schema": gateway_response.get("schema"),
            "command": gateway_response.get("command"),
            "contract_version": gateway_response.get("contract_version"),
        },
        "payload": {
            "proposal_status": proposal_status,
            "event_ref": event.get("event_ref"),
            "classification": payload.get("idempotency_classification"),
            "canonical_write_performed": canonical_write_performed,
            "operator_approval_required": operator_approval_required,
        },
    }
    return validate_public_payload(result)


def _authorize_record_boundary(*, classification: str, privacy_boundary: str) -> None:
    if classification == "PRIVATE" and privacy_boundary != PRIVATE_RUNTIME_ONLY:
        raise ProjectMemoryAdapterError(
            "PRIVATE_RECORD_BLOCKED_BY_PRIVACY_BOUNDARY",
            "private records require PRIVATE_RUNTIME_ONLY",
        )
    if classification == "CANON":
        return
    if classification == "PUBLIC":
        return


def _proposal(request: Mapping[str, Any]) -> Mapping[str, Any]:
    parameters = request.get("parameters")
    if isinstance(parameters, Mapping) and isinstance(parameters.get("proposal"), Mapping):
        return parameters["proposal"]
    record = request.get("record")
    if isinstance(record, Mapping) and isinstance(record.get("proposal"), Mapping):
        return record["proposal"]
    proposal = request.get("proposal")
    if isinstance(proposal, Mapping):
        return proposal
    raise ProjectMemoryAdapterError("INVALID_PATCH_PROPOSAL", "proposal must be an object")


def _record_classification(request: Mapping[str, Any]) -> str:
    values: list[object] = [request.get("record_classification")]
    record = request.get("record")
    if isinstance(record, Mapping):
        values.extend(
            [
                record.get("record_classification"),
                record.get("classification"),
                record.get("privacy_classification"),
                record.get("privacy"),
            ]
        )
    proposal = _proposal(request)
    values.extend([proposal.get("record_classification"), proposal.get("privacy_classification")])
    for value in values:
        if isinstance(value, str) and value:
            normalized = value.upper()
            if normalized in {"PUBLIC_SAFE", "PUBLIC_SAFE_CODE"}:
                return "PUBLIC"
            if normalized in PROJECT_MEMORY_RECORD_CLASSIFICATIONS:
                return normalized
            raise ProjectMemoryAdapterError("INVALID_RECORD_CLASSIFICATION", "record classification is invalid")
    return "PUBLIC"


def _privacy_boundary(value: object) -> str:
    if value not in PROJECT_MEMORY_PRIVACY_BOUNDARIES:
        raise ProjectMemoryAdapterError("INVALID_PRIVACY_BOUNDARY", "privacy boundary is not allowlisted")
    return str(value)


def _safe_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ProjectMemoryAdapterError(f"{name.upper()}_REQUIRED", f"{name} is mandatory")
    try:
        validate_public_payload({name: value})
    except MemoryGatewayPolicyError as exc:
        raise ProjectMemoryAdapterError(exc.reason_code, str(exc)) from exc
    return value

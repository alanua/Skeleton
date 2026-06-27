from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_gateway import MemoryGateway
from core.memory_gateway_policy import MemoryGatewayPolicyError, validate_public_payload


HERMES_MEMORY_REQUEST_SCHEMA = "skeleton.hermes_memory.request.v1"
HERMES_MEMORY_RESULT_SCHEMA = "skeleton.hermes_memory.result.v1"

HERMES_MEMORY_CAPABILITIES = (
    "lookup_exact",
    "get_conflicts",
    "get_override_history",
    "get_audit_log",
    "get_index_freshness",
    "propose_patch",
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class HermesMemoryAdapter:
    """Hermes-facing memory facade bound to one explicit namespace/project pair."""

    gateway: MemoryGateway
    namespace: str
    project_id: str

    def __post_init__(self) -> None:
        _safe_token(self.namespace, "namespace")
        _safe_token(self.project_id, "project_id")

    def run(self, packet: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(packet, Mapping):
            raise MemoryGatewayPolicyError("INVALID_HERMES_MEMORY_PACKET", "packet must be an object")
        if packet.get("schema") != HERMES_MEMORY_REQUEST_SCHEMA:
            raise MemoryGatewayPolicyError("INVALID_HERMES_MEMORY_PACKET", "packet schema is invalid")
        if packet.get("namespace") != self.namespace or packet.get("project_id") != self.project_id:
            raise MemoryGatewayPolicyError("HERMES_MEMORY_SCOPE_MISMATCH", "packet scope is not adapter scope")
        capability = packet.get("capability")
        if capability not in HERMES_MEMORY_CAPABILITIES:
            raise MemoryGatewayPolicyError("HERMES_MEMORY_CAPABILITY_FORBIDDEN", "capability is not allowed")
        payload = packet.get("payload", {})
        if not isinstance(payload, Mapping):
            raise MemoryGatewayPolicyError("INVALID_HERMES_MEMORY_PACKET", "payload must be an object")

        if capability == "propose_patch":
            proposal = payload.get("proposal")
            if not isinstance(proposal, Mapping):
                raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "proposal must be an object")
            existing = self.gateway.lookup_proposal_by_idempotency_key(
                namespace=self.namespace,
                project_id=self.project_id,
                idempotency_key=str(proposal.get("idempotency_key", "")),
            )
            if existing is not None:
                gateway_result = {
                    "proposal_event": {
                        **existing,
                        "status": "DUPLICATE_EXISTING",
                    }
                }
            else:
                gateway_result = self.gateway.propose_patch(
                    namespace=self.namespace,
                    project_id=self.project_id,
                    proposal=proposal,
                )["payload"]
        else:
            gateway_result = self._execute_capability(str(capability), payload)["payload"]

        result = {
            "schema": HERMES_MEMORY_RESULT_SCHEMA,
            "namespace": self.namespace,
            "project_id": self.project_id,
            "capability": capability,
            "payload": gateway_result,
        }
        return validate_public_payload(result)

    def _execute_capability(self, capability: str, payload: Mapping[str, Any]) -> dict[str, object]:
        if capability == "lookup_exact":
            return self.gateway.lookup_exact(
                namespace=self.namespace,
                project_id=self.project_id,
                key=str(payload.get("key", "")),
            )
        if capability == "get_conflicts":
            return self.gateway.get_conflicts(namespace=self.namespace, project_id=self.project_id)
        if capability == "get_override_history":
            return self.gateway.get_override_history(
                namespace=self.namespace,
                project_id=self.project_id,
                override_ref=str(payload.get("override_ref", "")),
            )
        if capability == "get_audit_log":
            return self.gateway.get_audit_log(namespace=self.namespace, project_id=self.project_id)
        if capability == "get_index_freshness":
            return self.gateway.get_memory_index_freshness(
                namespace=self.namespace,
                project_id=self.project_id,
            )
        raise MemoryGatewayPolicyError("HERMES_MEMORY_CAPABILITY_FORBIDDEN", "capability is not allowed")


def _safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise MemoryGatewayPolicyError("INVALID_HERMES_MEMORY_SCOPE", f"invalid {name}")
    validate_public_payload({name: value})
    return value

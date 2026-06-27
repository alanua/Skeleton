from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_gateway_policy import (
    ALLOWED_NAMESPACES,
    EXACT_CONFIRMATION_REVISION_MISMATCH,
    GRAPH_RESULT_NOT_CANON_CONFIRMED,
    PUBLIC_MODE_FORBIDDEN_NAMESPACES,
    SEMANTIC_RESULT_NOT_CANON_CONFIRMED,
    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
    MemoryGatewayPolicyError,
    command_name,
    sanitized_actor_ref,
    sanitized_reason_code,
    split_command,
    validate_namespace,
    validate_public_payload,
)
from core.memory_override import MemoryOverrideRegistry
from core.memory_patch_proposal import MemoryPatchProposalRegistry


MEMORY_GATEWAY_REQUEST_SCHEMA = "skeleton.memory_gateway.request.v1"
MEMORY_GATEWAY_RESPONSE_SCHEMA = "skeleton.memory_gateway.response.v1"
MEMORY_GATEWAY_AUDIT_SCHEMA = "skeleton.memory_gateway.audit_event.v1"
MEMORY_GATEWAY_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class GatewayCapabilityToken:
    schema: str
    namespaces: tuple[str, ...]
    project_id: str
    public_mode: bool = True


REQUIRED_MEMORY_COMMAND_SUFFIXES = frozenset(
    {
        "memory.lookup_exact",
        "memory.get_conflicts",
        "memory.get_override_history",
        "memory.get_audit_log",
        "memory.get_index_freshness",
        "memory.propose_patch",
    }
)


def capability_token(
    *,
    namespaces: tuple[str, ...],
    project_id: str | None = None,
    public_mode: bool = True,
) -> GatewayCapabilityToken:
    return GatewayCapabilityToken(
        schema="skeleton.memory_gateway.capability_token.v1",
        namespaces=namespaces,
        project_id=project_id or namespaces[0],
        public_mode=public_mode,
    )


class MemoryGateway:
    """Bounded synthetic Memory Gateway contract with namespace-isolated policy."""

    def __init__(
        self,
        token: GatewayCapabilityToken | Mapping[str, Any],
        *,
        patch_registry: MemoryPatchProposalRegistry | None = None,
        override_registry: MemoryOverrideRegistry | None = None,
    ) -> None:
        self._token = _normalize_token(token)
        self._patch_registry = patch_registry or MemoryPatchProposalRegistry()
        self._override_registry = override_registry or MemoryOverrideRegistry()
        self._audit_log: list[dict[str, object]] = []
        self._canonical = _canonical_seed()
        self._freshness = _freshness_seed()

    def execute(self, request: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(request, Mapping):
            raise MemoryGatewayPolicyError("INVALID_REQUEST", "request must be an object")
        if request.get("schema") != MEMORY_GATEWAY_REQUEST_SCHEMA:
            raise MemoryGatewayPolicyError("INVALID_REQUEST_SCHEMA", "request schema is invalid")
        command = request.get("command")
        namespace_from_command, suffix = split_command(str(command))
        namespace = self._authorize_namespace(request.get("namespace"))
        if namespace_from_command != namespace:
            raise MemoryGatewayPolicyError("COMMAND_NAMESPACE_MISMATCH", "command namespace mismatch")
        payload = request.get("payload", {})
        if not isinstance(payload, Mapping):
            raise MemoryGatewayPolicyError("INVALID_REQUEST", "payload must be an object")

        handlers = {
            "memory.lookup_exact": self.lookup_exact,
            "memory.get_conflicts": self.get_conflicts,
            "memory.get_override_history": self.get_override_history,
            "memory.get_audit_log": self.get_audit_log,
            "memory.get_index_freshness": self.get_memory_index_freshness,
            "memory.propose_patch": self.propose_patch,
        }
        if suffix not in handlers:
            raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command is not allowlisted")
        return handlers[suffix](namespace=namespace, **payload)

    def lookup_exact(self, *, namespace: str, key: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._bound_project_id()
        key = _safe_lookup_key(key)
        try:
            record = self._canonical[namespace][project_id][key]
        except KeyError as exc:
            raise MemoryGatewayPolicyError("CANONICAL_FACT_NOT_FOUND", "canonical fact not found") from exc
        return self._response(
            namespace=namespace,
            command_suffix="memory.lookup_exact",
            payload={
                **deepcopy(record),
                "namespace": namespace,
                "project_id": project_id,
                "authoritative": True,
                "authority_classification": "canonical_exact",
                "source_kind": "canonical_sqlite",
            },
        )

    def get_memory_index_freshness(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._bound_project_id()
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_index_freshness",
            payload={
                "project_id": project_id,
                "mempalace": deepcopy(self._freshness[namespace][project_id]["mempalace"]),
                "canonical_sqlite": deepcopy(self._freshness[namespace][project_id]["canonical_sqlite"]),
            },
        )

    def get_conflicts(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._bound_project_id()
        conflicts = [
            _sanitized_conflict(conflict)
            for conflict in self._patch_registry.get_conflicts()
            if _conflict_belongs_to_binding(conflict, namespace, project_id)
        ]
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_conflicts",
            payload={
                "event_class": "source_value_conflict",
                "project_id": project_id,
                "conflicts": conflicts,
            },
        )

    def get_override_history(self, *, namespace: str, override_ref: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._bound_project_id()
        history = [
            event
            for event in self._override_registry.get_override_history(override_ref)
            if event.get("namespace") == namespace and event.get("project_id") == project_id
        ]
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_override_history",
            payload={"event_class": "override_lifecycle", "project_id": project_id, "events": history},
        )

    def get_audit_log(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._bound_project_id()
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_audit_log",
            payload={
                "project_id": project_id,
                "events": [
                    deepcopy(event)
                    for event in self._audit_log
                    if event.get("namespace") == namespace and event.get("project_id") == project_id
                ]
            },
        )

    def propose_patch(self, *, namespace: str, proposal: Mapping[str, Any]) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        if not isinstance(proposal, Mapping):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "proposal must be an object")
        if proposal.get("namespace") != namespace:
            raise MemoryGatewayPolicyError("NAMESPACE_NOT_AUTHORIZED", "proposal namespace mismatch")
        if proposal.get("project_id") != self._bound_project_id():
            raise MemoryGatewayPolicyError("PROJECT_NOT_AUTHORIZED", "proposal project mismatch")
        self._validate_patch_evidence(namespace, proposal)
        event = self._patch_registry.propose(proposal)
        self._append_audit_event(
            namespace=namespace,
            project_id=self._bound_project_id(),
            actor_ref=proposal.get("actor_ref"),
            reason_code=proposal.get("reason_code"),
            approval_ref=proposal.get("approval_ref"),
            canonical_revision=proposal.get("confirmed_canonical_revision"),
        )
        return self._response(
            namespace=namespace,
            command_suffix="memory.propose_patch",
            payload={"proposal_event": event},
        )

    def _validate_patch_evidence(self, namespace: str, proposal: Mapping[str, Any]) -> None:
        project_id = self._bound_project_id()
        canonical_revision = self._freshness[namespace][project_id]["canonical_sqlite"]["current_canonical_revision"]
        if proposal.get("confirmed_canonical_revision") != canonical_revision:
            raise MemoryGatewayPolicyError(
                EXACT_CONFIRMATION_REVISION_MISMATCH,
                "exact confirmation revision does not match current canonical revision",
            )
        provenance_refs = proposal.get("provenance_refs")
        if not isinstance(provenance_refs, list):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "provenance refs must be an array")
        has_semantic = any(isinstance(ref, Mapping) and ref.get("kind") == "semantic_only" for ref in provenance_refs)
        has_graph = any(isinstance(ref, Mapping) and ref.get("kind") == "code_graph" for ref in provenance_refs)
        if has_semantic:
            if not proposal.get("confirmed_via_exact_ref"):
                raise MemoryGatewayPolicyError(
                    SEMANTIC_RESULT_NOT_CANON_CONFIRMED,
                    "semantic evidence requires exact canonical confirmation",
                )
            if self._freshness[namespace][project_id]["mempalace"]["stale"]:
                raise MemoryGatewayPolicyError(
                    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
                    "stale semantic index cannot support a patch proposal",
                )
        if has_graph:
            if not proposal.get("confirmed_via_exact_ref"):
                raise MemoryGatewayPolicyError(
                    GRAPH_RESULT_NOT_CANON_CONFIRMED,
                    "graph evidence requires exact canonical confirmation",
                )
            if self._freshness[namespace][project_id]["graphify"]["stale"]:
                raise MemoryGatewayPolicyError(
                    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
                    "stale graph index cannot support a patch proposal",
                )
        self._validate_canonical_exact_confirmation(namespace, proposal)

    def _validate_canonical_exact_confirmation(self, namespace: str, proposal: Mapping[str, Any]) -> None:
        target = proposal.get("normalized_target")
        confirmed_ref = proposal.get("confirmed_via_exact_ref")
        source_evidence_hash = proposal.get("source_evidence_hash")
        project_id = self._bound_project_id()
        if not isinstance(target, str) or not isinstance(confirmed_ref, str):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "exact confirmation target is invalid")
        canonical = self._canonical[namespace][project_id].get(target)
        current_revision = self._freshness[namespace][project_id]["canonical_sqlite"]["current_canonical_revision"]
        if canonical is None or canonical.get("canonical_revision") != current_revision:
            raise MemoryGatewayPolicyError(
                "EXACT_CONFIRMATION_NOT_CANONICAL",
                "exact confirmation is not bound to a current canonical record",
            )
        canonical_refs = canonical.get("provenance_refs")
        if not isinstance(canonical_refs, list):
            raise MemoryGatewayPolicyError(
                "EXACT_CONFIRMATION_NOT_CANONICAL",
                "canonical exact provenance is unavailable",
            )
        for ref in canonical_refs:
            if (
                isinstance(ref, Mapping)
                and ref.get("kind") == "exact_source"
                and ref.get("ref") == confirmed_ref
                and ref.get("evidence_hash") == source_evidence_hash
            ):
                return
        raise MemoryGatewayPolicyError(
            "EXACT_CONFIRMATION_NOT_CANONICAL",
            "exact confirmation does not match current canonical provenance",
        )

    def _append_audit_event(
        self,
        *,
        namespace: str,
        project_id: str,
        actor_ref: object,
        reason_code: object,
        approval_ref: object,
        canonical_revision: object,
    ) -> None:
        event = {
            "schema": MEMORY_GATEWAY_AUDIT_SCHEMA,
            "namespace": namespace,
            "project_id": project_id,
            "event_ref": f"memory-gateway-audit-{len(self._audit_log) + 1:06d}",
            "actor_ref": sanitized_actor_ref(actor_ref),
            "reason_code": sanitized_reason_code(reason_code),
            "approval_ref": sanitized_actor_ref(approval_ref),
            "canonical_revision": canonical_revision,
        }
        validate_public_payload(event)
        self._audit_log.append(event)

    def _authorize_namespace(self, namespace: object) -> str:
        authorized = validate_namespace(namespace, allowed_namespaces=frozenset(self._token.namespaces))
        if self._token.public_mode and authorized in PUBLIC_MODE_FORBIDDEN_NAMESPACES:
            raise MemoryGatewayPolicyError("PRIVATE_NAMESPACE_PUBLIC_MODE_FORBIDDEN", "namespace is private")
        return authorized

    def _bound_project_id(self) -> str:
        return _safe_project_id(self._token.project_id)

    def _response(self, *, namespace: str, command_suffix: str, payload: Mapping[str, Any]) -> dict[str, object]:
        response = {
            "schema": MEMORY_GATEWAY_RESPONSE_SCHEMA,
            "contract_version": MEMORY_GATEWAY_CONTRACT_VERSION,
            "namespace": namespace,
            "command": command_name(namespace, command_suffix),
            "payload": validate_public_payload(payload),
        }
        json.dumps(response, allow_nan=False, sort_keys=True)
        return response


def _normalize_token(token: GatewayCapabilityToken | Mapping[str, Any]) -> GatewayCapabilityToken:
    if isinstance(token, GatewayCapabilityToken):
        raw_namespaces = token.namespaces
        raw_project_id = token.project_id
        public_mode = token.public_mode
    elif isinstance(token, Mapping):
        if token.get("schema") != "skeleton.memory_gateway.capability_token.v1":
            raise MemoryGatewayPolicyError("INVALID_CAPABILITY_TOKEN", "capability token schema is invalid")
        raw_namespaces = token.get("namespaces")
        raw_project_id = token.get("project_id")
        public_mode = bool(token.get("public_mode", True))
    else:
        raise MemoryGatewayPolicyError("INVALID_CAPABILITY_TOKEN", "capability token must be an object")
    if not isinstance(raw_namespaces, tuple | list) or not raw_namespaces:
        raise MemoryGatewayPolicyError("NAMESPACE_REQUIRED", "capability token requires namespaces")
    namespaces = tuple(
        validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
        for namespace in raw_namespaces
    )
    project_id = _safe_project_id(raw_project_id or namespaces[0])
    return GatewayCapabilityToken(
        schema="skeleton.memory_gateway.capability_token.v1",
        namespaces=namespaces,
        project_id=project_id,
        public_mode=public_mode,
    )


def _canonical_seed() -> dict[str, dict[str, dict[str, dict[str, object]]]]:
    return {
        namespace: _canonical_projects(namespace)
        for namespace in ALLOWED_NAMESPACES
    }


def _canonical_projects(namespace: str) -> dict[str, dict[str, dict[str, object]]]:
    project_ids = [namespace]
    if namespace == "aufmass":
        project_ids.extend(["project-a", "project-b"])
    return {project_id: {"primary_fact": _canonical_fixture(namespace, project_id)} for project_id in project_ids}


def _canonical_fixture(namespace: str, project_id: str) -> dict[str, object]:
    evidence_hash = stable_project_hash(namespace, project_id)
    return {
        "canonical_ref": f"canon-{namespace}-{project_id}-primary",
        "canonical_revision": 3,
        "fact_type": "status",
        "value": {"state": f"ready-{project_id}"},
        "provenance_refs": [
            {
                "ref": f"exact-{namespace}-{project_id}-primary",
                "kind": "exact_source",
                "evidence_hash": evidence_hash,
            }
        ],
    }


def _freshness_seed() -> dict[str, dict[str, dict[str, dict[str, object]]]]:
    freshness = {}
    for namespace in ALLOWED_NAMESPACES:
        projects = [namespace]
        if namespace == "aufmass":
            projects.extend(["project-a", "project-b"])
        freshness[namespace] = {
            project_id: _freshness_fixture(namespace, project_id)
            for project_id in projects
        }
    return freshness


def _freshness_fixture(namespace: str, project_id: str) -> dict[str, dict[str, object]]:
    stale = namespace == "bauclock"
    return {
            "graphify": {
                "indexed_repo_commit": "commit-indexed-0003",
                "current_repo_commit": "commit-current-0004" if stale else "commit-indexed-0003",
                "indexed_at": "2026-06-27T00:00:00Z",
                "stale": stale,
                "index_namespace": namespace,
                "project_id": project_id,
            },
            "mempalace": {
                "indexed_canonical_revision": 2 if stale else 3,
                "current_canonical_revision": 3,
                "source_snapshot_id": f"snapshot-{namespace}-{project_id}-0003",
                "indexed_at": "2026-06-27T00:00:00Z",
                "stale": stale,
                "index_namespace": namespace,
                "project_id": project_id,
            },
            "canonical_sqlite": {
                "current_canonical_revision": 3,
                "project_id": project_id,
            },
        }


def stable_project_hash(namespace: str, project_id: str) -> str:
    return hashlib.sha256(f"{namespace}:{project_id}:primary".encode("utf-8")).hexdigest()


def _safe_lookup_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise MemoryGatewayPolicyError("INVALID_LOOKUP_KEY", "lookup key must be a bounded string")
    validate_public_payload({"lookup_key": value})
    return value


def _sanitized_conflict(conflict: Mapping[str, object]) -> dict[str, object]:
    allowed_keys = {
        "schema",
        "status",
        "event_ref",
        "conflict_ref",
        "namespace",
        "project_id",
        "dedupe_key",
        "existing_canonical_ref",
        "existing_event_ref",
        "reason_code",
    }
    return {key: deepcopy(conflict[key]) for key in allowed_keys if key in conflict}


def _conflict_belongs_to_binding(conflict: Mapping[str, object], namespace: str, project_id: str) -> bool:
    return conflict.get("namespace") == namespace and conflict.get("project_id") == project_id


def allowed_command_names(namespace: str) -> list[str]:
    namespace = validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
    return [command_name(namespace, suffix) for suffix in sorted(REQUIRED_MEMORY_COMMAND_SUFFIXES)]


def _safe_project_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise MemoryGatewayPolicyError("PROJECT_REQUIRED", "project_id is mandatory")
    validate_public_payload({"project_id": value})
    return value

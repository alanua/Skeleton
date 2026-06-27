from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

try:
    from core.memory_gateway_policy import (
        ALLOWED_COMMAND_SUFFIXES,
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
except ModuleNotFoundError:
    ALLOWED_NAMESPACES = frozenset(
        {
            "aufmass",
            "bauclock",
            "skeleton",
            "home_automation",
            "legal_private",
        }
    )
    ALLOWED_COMMAND_SUFFIXES = frozenset(
        {
            "memory.lookup_exact",
            "memory.search_semantic",
            "memory.get_conflicts",
            "memory.get_override_history",
            "memory.get_audit_log",
            "memory.get_index_freshness",
            "graph.query_code",
            "graph.get_index_freshness",
            "memory.propose_patch",
        }
    )
    PUBLIC_MODE_FORBIDDEN_NAMESPACES = frozenset({"legal_private"})
    SEMANTIC_RESULT_NOT_CANON_CONFIRMED = "SEMANTIC_RESULT_NOT_CANON_CONFIRMED"
    GRAPH_RESULT_NOT_CANON_CONFIRMED = "GRAPH_RESULT_NOT_CANON_CONFIRMED"
    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE = "STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE"
    EXACT_CONFIRMATION_REVISION_MISMATCH = "EXACT_CONFIRMATION_REVISION_MISMATCH"
    EXACT_CONFIRMATION_NOT_CANONICAL = "EXACT_CONFIRMATION_NOT_CANONICAL"

    _SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    _FORBIDDEN_PUBLIC_MARKERS = (
        "/",
        "\\",
        "file:",
        ".sqlite",
        ".db",
        "secret",
        "token",
        "password",
        "credential",
        "private-value",
    )

    class MemoryGatewayPolicyError(ValueError):
        """Raised when gateway policy rejects a request fail-closed."""

        def __init__(self, reason_code: str, message: str) -> None:
            super().__init__(message)
            self.reason_code = reason_code

    def command_name(namespace: str, suffix: str) -> str:
        namespace = validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
        if suffix not in ALLOWED_COMMAND_SUFFIXES:
            raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command suffix is not allowlisted")
        return f"{namespace}.{suffix}"

    def split_command(command: str) -> tuple[str, str]:
        if not isinstance(command, str) or "." not in command:
            raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command must be namespace-qualified")
        namespace, suffix = command.split(".", 1)
        validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
        if suffix not in ALLOWED_COMMAND_SUFFIXES:
            raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command is not allowlisted")
        return namespace, suffix

    def validate_namespace(namespace: object, *, allowed_namespaces: frozenset[str]) -> str:
        if not isinstance(namespace, str) or not namespace:
            raise MemoryGatewayPolicyError("NAMESPACE_REQUIRED", "namespace is mandatory")
        if namespace == "*" or "*" in namespace:
            raise MemoryGatewayPolicyError("WILDCARD_NAMESPACE_FORBIDDEN", "wildcard namespace access is forbidden")
        if not _SAFE_TOKEN_RE.fullmatch(namespace):
            raise MemoryGatewayPolicyError("INVALID_NAMESPACE", "namespace is malformed")
        if namespace not in ALLOWED_NAMESPACES:
            raise MemoryGatewayPolicyError("UNKNOWN_NAMESPACE", "namespace is not registered")
        if namespace not in allowed_namespaces:
            raise MemoryGatewayPolicyError("NAMESPACE_NOT_AUTHORIZED", "namespace is not authorized by capability token")
        return namespace

    def validate_public_payload(value: Any) -> Any:
        sanitized = deepcopy(value)
        _reject_unsafe_public_value(sanitized, "payload")
        return sanitized

    def sanitized_actor_ref(actor_ref: object) -> str:
        if not isinstance(actor_ref, str) or not _SAFE_TOKEN_RE.fullmatch(actor_ref):
            raise MemoryGatewayPolicyError("INVALID_ACTOR_REF", "actor ref must be a safe token")
        return actor_ref

    def sanitized_reason_code(reason_code: object) -> str:
        if not isinstance(reason_code, str) or not _SAFE_TOKEN_RE.fullmatch(reason_code):
            raise MemoryGatewayPolicyError("INVALID_REASON_CODE", "reason code must be a safe token")
        return reason_code

    def _reject_unsafe_public_value(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str) or not _SAFE_TOKEN_RE.fullmatch(key):
                    raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains unsafe key")
                if key.lower() in {
                    "content",
                    "raw_content",
                    "raw_path",
                    "local_path",
                    "path",
                    "secret",
                    "token",
                    "password",
                    "credential",
                    "storage_api",
                }:
                    raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains unsafe field")
                _reject_unsafe_public_value(child, f"{path}.{key}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                _reject_unsafe_public_value(child, f"{path}[{index}]")
            return
        if value is None or isinstance(value, (bool, int, float)):
            return
        if isinstance(value, str):
            lowered = value.lower()
            if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_MARKERS):
                raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains private-looking value")
            return
        raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} is not JSON-safe")
else:
    EXACT_CONFIRMATION_NOT_CANONICAL = "EXACT_CONFIRMATION_NOT_CANONICAL"
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
    public_mode: bool = True


def capability_token(*, namespaces: tuple[str, ...], public_mode: bool = True) -> GatewayCapabilityToken:
    return GatewayCapabilityToken(
        schema="skeleton.memory_gateway.capability_token.v1",
        namespaces=namespaces,
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
        self._semantic = _semantic_seed()
        self._graph = _graph_seed()
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
            "memory.search_semantic": self.search_semantic,
            "memory.get_conflicts": self.get_conflicts,
            "memory.get_override_history": self.get_override_history,
            "memory.get_audit_log": self.get_audit_log,
            "memory.get_index_freshness": self.get_memory_index_freshness,
            "graph.query_code": self.query_code,
            "graph.get_index_freshness": self.get_graph_index_freshness,
            "memory.propose_patch": self.propose_patch,
        }
        return handlers[suffix](namespace=namespace, **payload)

    def lookup_exact(self, *, namespace: str, key: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        key = _safe_lookup_key(key)
        try:
            record = self._canonical[namespace][key]
        except KeyError as exc:
            raise MemoryGatewayPolicyError("CANONICAL_FACT_NOT_FOUND", "canonical fact not found") from exc
        return self._response(
            namespace=namespace,
            command_suffix="memory.lookup_exact",
            payload={
                **deepcopy(record),
                "namespace": namespace,
                "authoritative": True,
                "authority_classification": "canonical_exact",
                "source_kind": "canonical_sqlite",
            },
        )

    def search_semantic(self, *, namespace: str, query: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        _safe_lookup_key(query)
        results = []
        for result in self._semantic[namespace]:
            rendered = deepcopy(result)
            rendered.update(
                {
                    "namespace": namespace,
                    "authoritative": False,
                    "authority_classification": "derived_semantic",
                    "source_kind": "mempalace",
                    "freshness": deepcopy(self._freshness[namespace]["mempalace"]),
                }
            )
            results.append(rendered)
        return self._response(
            namespace=namespace,
            command_suffix="memory.search_semantic",
            payload={"results": results},
        )

    def query_code(self, *, namespace: str, query: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        _safe_lookup_key(query)
        results = []
        for result in self._graph[namespace]:
            rendered = deepcopy(result)
            rendered.update(
                {
                    "namespace": namespace,
                    "authoritative": False,
                    "authoritative_scope": "code_graph",
                    "authority_classification": "derived_code_graph",
                    "source_kind": "graphify",
                    "freshness": deepcopy(self._freshness[namespace]["graphify"]),
                }
            )
            results.append(rendered)
        return self._response(namespace=namespace, command_suffix="graph.query_code", payload={"results": results})

    def get_memory_index_freshness(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_index_freshness",
            payload={
                "mempalace": deepcopy(self._freshness[namespace]["mempalace"]),
                "canonical_sqlite": deepcopy(self._freshness[namespace]["canonical_sqlite"]),
            },
        )

    def get_graph_index_freshness(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        return self._response(
            namespace=namespace,
            command_suffix="graph.get_index_freshness",
            payload={"graphify": deepcopy(self._freshness[namespace]["graphify"])},
        )

    def get_conflicts(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        conflicts = [
            _sanitized_conflict(conflict)
            for conflict in self._patch_registry.get_conflicts()
            if _conflict_belongs_to_namespace(conflict, namespace)
        ]
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_conflicts",
            payload={
                "event_class": "source_value_conflict",
                "conflicts": conflicts,
            },
        )

    def get_override_history(self, *, namespace: str, override_ref: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        history = [
            event
            for event in self._override_registry.get_override_history(override_ref)
            if event.get("namespace") == namespace
        ]
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_override_history",
            payload={"event_class": "override_lifecycle", "events": history},
        )

    def get_audit_log(self, *, namespace: str) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_audit_log",
            payload={
                "events": [
                    deepcopy(event)
                    for event in self._audit_log
                    if event.get("namespace") == namespace
                ]
            },
        )

    def propose_patch(self, *, namespace: str, proposal: Mapping[str, Any]) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        if not isinstance(proposal, Mapping):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "proposal must be an object")
        if proposal.get("namespace") != namespace:
            raise MemoryGatewayPolicyError("NAMESPACE_NOT_AUTHORIZED", "proposal namespace mismatch")
        self._validate_patch_evidence(namespace, proposal)
        event = self._patch_registry.propose(proposal)
        self._append_audit_event(
            namespace=namespace,
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
        canonical_revision = self._freshness[namespace]["canonical_sqlite"]["current_canonical_revision"]
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
            if self._freshness[namespace]["mempalace"]["stale"]:
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
            if self._freshness[namespace]["graphify"]["stale"]:
                raise MemoryGatewayPolicyError(
                    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
                    "stale graph index cannot support a patch proposal",
                )
        self._validate_canonical_exact_confirmation(namespace, proposal)

    def _validate_canonical_exact_confirmation(self, namespace: str, proposal: Mapping[str, Any]) -> None:
        target = proposal.get("normalized_target")
        confirmed_ref = proposal.get("confirmed_via_exact_ref")
        source_evidence_hash = proposal.get("source_evidence_hash")
        if not isinstance(target, str) or not isinstance(confirmed_ref, str):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "exact confirmation target is invalid")
        canonical = self._canonical[namespace].get(target)
        current_revision = self._freshness[namespace]["canonical_sqlite"]["current_canonical_revision"]
        if canonical is None or canonical.get("canonical_revision") != current_revision:
            raise MemoryGatewayPolicyError(
                EXACT_CONFIRMATION_NOT_CANONICAL,
                "exact confirmation is not bound to a current canonical record",
            )
        canonical_refs = canonical.get("provenance_refs")
        if not isinstance(canonical_refs, list):
            raise MemoryGatewayPolicyError(
                EXACT_CONFIRMATION_NOT_CANONICAL,
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
            EXACT_CONFIRMATION_NOT_CANONICAL,
            "exact confirmation does not match current canonical provenance",
        )

    def _append_audit_event(
        self,
        *,
        namespace: str,
        actor_ref: object,
        reason_code: object,
        approval_ref: object,
        canonical_revision: object,
    ) -> None:
        event = {
            "schema": MEMORY_GATEWAY_AUDIT_SCHEMA,
            "namespace": namespace,
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
        public_mode = token.public_mode
    elif isinstance(token, Mapping):
        if token.get("schema") != "skeleton.memory_gateway.capability_token.v1":
            raise MemoryGatewayPolicyError("INVALID_CAPABILITY_TOKEN", "capability token schema is invalid")
        raw_namespaces = token.get("namespaces")
        public_mode = bool(token.get("public_mode", True))
    else:
        raise MemoryGatewayPolicyError("INVALID_CAPABILITY_TOKEN", "capability token must be an object")
    if not isinstance(raw_namespaces, tuple | list) or not raw_namespaces:
        raise MemoryGatewayPolicyError("NAMESPACE_REQUIRED", "capability token requires namespaces")
    namespaces = tuple(
        validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
        for namespace in raw_namespaces
    )
    return GatewayCapabilityToken(
        schema="skeleton.memory_gateway.capability_token.v1",
        namespaces=namespaces,
        public_mode=public_mode,
    )


def _canonical_seed() -> dict[str, dict[str, dict[str, object]]]:
    return {
        namespace: {
            "primary_fact": {
                "canonical_ref": f"canon-{namespace}-primary",
                "canonical_revision": 3,
                "fact_type": "status",
                "value": {"state": "ready"},
                "provenance_refs": [
                    {
                        "ref": f"exact-{namespace}-primary",
                        "kind": "exact_source",
                        "evidence_hash": "0" * 64,
                    }
                ],
            }
        }
        for namespace in ALLOWED_NAMESPACES
    }


def _semantic_seed() -> dict[str, list[dict[str, object]]]:
    return {
        namespace: [
            {
                "result_ref": f"semantic-{namespace}-primary",
                "canonical_ref_hint": f"canon-{namespace}-primary",
                "canonical_revision_hint": 3,
                "provenance_refs": [
                    {
                        "ref": f"semantic-{namespace}-summary",
                        "kind": "semantic_only",
                        "evidence_hash": "1" * 64,
                    }
                ],
            }
        ]
        for namespace in ALLOWED_NAMESPACES
    }


def _graph_seed() -> dict[str, list[dict[str, object]]]:
    return {
        namespace: [
            {
                "result_ref": f"graph-{namespace}-primary",
                "canonical_ref_hint": f"canon-{namespace}-primary",
                "canonical_revision_hint": 3,
                "provenance_refs": [
                    {
                        "ref": f"graph-{namespace}-edge",
                        "kind": "code_graph",
                        "evidence_hash": "2" * 64,
                    }
                ],
            }
        ]
        for namespace in ALLOWED_NAMESPACES
    }


def _freshness_seed() -> dict[str, dict[str, dict[str, object]]]:
    freshness = {}
    for namespace in ALLOWED_NAMESPACES:
        stale = namespace == "bauclock"
        freshness[namespace] = {
            "graphify": {
                "indexed_repo_commit": "commit-indexed-0003",
                "current_repo_commit": "commit-current-0004" if stale else "commit-indexed-0003",
                "indexed_at": "2026-06-27T00:00:00Z",
                "stale": stale,
                "index_namespace": namespace,
            },
            "mempalace": {
                "indexed_canonical_revision": 2 if stale else 3,
                "current_canonical_revision": 3,
                "source_snapshot_id": f"snapshot-{namespace}-0003",
                "indexed_at": "2026-06-27T00:00:00Z",
                "stale": stale,
                "index_namespace": namespace,
            },
            "canonical_sqlite": {
                "current_canonical_revision": 3,
            },
        }
    return freshness


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
        "dedupe_key",
        "existing_canonical_ref",
        "existing_event_ref",
        "reason_code",
    }
    return {key: deepcopy(conflict[key]) for key in allowed_keys if key in conflict}


def _conflict_belongs_to_namespace(conflict: Mapping[str, object], namespace: str) -> bool:
    existing_canonical_ref = conflict.get("existing_canonical_ref")
    if not isinstance(existing_canonical_ref, str):
        return False
    return existing_canonical_ref.startswith(f"canonical-{namespace}-")


def allowed_command_names(namespace: str) -> list[str]:
    namespace = validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
    return [command_name(namespace, suffix) for suffix in sorted(ALLOWED_COMMAND_SUFFIXES)]

from __future__ import annotations

import re
from typing import Any

from core.memory_value_validation import MemoryValueError, validate_memory_value

MEMORY_GATEWAY_POLICY_SCHEMA = "skeleton.memory_gateway.policy.v1"
ALLOWED_NAMESPACES = frozenset({"aufmass", "bauclock", "skeleton", "home_automation", "legal_private"})
ALLOWED_COMMAND_SUFFIXES = frozenset(
    {
        "memory.lookup_exact",
        "memory.search_semantic",
        "memory.get_conflicts",
        "memory.get_override_history",
        "memory.get_audit_log",
        "memory.get_index_freshness",
        "memory.prepare_canonical_manifest",
        "memory.import_canonical_manifest",
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

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ID_FIELDS = frozenset({"namespace", "project_id", "lookup_key", "fact_id"})
_PUBLIC_FIELDS = frozenset(
    "schema status state event_type event_ref event_class conflict_ref override_ref "
    "namespace project_id lookup_key fact_id object_id entity_scope fact_type normalized_target canonical_ref "
    "canonical_revision created_revision imported_at canonical_namespace scope key version "
    "integrity_hash integrity_check preparation_status authority authoritative "
    "authoritative_scope authority_classification source_kind source_evidence_hash "
    "provenance_refs ref kind evidence_hash result_ref result_refs canonical_ref_hint "
    "canonical_revision_hint freshness mempalace graphify canonical_sqlite "
    "indexed_canonical_revision current_canonical_revision indexed_repo_commit "
    "current_repo_commit source_snapshot_id indexed_at stale index_namespace score results "
    "conflicts events actor_ref reason_code approval_ref approval_tier "
    "confirmed_via_exact_ref confirmed_canonical_revision existing_canonical_ref "
    "existing_event_ref dedupe_key idempotency_key idempotency_classification "
    "proposal_event manifest record_count bundle_id bundle_hash receipt_id file_sha256 "
    "aggregate_counts active_fact_count event_count tombstone_count wal_enabled item_count "
    "relationship_count confirmation_required privacy_classification provenance repo "
    "issue_number comment_id supersession supersedes record preference_summary "
    "operating_rules id category statement record_type canonical_write_performed "
    "operator_approval_required value_hash".split()
)


class MemoryGatewayPolicyError(ValueError):
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
    if not _SAFE_ID_RE.fullmatch(namespace):
        raise MemoryGatewayPolicyError("INVALID_NAMESPACE", "namespace is malformed")
    if namespace not in ALLOWED_NAMESPACES:
        raise MemoryGatewayPolicyError("UNKNOWN_NAMESPACE", "namespace is not registered")
    if namespace not in allowed_namespaces:
        raise MemoryGatewayPolicyError("NAMESPACE_NOT_AUTHORIZED", "namespace is not authorized")
    return namespace


def validate_public_payload(value: Any) -> Any:
    try:
        return _project(validate_memory_value(value))
    except MemoryValueError as exc:
        raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc


def sanitized_actor_ref(actor_ref: object) -> str:
    return _safe_id(actor_ref, "INVALID_ACTOR_REF", "actor_ref")


def sanitized_reason_code(reason_code: object) -> str:
    return _safe_id(reason_code, "INVALID_REASON_CODE", "reason_code")


def _safe_id(value: object, reason: str, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise MemoryGatewayPolicyError(reason, f"{field} is malformed")
    return value


def _project(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key not in _PUBLIC_FIELDS:
                continue
            if key in _ID_FIELDS:
                _safe_id(child, "UNSAFE_PUBLIC_PAYLOAD", key)
            result[key] = _project(child)
        return result
    if isinstance(value, list):
        return [_project(child) for child in value]
    return value

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Mapping

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
        "memory.private_mutate",
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
_ID_FIELDS = frozenset({"namespace", "project_id", "lookup_key", "fact_id", "namespace_token", "scope_token", "key_token"})
_PROVENANCE_FIELDS = frozenset(
    {
        "ref",
        "kind",
        "evidence_hash",
        "source_ref",
        "source_path",
        "indexed_repo_commit",
        "current_repo_commit",
        "stale",
    }
)
_FRESHNESS_FIELDS = frozenset(
    {
        "indexed_canonical_revision",
        "current_canonical_revision",
        "source_snapshot_id",
        "indexed_repo_commit",
        "current_repo_commit",
        "indexed_at",
        "stale",
        "index_namespace",
        "project_id",
        "graph_schema_version",
        "graphify_runtime_version",
        "authoritative",
        "authority_classification",
    }
)
_INDEX_STATUS_FIELDS = frozenset(
    {
        "state",
        "indexed_canonical_revision",
        "current_canonical_revision",
        "active_fact_count",
        "event_count",
        "tombstone_count",
        "wal_enabled",
        "item_count",
        "relationship_count",
        "authoritative",
        "project_id",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "authoritative",
        "authoritative_scope",
        "authority_classification",
        "namespace",
        "project_id",
        "result_ref",
        "result_refs",
        "canonical_ref",
        "canonical_ref_hint",
        "canonical_revision",
        "canonical_revision_hint",
        "relationship_kind",
        "query_kind",
        "source_ref",
        "target_ref",
        "source_kind",
        "source_attribution",
        "provenance_refs",
        "freshness",
        "indexed_canonical_revision",
        "current_canonical_revision",
        "indexed_repo_commit",
        "current_repo_commit",
        "indexed_at",
        "graphify_runtime_version",
        "graph_schema_version",
        "stale",
        "score",
        "confirmation_required",
        "related_refs",
        "source_snapshot_id",
        "bounded_text",
    }
)
_EXACT_FIELDS = frozenset(
    {
        "namespace",
        "project_id",
        "canonical_ref",
        "canonical_revision",
        "created_revision",
        "imported_at",
        "fact_type",
        "canonical_namespace",
        "scope",
        "key",
        "version",
        "integrity_hash",
        "provenance_refs",
        "authoritative",
        "authority_classification",
        "source_kind",
    }
)
_CONFLICT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "event_ref",
        "conflict_ref",
        "dedupe_key",
        "namespace",
        "project_id",
        "existing_canonical_ref",
        "existing_event_ref",
        "reason_code",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "state",
        "event_type",
        "event_ref",
        "event_class",
        "override_ref",
        "namespace",
        "project_id",
        "object_id",
        "entity_scope",
        "fact_type",
        "normalized_target",
        "canonical_ref",
        "canonical_revision",
        "actor_ref",
        "reason_code",
        "approval_ref",
        "approval_tier",
        "source_evidence_hash",
        "confirmed_via_exact_ref",
        "confirmed_canonical_revision",
        "dedupe_key",
        "idempotency_key",
        "idempotency_classification",
        "value_hash",
        "operation",
        "expected_revision",
        "source_hash",
        "bundle_hash",
        "bundle_id",
        "file_sha256",
        "record_count",
    }
)
_MANIFEST_PREPARE_FIELDS = frozenset(
    {
        "project_id",
        "preparation_status",
        "authority",
        "authoritative",
        "source_kind",
        "integrity_hash",
        "integrity_check",
    }
)
_MANIFEST_IMPORT_FIELDS = frozenset(
    {
        "project_id",
        "canonical_ref",
        "canonical_revision",
        "created_revision",
        "imported_at",
        "key",
        "version",
        "integrity_hash",
        "idempotency_classification",
        "snapshot_status",
        "read_back_status",
        "rollback_status",
        "authoritative",
        "authority_classification",
        "source_kind",
        "schema",
        "status",
        "namespace_token",
        "scope_token",
        "key_token",
    }
)


_QUERY_REPORT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "query_ref",
        "query_kind",
        "public_safe",
        "synthetic_only",
        "aggregate_counts",
        "error_class",
        "next_operator_action",
    }
)

_PUBLIC_FIELDS = frozenset(
    """
    schema status state event_type event_ref event_class conflict_ref override_ref
    namespace project_id lookup_key fact_id namespace_token scope_token key_token
    object_id entity_scope fact_type normalized_target canonical_ref
    canonical_revision created_revision imported_at canonical_namespace scope key version
    integrity_hash integrity_check preparation_status authority authoritative
    authoritative_scope authority_classification source_kind source_evidence_hash
    provenance_refs ref kind evidence_hash source_attribution source_ref source_path
    target_ref result_ref result_refs related_refs canonical_ref_hint
    canonical_revision_hint freshness mempalace graphify canonical_sqlite
    indexed_canonical_revision current_canonical_revision indexed_repo_commit
    current_repo_commit source_snapshot_id indexed_at stale index_namespace score
    results conflicts events actor_ref reason_code approval_ref approval_tier
    confirmed_via_exact_ref confirmed_canonical_revision existing_canonical_ref
    existing_event_ref dedupe_key idempotency_key idempotency_classification
    proposal_event record_count bundle_id bundle_hash receipt_id file_sha256
    operation expected_revision source_hash imported_canonical_refs
    aggregate_counts active_fact_count event_count tombstone_count wal_enabled
    item_count relationship_count confirmation_required privacy_classification
    provenance repo issue_number comment_id supersession supersedes record
    preference_summary operating_rules id category statement record_type
    canonical_write_performed operator_approval_required value_hash
    snapshot_status read_back_status rollback_status relationship_kind query_kind
    graphify_runtime_version graph_schema_version query_report query_ref public_safe
    synthetic_only node_count edge_count stale_count blocked_count
    missing_provenance_count error_class next_operator_action bounded_text
 operation decision allowed reason gateway command contract_version payload conflict_count freshness_checked proposal_status classification
 expected_revision source_hash imported_canonical_refs indexes degraded_indexes
    """.split()
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


def build_command_receipt(command_suffix: str, payload: Mapping[str, Any]) -> dict[str, object]:
    if command_suffix not in _COMMAND_RECEIPT_BUILDERS:
        raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command receipt builder is not registered")
    try:
        bounded = validate_memory_value(payload)
    except MemoryValueError as exc:
        raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
    if not isinstance(bounded, Mapping):
        raise MemoryGatewayPolicyError("INVALID_RECEIPT", "command receipt payload must be an object")
    return _COMMAND_RECEIPT_BUILDERS[command_suffix](bounded)


def validate_public_payload(value: Any) -> Any:
    try:
        bounded = validate_memory_value(value)
    except MemoryValueError as exc:
        raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
    return _project_public_payload(bounded)


def _project_public_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key not in _PUBLIC_FIELDS:
                continue
            if key in _ID_FIELDS and child is not None:
                _safe_id(child, "UNSAFE_PUBLIC_PAYLOAD", key)
            result[key] = _project_public_payload(child)
        return result
    if isinstance(value, list):
        return [_project_public_payload(child) for child in value]
    return value


def sanitized_actor_ref(actor_ref: object) -> str:
    return _safe_id(actor_ref, "INVALID_ACTOR_REF", "actor_ref")


def sanitized_reason_code(reason_code: object) -> str:
    return _safe_id(reason_code, "INVALID_REASON_CODE", "reason_code")


def _safe_id(value: object, reason: str, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise MemoryGatewayPolicyError(reason, f"{field} is malformed")
    return value


def _typed_mapping(value: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, object] = {}
    for key in fields:
        if key not in value:
            continue
        child = value[key]
        if key in _ID_FIELDS:
            _safe_id(child, "UNSAFE_PUBLIC_PAYLOAD", key)
        result[key] = _typed_child(key, child)
    return result


def _typed_child(key: str, child: object) -> object:
    if key in {"provenance_refs", "source_attribution"}:
        return [_typed_mapping(item, _PROVENANCE_FIELDS) for item in _as_list(child)]
    if key == "freshness":
        return _typed_mapping(child, _FRESHNESS_FIELDS)
    if key in {"mempalace", "graphify", "canonical_sqlite"}:
        return _typed_mapping(child, _FRESHNESS_FIELDS | _INDEX_STATUS_FIELDS)
    if key == "indexes":
        return {
            name: _typed_mapping(state, _FRESHNESS_FIELDS | _INDEX_STATUS_FIELDS)
            for name, state in child.items()
            if isinstance(name, str) and isinstance(state, Mapping)
        } if isinstance(child, Mapping) else {}
    if key == "results":
        return [_typed_mapping(item, _RESULT_FIELDS) for item in _as_list(child)]
    if key == "conflicts":
        return [_typed_mapping(item, _CONFLICT_FIELDS) for item in _as_list(child)]
    if key == "events":
        return [_typed_mapping(item, _EVENT_FIELDS) for item in _as_list(child)]
    if key == "proposal_event":
        return _typed_mapping(child, _EVENT_FIELDS)
    if key == "query_report":
        return _typed_mapping(child, _QUERY_REPORT_FIELDS)
    if key == "aggregate_counts":
        return _typed_mapping(
            child,
            frozenset(
                {
                    "active_fact_count",
                    "event_count",
                    "tombstone_count",
                    "item_count",
                    "relationship_count",
                    "node_count",
                    "edge_count",
                    "stale_count",
                    "blocked_count",
                    "missing_provenance_count",
                    "record_count",
                }
            ),
        )
    if isinstance(child, Mapping):
        return {}
    if isinstance(child, list):
        return [item for item in child if not isinstance(item, Mapping | list)]
    return child


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _lookup_exact_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, _EXACT_FIELDS)


def _semantic_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"results", "authoritative", "confirmation_required"}))


def _graph_query_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(
        payload,
        frozenset(
            {
                "schema",
                "namespace",
                "project_id",
                "authoritative",
                "authority_classification",
                "results",
                "query_report",
            }
        ),
    )


def _memory_freshness_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"project_id", "mempalace", "canonical_sqlite", "authoritative"}))


def _graph_freshness_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"project_id", "graphify", "authoritative"}))


def _conflicts_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"event_class", "conflicts"}))


def _override_history_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"event_class", "events"}))


def _audit_log_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"events"}))


def _prepare_manifest_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, _MANIFEST_PREPARE_FIELDS)


def _import_manifest_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, _MANIFEST_IMPORT_FIELDS)


def _proposal_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(payload, frozenset({"project_id", "proposal_event", "idempotency_classification"}))


def _private_mutation_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    return _typed_mapping(
        payload,
        frozenset(
            {
                "schema",
                "status",
                "operation",
                "project_id",
                "idempotency_key",
                "idempotency_classification",
                "expected_revision",
                "canonical_revision",
                "canonical_sqlite",
                "canonical_ref",
                "source_hash",
                "actor_ref",
                "reason_code",
                "approval_ref",
                "indexes",
                "degraded_indexes",
                "bundle_id",
                "bundle_hash",
                "file_sha256",
                "record_count",
                "imported_canonical_refs",
                "error_class",
            }
        ),
    )


_COMMAND_RECEIPT_BUILDERS: dict[str, Callable[[Mapping[str, Any]], dict[str, object]]] = {
    "memory.lookup_exact": _lookup_exact_receipt,
    "memory.search_semantic": _semantic_receipt,
    "memory.get_conflicts": _conflicts_receipt,
    "memory.get_override_history": _override_history_receipt,
    "memory.get_audit_log": _audit_log_receipt,
    "memory.get_index_freshness": _memory_freshness_receipt,
    "memory.prepare_canonical_manifest": _prepare_manifest_receipt,
    "memory.import_canonical_manifest": _import_manifest_receipt,
    "memory.private_mutate": _private_mutation_receipt,
    "graph.query_code": _graph_query_receipt,
    "graph.get_index_freshness": _graph_freshness_receipt,
    "memory.propose_patch": _proposal_receipt,
}

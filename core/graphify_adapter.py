from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_gateway_policy import validate_public_payload


GRAPHIFY_RESULT_SCHEMA = "skeleton.graphify_result.v1"
GRAPHIFY_FIXTURE_SCHEMA = "skeleton.graphify_synthetic_fixture.v1"
GRAPHIFY_SYNTHETIC_NAMESPACE = "skeleton"
GRAPHIFY_SYNTHETIC_PROJECT_ID = "graphify_synthetic"
GRAPHIFY_SCHEMA_VERSION = "skeleton.graphify.synthetic_graph.v1"
GRAPHIFY_ALLOWED_QUERY_KINDS = frozenset(
    {
        "module_relationship",
        "schema_relationship",
        "test_relationship",
        "dependency_relationship",
        "provenance_relationship",
    }
)
GRAPHIFY_MAX_RESULTS = 5
GRAPHIFY_RUNTIME_PROFILE = "synthetic-fixture-readonly"
GRAPHIFY_RUNTIME_VERSION = "graphify-unavailable-v1"
GRAPHIFY_RUNTIME_AVAILABLE = False

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_COMMIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{6,127}$")
_ISO_UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_PUBLIC_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()#-]{0,179}$")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_REPO_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_FORBIDDEN_MARKERS = (
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
_REPORT_KIND_BY_QUERY_KIND = {
    "module_relationship": "relationship_overview",
    "schema_relationship": "relationship_overview",
    "test_relationship": "relationship_overview",
    "dependency_relationship": "dependency_overview",
    "provenance_relationship": "provenance_overview",
}


class GraphifyAdapterError(ValueError):
    """Raised when the bounded synthetic Graphify adapter fails closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SyntheticRelationship:
    result_ref: str
    query_kind: str
    relationship_kind: str
    source_ref: str
    source_path: str
    evidence_hash: str
    related_refs: tuple[str, ...]
    deleted: bool = False
    blocked: bool = False


@dataclass(frozen=True)
class GraphifySyntheticFixture:
    namespace: str
    project_id: str
    runtime_profile: str
    runtime_version: str
    runtime_available: bool
    index_namespace: str
    indexed_repo_commit: str
    current_repo_commit: str
    indexed_at: str
    graph_schema_version: str
    relationships: tuple[SyntheticRelationship, ...]


class GraphifyAdapter:
    """Read-only public-safe adapter for a bounded synthetic Graphify fixture."""

    def __init__(self, fixture_data: Mapping[str, Any]) -> None:
        self._fixture = load_synthetic_fixture(fixture_data)

    @property
    def fixture(self) -> GraphifySyntheticFixture:
        return self._fixture

    def query_code(
        self,
        *,
        namespace: str,
        project_id: str,
        query: str,
        limit: int = GRAPHIFY_MAX_RESULTS,
    ) -> dict[str, object]:
        self._authorize_scope(namespace=namespace, project_id=project_id)
        query_kind = _query_kind(query)
        limit = _bounded_limit(limit)
        matches = [
            relationship
            for relationship in self._fixture.relationships
            if relationship.query_kind == query_kind and not relationship.deleted
        ]
        if len(matches) > limit:
            raise GraphifyAdapterError("GRAPHIFY_RESULT_LIMIT_EXCEEDED", "query result count exceeds limit")

        stale = self._fixture.indexed_repo_commit != self._fixture.current_repo_commit
        results = [
            {
                "schema": GRAPHIFY_RESULT_SCHEMA,
                "authoritative": False,
                "authority_classification": "derived_code_graph",
                "namespace": self._fixture.namespace,
                "project_id": self._fixture.project_id,
                "result_ref": relationship.result_ref,
                "relationship_kind": relationship.relationship_kind,
                "query_kind": relationship.query_kind,
                "related_refs": [_public_ref_from_path(path) for path in relationship.related_refs],
                "source_attribution": [
                    {
                        "source_ref": relationship.source_ref,
                        "kind": "synthetic_code_relationship",
                        "evidence_hash": relationship.evidence_hash,
                    }
                ],
                "provenance_refs": [
                    {
                        "ref": relationship.source_ref,
                        "kind": "code_graph",
                        "evidence_hash": relationship.evidence_hash,
                        "indexed_repo_commit": self._fixture.indexed_repo_commit,
                        "current_repo_commit": self._fixture.current_repo_commit,
                        "stale": stale,
                    }
                ],
                "indexed_repo_commit": self._fixture.indexed_repo_commit,
                "current_repo_commit": self._fixture.current_repo_commit,
                "indexed_at": self._fixture.indexed_at,
                "graph_schema_version": self._fixture.graph_schema_version,
                "stale": stale,
            }
            for relationship in sorted(matches, key=lambda item: item.result_ref)
        ]
        report = _query_report(
            query_kind=query_kind,
            query_ref=f"graph-query-{query_kind}",
            results=results,
            relationship_count=len(matches),
            fixture=self._fixture,
        )
        payload = {
            "schema": "skeleton.graphify_query_response.v1",
            "namespace": self._fixture.namespace,
            "project_id": self._fixture.project_id,
            "authoritative": False,
            "authority_classification": "derived_code_graph",
            "results": results,
            "query_report": report,
        }
        return validate_public_payload(payload)

    def get_index_freshness(self, *, namespace: str, project_id: str) -> dict[str, object]:
        self._authorize_scope(namespace=namespace, project_id=project_id)
        freshness = {
            "indexed_repo_commit": self._fixture.indexed_repo_commit,
            "current_repo_commit": self._fixture.current_repo_commit,
            "indexed_at": self._fixture.indexed_at,
            "stale": self._fixture.indexed_repo_commit != self._fixture.current_repo_commit,
            "index_namespace": self._fixture.index_namespace,
            "project_id": self._fixture.project_id,
            "runtime_profile": self._fixture.runtime_profile,
            "runtime_version": self._fixture.runtime_version,
            "runtime_available": self._fixture.runtime_available,
            "graph_schema_version": self._fixture.graph_schema_version,
            "authoritative": False,
            "authority_classification": "derived_code_graph",
        }
        return validate_public_payload(freshness)

    def delete_item(self, result_ref: str) -> "GraphifyAdapter":
        replacement = _fixture_to_dict(self._fixture)
        for relationship in replacement["relationships"]:
            if relationship["result_ref"] == result_ref:
                relationship["deleted"] = True
                return GraphifyAdapter(replacement)
        raise GraphifyAdapterError("GRAPHIFY_ITEM_NOT_FOUND", "relationship item not found")

    def _authorize_scope(self, *, namespace: str, project_id: str) -> None:
        if namespace != GRAPHIFY_SYNTHETIC_NAMESPACE or project_id != GRAPHIFY_SYNTHETIC_PROJECT_ID:
            raise GraphifyAdapterError("GRAPHIFY_SCOPE_NOT_AUTHORIZED", "scope is not authorized")


def load_synthetic_fixture(data: Mapping[str, Any]) -> GraphifySyntheticFixture:
    if not isinstance(data, Mapping):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "fixture must be an object")
    allowed = {
        "schema",
        "namespace",
        "project_id",
        "runtime_profile",
        "runtime_version",
        "runtime_available",
        "index_namespace",
        "indexed_repo_commit",
        "current_repo_commit",
        "indexed_at",
        "graph_schema_version",
        "relationships",
    }
    _require_keys(data, allowed, allowed, "fixture")
    if data["schema"] != GRAPHIFY_FIXTURE_SCHEMA:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "fixture schema is invalid")
    namespace = _safe_token(data["namespace"], "namespace")
    project_id = _safe_token(data["project_id"], "project_id")
    if namespace != GRAPHIFY_SYNTHETIC_NAMESPACE or project_id != GRAPHIFY_SYNTHETIC_PROJECT_ID:
        raise GraphifyAdapterError("GRAPHIFY_SCOPE_NOT_AUTHORIZED", "fixture scope is not authorized")
    runtime_profile = _safe_token(data["runtime_profile"], "runtime_profile")
    runtime_version = _safe_token(data["runtime_version"], "runtime_version")
    runtime_available = data["runtime_available"]
    if (
        runtime_profile != GRAPHIFY_RUNTIME_PROFILE
        or runtime_version != GRAPHIFY_RUNTIME_VERSION
        or runtime_available is not GRAPHIFY_RUNTIME_AVAILABLE
    ):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "runtime profile is not pinned")
    index_namespace = _safe_token(data["index_namespace"], "index_namespace")
    if index_namespace != GRAPHIFY_SYNTHETIC_NAMESPACE:
        raise GraphifyAdapterError("GRAPHIFY_SCOPE_NOT_AUTHORIZED", "index namespace is not authorized")
    relationships_value = data["relationships"]
    if not isinstance(relationships_value, list) or not relationships_value:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "relationships must be a non-empty array")
    if len(relationships_value) > 32:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_TOO_LARGE", "fixture relationship count exceeds bound")
    relationships = tuple(_relationship(item) for item in relationships_value)
    fixture = GraphifySyntheticFixture(
        namespace=namespace,
        project_id=project_id,
        runtime_profile=runtime_profile,
        runtime_version=runtime_version,
        runtime_available=runtime_available,
        index_namespace=index_namespace,
        indexed_repo_commit=_commit(data["indexed_repo_commit"], "indexed_repo_commit"),
        current_repo_commit=_commit(data["current_repo_commit"], "current_repo_commit"),
        indexed_at=_timestamp(data["indexed_at"], "indexed_at"),
        graph_schema_version=_safe_token(data["graph_schema_version"], "graph_schema_version"),
        relationships=relationships,
    )
    validate_public_payload(_fixture_to_dict(fixture))
    return fixture


def _relationship(value: object) -> SyntheticRelationship:
    if not isinstance(value, Mapping):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "relationship must be an object")
    allowed = {
        "result_ref",
        "query_kind",
        "relationship_kind",
        "source_ref",
        "source_path",
        "evidence_hash",
        "related_refs",
        "deleted",
        "blocked",
    }
    _require_keys(
        value,
        {
            "result_ref",
            "query_kind",
            "relationship_kind",
            "source_ref",
            "source_path",
            "evidence_hash",
            "related_refs",
            "deleted",
        },
        allowed,
        "relationship",
    )
    query_kind = _query_kind(value["query_kind"])
    related_refs = value["related_refs"]
    if not isinstance(related_refs, list) or not related_refs or len(related_refs) > 6:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "related_refs must be a bounded array")
    source_ref = _safe_token(value["source_ref"], "source_ref")
    if not source_ref.startswith("src-synthetic-"):
        raise GraphifyAdapterError("GRAPHIFY_MISSING_PROVENANCE", "synthetic source provenance is required")
    if not isinstance(value["deleted"], bool):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "deleted must be boolean")
    blocked = value.get("blocked", False)
    if not isinstance(blocked, bool):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "blocked must be boolean")
    return SyntheticRelationship(
        result_ref=_safe_token(value["result_ref"], "result_ref"),
        query_kind=query_kind,
        relationship_kind=_public_text(value["relationship_kind"], "relationship_kind"),
        source_ref=source_ref,
        source_path=_repo_relative_path(value["source_path"], "source_path"),
        evidence_hash=_evidence_hash(value["evidence_hash"]),
        related_refs=tuple(_repo_relative_path(item, "related_ref") for item in related_refs),
        deleted=value["deleted"],
        blocked=blocked,
    )


def _query_report(
    *,
    query_kind: str,
    query_ref: str,
    results: list[dict[str, object]],
    relationship_count: int,
    fixture: GraphifySyntheticFixture,
) -> dict[str, object]:
    active = [relationship for relationship in fixture.relationships if not relationship.deleted]
    return {
        "schema": "skeleton.graph_memory.query_report.v0",
        "status": "DONE",
        "query_ref": query_ref,
        "query_kind": _REPORT_KIND_BY_QUERY_KIND[query_kind],
        "public_safe": True,
        "synthetic_only": True,
        "aggregate_counts": {
            "node_count": len({ref for relationship in active for ref in relationship.related_refs}),
            "edge_count": len(active),
            "relationship_count": relationship_count,
            "stale_count": len(results) if fixture.indexed_repo_commit != fixture.current_repo_commit else 0,
            "blocked_count": sum(1 for relationship in active if relationship.blocked),
            "missing_provenance_count": 0,
        },
        "error_class": None,
        "next_operator_action": "review_graph_provenance" if query_kind == "provenance_relationship" else "none",
    }


def _require_keys(value: Mapping[str, Any], required: set[str], allowed: set[str], label: str) -> None:
    keys = set(value)
    if missing := required - keys:
        if "source_ref" in missing or "evidence_hash" in missing:
            raise GraphifyAdapterError("GRAPHIFY_MISSING_PROVENANCE", f"{label} is missing provenance")
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{label} is missing required fields")
    if extra := keys - allowed:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{label} contains unsupported fields")


def _query_kind(value: object) -> str:
    if not isinstance(value, str) or value not in GRAPHIFY_ALLOWED_QUERY_KINDS:
        raise GraphifyAdapterError("GRAPHIFY_QUERY_KIND_NOT_ALLOWLISTED", "query kind is not allowlisted")
    return value


def _bounded_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > GRAPHIFY_MAX_RESULTS:
        raise GraphifyAdapterError("GRAPHIFY_INVALID_LIMIT", "limit must be an integer from 1 to 5")
    return value


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be a safe token")
    _reject_private_markers(value, field)
    return value


def _commit(value: object, field: str) -> str:
    if not isinstance(value, str) or not _COMMIT_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be a safe commit token")
    _reject_private_markers(value, field)
    return value


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ISO_UTC_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be an ISO UTC timestamp")
    return value


def _public_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not _PUBLIC_TEXT_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be bounded public text")
    _reject_private_markers(value, field)
    return value


def _repo_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 180:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be a bounded repository-relative path")
    lowered = value.lower()
    if (
        value.startswith(("/", "~"))
        or "\\" in value
        or _URL_SCHEME_RE.match(value)
        or any(
            marker in lowered
            for marker in (".sqlite", ".db", "secret", "token", "password", "credential", "private")
        )
    ):
        raise GraphifyAdapterError("GRAPHIFY_PRIVATE_VALUE_REJECTED", f"{field} contains private-looking value")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be a bounded repository-relative path")
    if not all(_REPO_PATH_SEGMENT_RE.fullmatch(part) for part in parts):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be a bounded repository-relative path")
    return value


def _evidence_hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_MISSING_PROVENANCE", "evidence hash is required")
    return value


def _reject_private_markers(value: str, field: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        raise GraphifyAdapterError("GRAPHIFY_PRIVATE_VALUE_REJECTED", f"{field} contains private-looking value")


def _fixture_to_dict(fixture: GraphifySyntheticFixture) -> dict[str, object]:
    return {
        "schema": GRAPHIFY_FIXTURE_SCHEMA,
        "namespace": fixture.namespace,
        "project_id": fixture.project_id,
        "runtime_profile": fixture.runtime_profile,
        "runtime_version": fixture.runtime_version,
        "runtime_available": fixture.runtime_available,
        "index_namespace": fixture.index_namespace,
        "indexed_repo_commit": fixture.indexed_repo_commit,
        "current_repo_commit": fixture.current_repo_commit,
        "indexed_at": fixture.indexed_at,
        "graph_schema_version": fixture.graph_schema_version,
        "relationships": [
            {
                "result_ref": relationship.result_ref,
                "query_kind": relationship.query_kind,
                "relationship_kind": relationship.relationship_kind,
                "source_ref": relationship.source_ref,
                "source_path": _public_ref_from_path(relationship.source_path),
                "evidence_hash": relationship.evidence_hash,
                "related_refs": [_public_ref_from_path(path) for path in relationship.related_refs],
                "deleted": relationship.deleted,
                "blocked": relationship.blocked,
            }
            for relationship in fixture.relationships
        ],
    }


def _public_ref_from_path(value: str) -> str:
    return "repo-" + value.replace("/", "-").replace(".", "-")


def dumps_public(value: Mapping[str, Any]) -> str:
    """Serialize only after public payload validation for tests and docs tooling."""

    return json.dumps(validate_public_payload(value), allow_nan=False, sort_keys=True)

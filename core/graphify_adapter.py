from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway_policy import validate_public_payload
from core.private_memory_history import bytes_hash, canonical_json, utc_now


GRAPHIFY_RESULT_SCHEMA = "skeleton.graphify_result.v1"
LOCAL_GRAPHIFY_INDEX_SCHEMA = "skeleton.private_memory_stack.graphify_index.v1"
GRAPHIFY_FIXTURE_SCHEMA = "graphify.graph.json"
GRAPHIFY_SYNTHETIC_NAMESPACE = "skeleton"
GRAPHIFY_SYNTHETIC_PROJECT_ID = "graphify_synthetic"
GRAPHIFY_SCHEMA_VERSION = "skeleton.graphify.synthetic_graph.v1"
GRAPHIFY_RUNTIME_VERSION = "0.8.44"
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

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_NODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:<>-]{0,255}$")
_SAFE_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,255}$")
_COMMIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{6,127}$")
_ISO_UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_PUBLIC_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()#-]{0,179}$")
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


class LocalGraphifyIndex:
    """Local-private derived graph index that never writes canonical SQLite."""

    def __init__(self, index_path: str | Path) -> None:
        self.index_path = Path(index_path)
        self._index = self._load()

    @classmethod
    def rebuild_from_facts(
        cls,
        index_path: str | Path,
        *,
        facts: list[Mapping[str, Any]],
        canonical_revision: int,
    ) -> dict[str, object]:
        relationships = []
        for fact in facts:
            canonical_ref = str(fact["canonical_ref"])
            namespace = str(fact["namespace"])
            revision = int(fact["canonical_revision"])
            attribution = {
                "canonical_ref": canonical_ref,
                "canonical_revision": revision,
                "source_kind": "canonical_sqlite",
                "value_hash": str(fact["value_hash"]),
            }
            relationships.append(
                _local_relationship(
                    source=canonical_ref,
                    target=f"namespace:{namespace}",
                    relationship_kind="namespace_member",
                    query_terms=[namespace, "namespace", "member"],
                    attribution=attribution,
                )
            )
            value = fact["value"]
            for tag in _safe_tags(value):
                relationships.append(
                    _local_relationship(
                        source=canonical_ref,
                        target=f"tag:{tag}",
                        relationship_kind="tagged",
                        query_terms=[tag, "tagged", namespace],
                        attribution=attribution,
                    )
                )
            for explicit in _explicit_relationships(value):
                relationships.append(
                    _local_relationship(
                        source=canonical_ref,
                        target=explicit["target"],
                        relationship_kind=explicit["kind"],
                        query_terms=[explicit["target"], explicit["kind"], namespace],
                        attribution=attribution,
                    )
                )
        payload = {
            "schema": LOCAL_GRAPHIFY_INDEX_SCHEMA,
            "authoritative": False,
            "authority_classification": "derived_relationship_graph",
            "current_canonical_revision": canonical_revision,
            "indexed_canonical_revision": canonical_revision,
            "indexed_at": utc_now(),
            "relationship_count": len(relationships),
            "relationships": sorted(relationships, key=lambda item: (item["source"], item["target"], item["kind"])),
        }
        payload["index_hash"] = bytes_hash(canonical_json(payload).encode("utf-8"))
        atomic_write_json_private(Path(index_path), payload)
        return cls.status(index_path, current_canonical_revision=canonical_revision)

    @staticmethod
    def status(index_path: str | Path, *, current_canonical_revision: int) -> dict[str, object]:
        path = Path(index_path)
        if not path.is_file():
            return {"state": "STALE", "indexed_canonical_revision": 0, "relationship_count": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _validate_local_graph_index(data)
            indexed = int(data.get("indexed_canonical_revision", 0))
            return {
                "state": "READY" if indexed == current_canonical_revision else "STALE",
                "indexed_canonical_revision": indexed,
                "relationship_count": int(data.get("relationship_count", 0)),
                "authoritative": False,
            }
        except Exception:
            return {
                "state": "BLOCKED",
                "indexed_canonical_revision": 0,
                "relationship_count": 0,
                "authoritative": False,
            }

    def query(self, *, query: str, limit: int = 5) -> dict[str, object]:
        terms = set(_local_terms(query))
        limit = _bounded_limit(limit)
        scored = []
        for relationship in self._index["relationships"]:
            haystack = set(relationship["query_terms"])
            if not terms & haystack:
                continue
            score = len(terms & haystack)
            scored.append(
                (
                    -score,
                    relationship["source"],
                    relationship["target"],
                    {
                        "schema": GRAPHIFY_RESULT_SCHEMA,
                        "authoritative": False,
                        "authority_classification": "derived_relationship_graph",
                        "canonical_ref": relationship["source"],
                        "canonical_revision": relationship["source_attribution"]["canonical_revision"],
                        "relationship_kind": relationship["kind"],
                        "source_ref": relationship["source"],
                        "target_ref": relationship["target"],
                        "source_attribution": [deepcopy(relationship["source_attribution"])],
                        "indexed_canonical_revision": self._index["indexed_canonical_revision"],
                        "current_canonical_revision": self._index["current_canonical_revision"],
                        "stale": self._index["indexed_canonical_revision"] != self._index["current_canonical_revision"],
                    },
                )
            )
        return {
            "schema": "skeleton.private_memory_stack.relation_query.v1",
            "authoritative": False,
            "authority_classification": "derived_relationship_graph",
            "confirmation_required": "exact_get_reads_canonical_sqlite",
            "results": [item for _, _, _, item in sorted(scored)[:limit]],
        }

    def _load(self) -> dict[str, Any]:
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        _validate_local_graph_index(data)
        return data


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
    indexed_repo_commit: str
    current_repo_commit: str
    indexed_at: str
    graphify_runtime_version: str
    graph_schema_version: str
    relationships: tuple[SyntheticRelationship, ...]


class GraphifyAdapter:
    """Read-only public-safe adapter for a bounded synthetic Graphify fixture."""

    def __init__(self, fixture_data: Mapping[str, Any]) -> None:
        self._fixture = load_synthetic_fixture(fixture_data)

    @classmethod
    def _from_fixture(cls, fixture: GraphifySyntheticFixture) -> "GraphifyAdapter":
        adapter = cls.__new__(cls)
        adapter._fixture = fixture
        return adapter

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
                "related_refs": list(relationship.related_refs),
                "source_attribution": [
                    {
                        "source_ref": relationship.source_ref,
                        "source_path": relationship.source_path,
                        "kind": "synthetic_code_relationship",
                        "evidence_hash": relationship.evidence_hash,
                    }
                ],
                "provenance_refs": [
                    {
                        "ref": relationship.source_ref,
                        "source_path": relationship.source_path,
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
                "graphify_runtime_version": self._fixture.graphify_runtime_version,
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
            "index_namespace": self._fixture.namespace,
            "project_id": self._fixture.project_id,
            "graph_schema_version": self._fixture.graph_schema_version,
            "graphify_runtime_version": self._fixture.graphify_runtime_version,
            "authoritative": False,
            "authority_classification": "derived_code_graph",
        }
        return validate_public_payload(freshness)

    def delete_item(self, result_ref: str) -> "GraphifyAdapter":
        relationships = []
        found = False
        for relationship in self._fixture.relationships:
            if relationship.result_ref == result_ref:
                relationships.append(replace(relationship, deleted=True))
                found = True
            else:
                relationships.append(relationship)
        if found:
            return GraphifyAdapter._from_fixture(replace(self._fixture, relationships=tuple(relationships)))
        raise GraphifyAdapterError("GRAPHIFY_ITEM_NOT_FOUND", "relationship item not found")

    def _authorize_scope(self, *, namespace: str, project_id: str) -> None:
        if namespace != GRAPHIFY_SYNTHETIC_NAMESPACE or project_id != GRAPHIFY_SYNTHETIC_PROJECT_ID:
            raise GraphifyAdapterError("GRAPHIFY_SCOPE_NOT_AUTHORIZED", "scope is not authorized")


def load_synthetic_fixture(data: Mapping[str, Any]) -> GraphifySyntheticFixture:
    if not isinstance(data, Mapping):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "fixture must be an object")
    graph_meta = _graph_metadata(data)
    namespace = _safe_token(graph_meta["namespace"], "namespace")
    project_id = _safe_token(graph_meta["project_id"], "project_id")
    if namespace != GRAPHIFY_SYNTHETIC_NAMESPACE or project_id != GRAPHIFY_SYNTHETIC_PROJECT_ID:
        raise GraphifyAdapterError("GRAPHIFY_SCOPE_NOT_AUTHORIZED", "fixture scope is not authorized")
    runtime_version = _runtime_version(graph_meta.get("graphify_runtime_version", graph_meta.get("graphify_version")))
    nodes = _node_paths(data.get("nodes"))
    relationships_value = _graph_edges(data)
    if len(relationships_value) > 32:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_TOO_LARGE", "fixture relationship count exceeds bound")
    relationships = tuple(_relationship(item, nodes) for item in relationships_value)
    if not relationships:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "relationships must be a non-empty array")
    fixture = GraphifySyntheticFixture(
        namespace=namespace,
        project_id=project_id,
        indexed_repo_commit=_commit(graph_meta["indexed_repo_commit"], "indexed_repo_commit"),
        current_repo_commit=_commit(graph_meta["current_repo_commit"], "current_repo_commit"),
        indexed_at=_timestamp(graph_meta["indexed_at"], "indexed_at"),
        graphify_runtime_version=runtime_version,
        graph_schema_version=_safe_token(graph_meta["graph_schema_version"], "graph_schema_version"),
        relationships=relationships,
    )
    validate_public_payload(_fixture_to_dict(fixture))
    return fixture


def _graph_metadata(data: Mapping[str, Any]) -> Mapping[str, Any]:
    graph = data.get("graph", data)
    if not isinstance(graph, Mapping):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "graph metadata must be an object")
    required = {
        "namespace",
        "project_id",
        "indexed_repo_commit",
        "current_repo_commit",
        "indexed_at",
        "graph_schema_version",
    }
    _require_keys(graph, required, set(graph), "graph metadata")
    return graph


def _node_paths(value: object) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "Graphify nodes must be a non-empty array")
    nodes: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "Graphify node must be an object")
        node_id = _node_id(item.get("id"))
        raw_path = item.get("source_path", item.get("path", item.get("file")))
        nodes[node_id] = _repo_source_path(raw_path, "node source_path")
    return nodes


def _graph_edges(data: Mapping[str, Any]) -> list[object]:
    edges = data.get("links", data.get("edges"))
    if not isinstance(edges, list):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "Graphify links or edges must be an array")
    return edges


def _relationship(value: object, node_paths: Mapping[str, str]) -> SyntheticRelationship:
    if not isinstance(value, Mapping):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "relationship must be an object")
    _require_keys(
        value,
        {
            "source",
            "target",
            "result_ref",
            "query_kind",
            "relationship_kind",
            "evidence_hash",
            "deleted",
        },
        set(value),
        "relationship",
    )
    source_node = _node_id(value["source"])
    target_node = _node_id(value["target"])
    query_kind = _query_kind(value["query_kind"])
    source_path = node_paths.get(source_node)
    target_path = node_paths.get(target_node)
    if source_path is None or target_path is None:
        raise GraphifyAdapterError("GRAPHIFY_MISSING_PROVENANCE", "edge endpoints must resolve to source paths")
    if not isinstance(value["deleted"], bool):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "deleted must be boolean")
    blocked = value.get("blocked", False)
    if not isinstance(blocked, bool):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "blocked must be boolean")
    return SyntheticRelationship(
        result_ref=_safe_token(value["result_ref"], "result_ref"),
        query_kind=query_kind,
        relationship_kind=_public_text(value["relationship_kind"], "relationship_kind"),
        source_ref=f"src-{_path_ref(source_path)}",
        source_path=source_path,
        evidence_hash=_evidence_hash(value["evidence_hash"]),
        related_refs=(_path_ref(source_path), _path_ref(target_path)),
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
    return value


def _node_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_NODE_ID_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", "Graphify node id must be bounded")
    return value


def _repo_source_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_REPO_PATH_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_PRIVATE_VALUE_REJECTED", f"{field} must be a repository-relative path")
    if value.startswith("/") or value.startswith(".") or "/../" in f"/{value}/" or "//" in value:
        raise GraphifyAdapterError("GRAPHIFY_PRIVATE_VALUE_REJECTED", f"{field} must be repository-relative")
    return value


def _path_ref(value: str) -> str:
    return value.replace("/", "-").replace(".", "_")


def _runtime_version(value: object) -> str:
    if value != GRAPHIFY_RUNTIME_VERSION:
        raise GraphifyAdapterError(
            "GRAPHIFY_RUNTIME_VERSION_UNVERIFIED",
            "Graphify fixture must come from verified runtime version 0.8.44",
        )
    return GRAPHIFY_RUNTIME_VERSION


def _commit(value: object, field: str) -> str:
    if not isinstance(value, str) or not _COMMIT_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be a safe commit token")
    return value


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ISO_UTC_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be an ISO UTC timestamp")
    return value


def _public_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not _PUBLIC_TEXT_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_FIXTURE_MALFORMED", f"{field} must be bounded public text")
    return value


def _evidence_hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise GraphifyAdapterError("GRAPHIFY_MISSING_PROVENANCE", "evidence hash is required")
    return value


def _fixture_to_dict(fixture: GraphifySyntheticFixture) -> dict[str, object]:
    return {
        "schema": GRAPHIFY_FIXTURE_SCHEMA,
        "namespace": fixture.namespace,
        "project_id": fixture.project_id,
        "indexed_repo_commit": fixture.indexed_repo_commit,
        "current_repo_commit": fixture.current_repo_commit,
        "indexed_at": fixture.indexed_at,
        "graphify_runtime_version": fixture.graphify_runtime_version,
        "graph_schema_version": fixture.graph_schema_version,
        "relationships": [
            {
                "result_ref": relationship.result_ref,
                "query_kind": relationship.query_kind,
                "relationship_kind": relationship.relationship_kind,
                "source_ref": relationship.source_ref,
                "source_path": relationship.source_path,
                "evidence_hash": relationship.evidence_hash,
                "related_refs": list(relationship.related_refs),
                "deleted": relationship.deleted,
                "blocked": relationship.blocked,
            }
            for relationship in fixture.relationships
        ],
    }


def _local_relationship(
    *,
    source: str,
    target: str,
    relationship_kind: str,
    query_terms: list[str],
    attribution: Mapping[str, object],
) -> dict[str, object]:
    return {
        "source": source,
        "target": target,
        "kind": relationship_kind,
        "query_terms": sorted(set(_local_terms(" ".join(query_terms + [source, target, relationship_kind])))),
        "source_attribution": dict(attribution),
    }


def _safe_tags(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("tags")
    if not isinstance(raw, list):
        return []
    tags = []
    for item in raw:
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", item):
            tags.append(item)
    return tags[:16]


def _explicit_relationships(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("relationships")
    if not isinstance(raw, list):
        return []
    relationships = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        target = item.get("target") or item.get("target_ref")
        kind = item.get("kind") or item.get("relationship_kind")
        if (
            isinstance(target, str)
            and isinstance(kind, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", target)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", kind)
        ):
            relationships.append({"target": target, "kind": kind})
    return relationships[:32]


def _local_terms(value: str) -> list[str]:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH_QUERY", "query must be a bounded string")
    terms = re.findall(r"[a-z0-9]+", value.lower())
    if not terms:
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH_QUERY", "query has no searchable terms")
    return terms


def _validate_local_graph_index(data: object) -> Mapping[str, Any]:
    if not isinstance(data, Mapping) or data.get("schema") != LOCAL_GRAPHIFY_INDEX_SCHEMA:
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH", "local Graphify index schema is invalid")
    relationships = data.get("relationships")
    if not isinstance(relationships, list):
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH", "local Graphify relationships must be an array")
    if int(data.get("relationship_count", -1)) != len(relationships):
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH", "local Graphify relationship count is invalid")
    expected_hash = data.get("index_hash")
    if not isinstance(expected_hash, str) or not _HASH_RE.fullmatch(expected_hash):
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH", "local Graphify index hash is invalid")
    without_hash = dict(data)
    without_hash.pop("index_hash", None)
    actual_hash = bytes_hash(canonical_json(without_hash).encode("utf-8"))
    if actual_hash != expected_hash:
        raise GraphifyAdapterError("INVALID_LOCAL_GRAPH", "local Graphify index hash mismatch")
    return data


def atomic_write_json_private(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
        tmp.chmod(0o600)
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def dumps_public(value: Mapping[str, Any]) -> str:
    """Serialize only after public payload validation for tests and docs tooling."""

    return json.dumps(validate_public_payload(value), allow_nan=False, sort_keys=True)

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, TypeVar, runtime_checkable


SEMANTIC_MEMORY_PROJECTION_EVENT_SCHEMA = "skeleton.semantic_memory_projection_event.v1"
SEMANTIC_MEMORY_RECALL_REQUEST_SCHEMA = "skeleton.semantic_memory_recall_request.v1"
SEMANTIC_MEMORY_RECALL_RESPONSE_SCHEMA = "skeleton.semantic_memory_recall_response.v1"
SEMANTIC_MEMORY_PROJECTION_RECEIPT_SCHEMA = "skeleton.semantic_memory_projection_receipt.v1"

BOUNDED_PROJECTION_TEXT_CHARS = 512
MAX_RECALL_RESULTS = 10

SAFE_REASON_CODES = frozenset(
    {
        "MEMORY_UNAVAILABLE",
        "PROJECT_ID_AMBIGUOUS",
        "PROJECTION_STALE",
        "CROSS_PROJECT_RECALL_FORBIDDEN",
        "COGNEE_DEPENDENCY_UNAVAILABLE",
        "INVALID_PROJECTION_EVENT",
        "INVALID_RECALL_REQUEST",
        "CONTENT_HASH_MISMATCH",
        "DATASET_ID_AMBIGUOUS",
    }
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/#-]{0,191}$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()/#-]{0,511}$")
_SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")

ProjectionEventT = TypeVar("ProjectionEventT", bound="ProjectionEvent")
RecallRequestT = TypeVar("RecallRequestT", bound="RecallRequest")


class SemanticMemoryProjectionError(ValueError):
    """Raised when a semantic projection operation fails closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProjectionBinding:
    project_id: str
    dataset_id: str


@dataclass(frozen=True)
class ProjectionEvent:
    project_id: str
    dataset_id: str
    canonical_revision: int
    canonical_ref: str
    content_hash: str
    bounded_text: str
    provenance: Mapping[str, str]


@dataclass(frozen=True)
class RecallRequest:
    project_id: str
    dataset_id: str
    query: str
    canonical_revision: int
    limit: int = 5


@dataclass(frozen=True)
class RecallResult:
    project_id: str
    dataset_id: str
    canonical_revision: int
    canonical_ref: str
    content_hash: str
    score: float
    bounded_text: str
    provenance: Mapping[str, str]


@dataclass(frozen=True)
class HealthStatus:
    status: str
    project_id: str
    dataset_id: str
    indexed_canonical_revision: int
    current_canonical_revision: int
    stale: bool
    reason_code: str | None = None


@runtime_checkable
class SemanticMemoryProjection(Protocol[ProjectionEventT, RecallRequestT]):
    """Generic contract for derived semantic memory projections.

    Implementations are non-authoritative. They may project bounded synthetic
    source events into disposable local index storage, but exact canonical reads
    and all canonical mutations remain outside this protocol.
    """

    @property
    def binding(self) -> ProjectionBinding:
        raise NotImplementedError

    @property
    def self_improvement(self) -> bool:
        raise NotImplementedError

    def project(self, events: list[ProjectionEventT]) -> dict[str, object]:
        raise NotImplementedError

    def recall(self, request: RecallRequestT) -> dict[str, object]:
        raise NotImplementedError

    def health(self, *, current_canonical_revision: int) -> HealthStatus:
        raise NotImplementedError

    def forget_projection(self, *, canonical_ref: str) -> dict[str, object]:
        raise NotImplementedError


def load_projection_event(data: Mapping[str, Any]) -> ProjectionEvent:
    if not isinstance(data, Mapping):
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", "event must be an object")
    _reject_extra_keys(
        data,
        {
            "schema",
            "project_id",
            "dataset_id",
            "canonical_revision",
            "canonical_ref",
            "content_hash",
            "bounded_text",
            "provenance",
        },
        "event",
    )
    if data.get("schema") != SEMANTIC_MEMORY_PROJECTION_EVENT_SCHEMA:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", "event schema is invalid")

    bounded_text = _safe_text(data.get("bounded_text"), "bounded_text")
    content_hash = _safe_hash(data.get("content_hash"), "content_hash")
    if sha256_text(bounded_text) != content_hash.lower():
        raise SemanticMemoryProjectionError("CONTENT_HASH_MISMATCH", "content_hash must match bounded_text")

    return ProjectionEvent(
        project_id=safe_id(data.get("project_id"), "project_id"),
        dataset_id=safe_id(data.get("dataset_id"), "dataset_id"),
        canonical_revision=positive_int(data.get("canonical_revision"), "canonical_revision"),
        canonical_ref=safe_ref(data.get("canonical_ref"), "canonical_ref"),
        content_hash=content_hash.lower(),
        bounded_text=bounded_text,
        provenance=_safe_string_map(data.get("provenance"), "provenance"),
    )


def load_recall_request(data: Mapping[str, Any]) -> RecallRequest:
    if not isinstance(data, Mapping):
        raise SemanticMemoryProjectionError("INVALID_RECALL_REQUEST", "request must be an object")
    _reject_extra_keys(data, {"schema", "project_id", "dataset_id", "query", "canonical_revision", "limit"}, "request")
    if data.get("schema") != SEMANTIC_MEMORY_RECALL_REQUEST_SCHEMA:
        raise SemanticMemoryProjectionError("INVALID_RECALL_REQUEST", "request schema is invalid")
    query = _safe_text(data.get("query"), "query", max_length=128)
    limit = data.get("limit", 5)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_RECALL_RESULTS:
        raise SemanticMemoryProjectionError("INVALID_RECALL_REQUEST", "limit must be an integer from 1 to 10")
    return RecallRequest(
        project_id=safe_id(data.get("project_id"), "project_id"),
        dataset_id=safe_id(data.get("dataset_id"), "dataset_id"),
        query=query,
        canonical_revision=positive_int(data.get("canonical_revision"), "canonical_revision"),
        limit=limit,
    )


def sanitized_receipt(
    *,
    status: str,
    project_id: str,
    dataset_id: str,
    reason_code: str | None,
    projected_count: int = 0,
    recalled_count: int = 0,
    deleted_count: int = 0,
    canonical_revisions: list[int] | None = None,
    content_hashes: list[str] | None = None,
) -> dict[str, object]:
    if reason_code is not None and reason_code not in SAFE_REASON_CODES:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", "receipt reason code is not public-safe")
    return {
        "schema": SEMANTIC_MEMORY_PROJECTION_RECEIPT_SCHEMA,
        "status": status,
        "project_id": project_id,
        "dataset_id": dataset_id,
        "reason_code": reason_code,
        "counts": {
            "projected": projected_count,
            "recalled": recalled_count,
            "deleted": deleted_count,
        },
        "canonical_revisions": sorted(set(canonical_revisions or [])),
        "content_hashes": sorted(set(content_hashes or [])),
    }


def recall_response(
    *,
    project_id: str,
    dataset_id: str,
    canonical_revision: int,
    stale: bool,
    results: list[RecallResult],
) -> dict[str, object]:
    return {
        "schema": SEMANTIC_MEMORY_RECALL_RESPONSE_SCHEMA,
        "status": "STALE" if stale else "OK",
        "project_id": project_id,
        "dataset_id": dataset_id,
        "canonical_revision": canonical_revision,
        "stale": stale,
        "results": [
            {
                "project_id": result.project_id,
                "dataset_id": result.dataset_id,
                "canonical_revision": result.canonical_revision,
                "canonical_ref": result.canonical_ref,
                "content_hash": result.content_hash,
                "score": result.score,
                "bounded_text": result.bounded_text,
                "provenance": deepcopy(dict(result.provenance)),
            }
            for result in results
        ],
    }


def safe_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value) or value in {"*", "all", "ALL"}:
        reason = "PROJECT_ID_AMBIGUOUS" if name == "project_id" else "DATASET_ID_AMBIGUOUS"
        raise SemanticMemoryProjectionError(reason, f"{name} must be an explicit safe identifier")
    return value


def safe_ref(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_REF_RE.fullmatch(value) or value in {"*", "all", "ALL"}:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{name} must be an explicit safe ref")
    return value


def positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{name} must be a positive integer")
    return value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_payload_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_text(value: object, name: str, *, max_length: int = BOUNDED_PROJECTION_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or not _SAFE_TEXT_RE.fullmatch(value):
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{name} must be bounded public-safe text")
    return value


def _safe_hash(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{name} must be sha256-shaped")
    return value


def _safe_string_map(value: object, name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{name} must be a non-empty object")
    if len(value) > 8:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{name} is capped at 8 entries")
    result = {}
    for key, item in value.items():
        result[safe_id(key, f"{name}_key")] = safe_ref(item, f"{name}_value")
    return result


def _reject_extra_keys(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise SemanticMemoryProjectionError("INVALID_PROJECTION_EVENT", f"{path} contains unsupported field")

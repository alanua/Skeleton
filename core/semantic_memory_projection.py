from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Protocol


SEMANTIC_PROJECTION_EVENT_SCHEMA = "skeleton.semantic_memory.projection_event.v1"
SEMANTIC_RECALL_REQUEST_SCHEMA = "skeleton.semantic_memory.recall_request.v1"
SEMANTIC_RECALL_RESPONSE_SCHEMA = "skeleton.semantic_memory.recall_response.v1"
SEMANTIC_PROJECTION_RECEIPT_SCHEMA = "skeleton.semantic_memory.projection_receipt.v1"
SEMANTIC_HEALTH_SCHEMA = "skeleton.semantic_memory.health.v1"

MEMORY_UNAVAILABLE = "MEMORY_UNAVAILABLE"
PROJECT_ID_AMBIGUOUS = "PROJECT_ID_AMBIGUOUS"
DATASET_ID_AMBIGUOUS = "DATASET_ID_AMBIGUOUS"
PROJECTION_STALE = "PROJECTION_STALE"
CROSS_PROJECT_RECALL_FORBIDDEN = "CROSS_PROJECT_RECALL_FORBIDDEN"
COGNEE_DEPENDENCY_UNAVAILABLE = "COGNEE_DEPENDENCY_UNAVAILABLE"
COGNEE_RUNTIME_NOT_IMPLEMENTED = "COGNEE_RUNTIME_NOT_IMPLEMENTED"

MAX_PROJECTION_TEXT_CHARS = 4096
MAX_QUERY_CHARS = 512
MAX_RESULTS = 8
HASH_HEX_LENGTH = 64

class SemanticProjectionError(ValueError):
    """Raised when a derived semantic projection fails closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SemanticScope:
    project_id: str
    dataset_id: str


@dataclass(frozen=True)
class SemanticProjectionEvent:
    schema: str
    scope: SemanticScope
    canonical_revision: int
    canonical_ref: str
    content_hash: str
    projection_text_hash: str
    bounded_text: str
    provenance: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class SemanticRecallRequest:
    schema: str
    scope: SemanticScope
    query: str
    current_canonical_revision: int
    limit: int = 5


@dataclass(frozen=True)
class SemanticRecallResult:
    canonical_ref: str
    canonical_revision: int
    content_hash: str
    projection_text_hash: str
    score: float
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class SemanticRecallResponse:
    schema: str
    status: str
    scope: SemanticScope
    current_canonical_revision: int
    indexed_canonical_revision: int
    authoritative: bool
    results: tuple[SemanticRecallResult, ...]


@dataclass(frozen=True)
class SemanticProjectionHealth:
    schema: str
    status: str
    scope: SemanticScope
    current_canonical_revision: int
    indexed_canonical_revision: int
    aggregate_counts: Mapping[str, int]
    reason_codes: tuple[str, ...]
    authoritative: bool = False


@dataclass(frozen=True)
class SemanticProjectionReceipt:
    schema: str
    status: str
    aggregate_counts: Mapping[str, int]
    canonical_revisions: Mapping[str, int]
    hashes: Mapping[str, object]
    reason_codes: tuple[str, ...]
    authoritative: bool = False


class SemanticProjectionProtocol(Protocol):
    """Public derived semantic projection contract."""

    def project(self, event: Mapping[str, object]) -> dict[str, object]:
        ...

    def recall(self, request: Mapping[str, object]) -> dict[str, object]:
        ...

    def health(self, *, project_id: object, dataset_id: object, current_canonical_revision: int) -> dict[str, object]:
        ...

    def forget_projection(self, *, project_id: object, dataset_id: object) -> dict[str, object]:
        ...


def canonical_json_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def projection_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize_scope(project_id: object, dataset_id: object) -> SemanticScope:
    return SemanticScope(
        project_id=_strict_scope_id(project_id, "project_id", PROJECT_ID_AMBIGUOUS),
        dataset_id=_strict_scope_id(dataset_id, "dataset_id", DATASET_ID_AMBIGUOUS),
    )


def sanitize_projection_event(event: Mapping[str, object]) -> SemanticProjectionEvent:
    if event.get("schema") != SEMANTIC_PROJECTION_EVENT_SCHEMA:
        raise SemanticProjectionError("INVALID_PROJECTION_EVENT", "projection event schema is invalid")
    scope = sanitize_scope(event.get("project_id"), event.get("dataset_id"))
    canonical_revision = _positive_int(event.get("canonical_revision"), "canonical_revision")
    canonical_ref = _required_bounded_string(event.get("canonical_ref"), "canonical_ref", max_chars=256)
    content_hash = _strict_hash(event.get("content_hash"), "content_hash")
    bounded_text = sanitize_bounded_text(event.get("bounded_text"), max_chars=MAX_PROJECTION_TEXT_CHARS)
    expected_text_hash = projection_text_hash(bounded_text)
    provided_text_hash = _strict_hash(event.get("projection_text_hash"), "projection_text_hash")
    if provided_text_hash != expected_text_hash:
        raise SemanticProjectionError("PROJECTION_TEXT_HASH_MISMATCH", "projection text hash does not match text")
    provenance = _sanitize_provenance(event.get("provenance"))
    return SemanticProjectionEvent(
        schema=SEMANTIC_PROJECTION_EVENT_SCHEMA,
        scope=scope,
        canonical_revision=canonical_revision,
        canonical_ref=canonical_ref,
        content_hash=content_hash,
        projection_text_hash=provided_text_hash,
        bounded_text=bounded_text,
        provenance=provenance,
    )


def sanitize_recall_request(request: Mapping[str, object]) -> SemanticRecallRequest:
    if request.get("schema") != SEMANTIC_RECALL_REQUEST_SCHEMA:
        raise SemanticProjectionError("INVALID_RECALL_REQUEST", "recall request schema is invalid")
    scope = sanitize_scope(request.get("project_id"), request.get("dataset_id"))
    query = sanitize_bounded_text(request.get("query"), max_chars=MAX_QUERY_CHARS)
    current_revision = _non_negative_int(request.get("current_canonical_revision"), "current_canonical_revision")
    limit = _bounded_limit(request.get("limit", 5))
    return SemanticRecallRequest(
        schema=SEMANTIC_RECALL_REQUEST_SCHEMA,
        scope=scope,
        query=query,
        current_canonical_revision=current_revision,
        limit=limit,
    )


def sanitize_bounded_text(value: object, *, max_chars: int) -> str:
    text = _required_bounded_string(value, "text", max_chars=max_chars)
    if any(unicodedata.category(char) == "Cc" for char in text):
        raise SemanticProjectionError("TEXT_CONTROL_CHARACTERS_REJECTED", "text contains control characters")
    return text


def public_receipt(
    *,
    status: str,
    event_count: int = 0,
    result_count: int = 0,
    indexed_canonical_revision: int,
    current_canonical_revision: int,
    content_hashes: tuple[str, ...] = (),
    projection_text_hashes: tuple[str, ...] = (),
    reason_codes: tuple[str, ...] = (),
) -> SemanticProjectionReceipt:
    return SemanticProjectionReceipt(
        schema=SEMANTIC_PROJECTION_RECEIPT_SCHEMA,
        status=status,
        aggregate_counts={
            "event_count": event_count,
            "result_count": result_count,
        },
        canonical_revisions={
            "indexed_canonical_revision": indexed_canonical_revision,
            "current_canonical_revision": current_canonical_revision,
        },
        hashes={
            "content_hashes": tuple(sorted(content_hashes)),
            "projection_text_hashes": tuple(sorted(projection_text_hashes)),
        },
        reason_codes=tuple(reason_codes),
        authoritative=False,
    )


def receipt_to_public_dict(receipt: SemanticProjectionReceipt) -> dict[str, object]:
    return {
        "schema": receipt.schema,
        "status": receipt.status,
        "aggregate_counts": dict(receipt.aggregate_counts),
        "canonical_revisions": dict(receipt.canonical_revisions),
        "hashes": {
            "content_hashes": list(receipt.hashes.get("content_hashes", ())),
            "projection_text_hashes": list(receipt.hashes.get("projection_text_hashes", ())),
        },
        "reason_codes": list(receipt.reason_codes),
        "authoritative": receipt.authoritative,
    }


def health_to_public_dict(health: SemanticProjectionHealth) -> dict[str, object]:
    return {
        "schema": health.schema,
        "status": health.status,
        "aggregate_counts": dict(health.aggregate_counts),
        "canonical_revisions": {
            "indexed_canonical_revision": health.indexed_canonical_revision,
            "current_canonical_revision": health.current_canonical_revision,
        },
        "reason_codes": list(health.reason_codes),
        "authoritative": health.authoritative,
    }


def recall_response_to_private_dict(response: SemanticRecallResponse) -> dict[str, object]:
    return {
        "schema": response.schema,
        "status": response.status,
        "project_id": response.scope.project_id,
        "dataset_id": response.scope.dataset_id,
        "current_canonical_revision": response.current_canonical_revision,
        "indexed_canonical_revision": response.indexed_canonical_revision,
        "authoritative": response.authoritative,
        "results": [
            {
                "canonical_ref": result.canonical_ref,
                "canonical_revision": result.canonical_revision,
                "content_hash": result.content_hash,
                "projection_text_hash": result.projection_text_hash,
                "score": result.score,
                "metadata": dict(result.metadata),
            }
            for result in response.results
        ],
    }


def _strict_scope_id(value: object, field: str, reason_code: str) -> str:
    if not isinstance(value, str) or len(value) > 128:
        raise SemanticProjectionError(reason_code, f"{field} must be exact")
    text = value
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    if not normalized or normalized in {"*", "all", "any"}:
        raise SemanticProjectionError(reason_code, f"{field} must be exact")
    if "/" in text or "\\" in text or any(unicodedata.category(char) == "Cc" for char in text):
        raise SemanticProjectionError(reason_code, f"{field} must be public-safe")
    return text


def _strict_hash(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != HASH_HEX_LENGTH:
        raise SemanticProjectionError("INVALID_HASH", f"{field} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise SemanticProjectionError("INVALID_HASH", f"{field} must be a SHA-256 hex digest") from exc
    return value.lower()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SemanticProjectionError("INVALID_CANONICAL_REVISION", f"{field} must be positive")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SemanticProjectionError("INVALID_CANONICAL_REVISION", f"{field} must be non-negative")
    return value


def _bounded_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SemanticProjectionError("INVALID_LIMIT", "limit must be an integer")
    return max(1, min(value, MAX_RESULTS))


def _required_bounded_string(value: object, field: str, *, max_chars: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise SemanticProjectionError("INVALID_TEXT", f"{field} must be a non-empty bounded string")
    return value


def _sanitize_provenance(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not value or len(value) > 8:
        raise SemanticProjectionError("INVALID_PROVENANCE", "provenance must be a bounded non-empty list")
    sanitized: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise SemanticProjectionError("INVALID_PROVENANCE", "provenance entries must be objects")
        encoded = json.dumps(item, sort_keys=True, ensure_ascii=True)
        if len(encoded) > 1024:
            raise SemanticProjectionError("INVALID_PROVENANCE", "provenance entries must be bounded")
        if any(unicodedata.category(char) == "Cc" for char in encoded):
            raise SemanticProjectionError("INVALID_PROVENANCE", "provenance contains control characters")
        sanitized.append(dict(item))
    return tuple(sanitized)

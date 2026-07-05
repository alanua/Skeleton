from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


MEMPALACE_PROJECTION_SCHEMA = "skeleton.mempalace_projection.v1"
MEMPALACE_SYNTHETIC_NAMESPACE = "skeleton"
MEMPALACE_SYNTHETIC_PROJECT_ID = "mempalace_synthetic"
MEMPALACE_INDEX_SCHEMA = "skeleton.mempalace_index_manifest.v1"
BOUNDED_EXCERPT_CHARS = 180

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()#-]{0,511}$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DOCUMENT_KEYS = frozenset(
    {
        "item_id",
        "canonical_ref",
        "canonical_revision",
        "title",
        "bounded_text",
        "tags",
        "source_attribution",
        "deleted",
    }
)
_ATTRIBUTION_KEYS = frozenset({"source_ref", "kind", "evidence_hash"})


class MemPalaceProjectionError(ValueError):
    """Raised when synthetic projection input is not bounded or public-safe."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MemPalaceDocument:
    item_id: str
    canonical_ref: str
    canonical_revision: int
    title: str
    bounded_text: str
    tags: tuple[str, ...]
    source_attribution: tuple[dict[str, str], ...]
    deleted: bool


@dataclass(frozen=True)
class MemPalaceProjection:
    namespace: str
    project_id: str
    source_snapshot_id: str
    current_canonical_revision: int
    indexed_at: str
    documents: tuple[MemPalaceDocument, ...]


def load_projection(data: Mapping[str, Any]) -> MemPalaceProjection:
    """Validate bounded synthetic projection data and return a typed projection."""

    if not isinstance(data, Mapping):
        raise MemPalaceProjectionError("INVALID_PROJECTION", "projection must be an object")
    allowed_top_level = {
        "schema",
        "namespace",
        "project_id",
        "source_snapshot_id",
        "current_canonical_revision",
        "indexed_at",
        "documents",
    }
    _reject_extra_keys(data, allowed_top_level, "projection")
    if data.get("schema") != MEMPALACE_PROJECTION_SCHEMA:
        raise MemPalaceProjectionError("INVALID_PROJECTION_SCHEMA", "projection schema is invalid")
    namespace = _safe_token(data.get("namespace"), "namespace")
    project_id = _safe_token(data.get("project_id"), "project_id")
    if namespace != MEMPALACE_SYNTHETIC_NAMESPACE or project_id != MEMPALACE_SYNTHETIC_PROJECT_ID:
        raise MemPalaceProjectionError("PROJECTION_SCOPE_NOT_SYNTHETIC", "projection scope is not synthetic")
    source_snapshot_id = _safe_token(data.get("source_snapshot_id"), "source_snapshot_id")
    indexed_at = _safe_timestamp(data.get("indexed_at"))
    current_revision = _positive_int(data.get("current_canonical_revision"), "current_canonical_revision")
    raw_documents = data.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise MemPalaceProjectionError("DOCUMENTS_REQUIRED", "documents must be a non-empty array")
    if len(raw_documents) > 32:
        raise MemPalaceProjectionError("DOCUMENT_LIMIT_EXCEEDED", "synthetic projection is capped at 32 documents")

    seen: set[str] = set()
    documents = []
    for raw_document in raw_documents:
        if not isinstance(raw_document, Mapping):
            raise MemPalaceProjectionError("INVALID_DOCUMENT", "document must be an object")
        document = _load_document(raw_document)
        if document.item_id in seen:
            raise MemPalaceProjectionError("DUPLICATE_ITEM_ID", "document item_id is duplicated")
        seen.add(document.item_id)
        documents.append(document)

    return MemPalaceProjection(
        namespace=namespace,
        project_id=project_id,
        source_snapshot_id=source_snapshot_id,
        current_canonical_revision=current_revision,
        indexed_at=indexed_at,
        documents=tuple(documents),
    )


def build_index_manifest(projection: MemPalaceProjection) -> dict[str, object]:
    """Build a deterministic, rebuildable lexical index manifest from projection source."""

    items = []
    for document in projection.documents:
        if document.deleted:
            continue
        tokens = sorted(set(_tokens(document.title) + _tokens(document.bounded_text) + list(document.tags)))
        items.append(
            {
                "item_id": document.item_id,
                "canonical_ref": document.canonical_ref,
                "indexed_canonical_revision": document.canonical_revision,
                "text_hash": _sha256(document.bounded_text),
                "token_set": tokens,
                "source_attribution": deepcopy(list(document.source_attribution)),
            }
        )
    items.sort(key=lambda item: str(item["item_id"]))
    manifest = {
        "schema": MEMPALACE_INDEX_SCHEMA,
        "namespace": projection.namespace,
        "project_id": projection.project_id,
        "source_snapshot_id": projection.source_snapshot_id,
        "indexed_at": projection.indexed_at,
        "item_count": len(items),
        "items": items,
    }
    manifest["manifest_hash"] = _sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return manifest


def query_terms(query: str) -> tuple[str, ...]:
    if not isinstance(query, str) or not query.strip() or len(query) > 128:
        raise MemPalaceProjectionError("INVALID_QUERY", "query must be a bounded string")
    terms = tuple(sorted(set(_tokens(query))))
    if not terms:
        raise MemPalaceProjectionError("INVALID_QUERY", "query has no searchable terms")
    return terms


def projection_terms(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise MemPalaceProjectionError("INVALID_TEXT", "projection text must be a string")
    return tuple(sorted(set(_tokens(value))))


def bounded_excerpt(value: str) -> str:
    return value if len(value) <= BOUNDED_EXCERPT_CHARS else value[: BOUNDED_EXCERPT_CHARS - 3] + "..."


def _load_document(data: Mapping[str, Any]) -> MemPalaceDocument:
    _reject_extra_keys(data, _DOCUMENT_KEYS, "document")
    item_id = _safe_token(data.get("item_id"), "item_id")
    canonical_ref = _safe_token(data.get("canonical_ref"), "canonical_ref")
    canonical_revision = _positive_int(data.get("canonical_revision"), "canonical_revision")
    title = _safe_text(data.get("title"), "title", max_length=96)
    bounded_text = _safe_text(data.get("bounded_text"), "bounded_text", max_length=BOUNDED_EXCERPT_CHARS)
    raw_tags = data.get("tags")
    if not isinstance(raw_tags, list) or len(raw_tags) > 12:
        raise MemPalaceProjectionError("INVALID_TAGS", "tags must be a bounded array")
    tags = tuple(_safe_token(tag, "tag") for tag in raw_tags)
    raw_attribution = data.get("source_attribution")
    if not isinstance(raw_attribution, list) or not raw_attribution:
        raise MemPalaceProjectionError("SOURCE_ATTRIBUTION_REQUIRED", "source attribution is mandatory")
    if len(raw_attribution) > 4:
        raise MemPalaceProjectionError("SOURCE_ATTRIBUTION_LIMIT_EXCEEDED", "source attribution is capped")
    source_attribution = tuple(_load_attribution(item) for item in raw_attribution)
    deleted = data.get("deleted")
    if not isinstance(deleted, bool):
        raise MemPalaceProjectionError("INVALID_DELETED_FLAG", "deleted must be boolean")
    return MemPalaceDocument(
        item_id=item_id,
        canonical_ref=canonical_ref,
        canonical_revision=canonical_revision,
        title=title,
        bounded_text=bounded_text,
        tags=tags,
        source_attribution=source_attribution,
        deleted=deleted,
    )


def _load_attribution(data: object) -> dict[str, str]:
    if not isinstance(data, Mapping):
        raise MemPalaceProjectionError("INVALID_SOURCE_ATTRIBUTION", "source attribution must be an object")
    _reject_extra_keys(data, _ATTRIBUTION_KEYS, "source_attribution")
    evidence_hash = _safe_token(data.get("evidence_hash"), "evidence_hash")
    if len(evidence_hash) != 64:
        raise MemPalaceProjectionError("INVALID_EVIDENCE_HASH", "evidence hash must be sha256-shaped")
    return {
        "source_ref": _safe_token(data.get("source_ref"), "source_ref"),
        "kind": _safe_token(data.get("kind"), "kind"),
        "evidence_hash": evidence_hash,
    }


def _reject_extra_keys(data: Mapping[str, Any], allowed: frozenset[str] | set[str], path: str) -> None:
    extra = sorted(set(data) - set(allowed))
    if extra:
        raise MemPalaceProjectionError("UNSUPPORTED_PROJECTION_FIELD", f"{path} contains unsupported field")


def _safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise MemPalaceProjectionError("INVALID_TOKEN", f"{name} must be a safe token")
    return value


def _safe_text(value: object, name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or not _SAFE_TEXT_RE.fullmatch(value):
        raise MemPalaceProjectionError("INVALID_TEXT", f"{name} must be bounded public-safe text")
    return value


def _safe_timestamp(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise MemPalaceProjectionError("INVALID_TIMESTAMP", "indexed_at must be a UTC timestamp")
    return value


def _positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MemPalaceProjectionError("INVALID_INTEGER", f"{name} must be a positive integer")
    return value


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.lower())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

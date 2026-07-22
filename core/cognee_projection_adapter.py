from __future__ import annotations

import asyncio
import importlib
import re
from copy import deepcopy
from typing import Any, Mapping, Protocol

from core.semantic_memory_projection import (
    ProjectionBinding,
    ProjectionEvent,
    RecallRequest,
    RecallResult,
    SemanticMemoryProjectionError,
    HealthStatus,
    load_projection_event,
    load_recall_request,
    recall_response,
    safe_id,
    safe_ref,
    sanitized_receipt,
    stable_payload_hash,
)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class CogneeProjectionBackend(Protocol):
    """Minimal backend boundary so policy tests do not call networks or models."""

    def add(self, *, project_id: str, dataset_id: str, document_id: str, text: str, metadata: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def search(self, *, project_id: str, dataset_id: str, query: str, limit: int) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def delete(self, *, project_id: str, dataset_id: str, canonical_ref: str) -> int:
        raise NotImplementedError

    def health(self, *, project_id: str, dataset_id: str) -> Mapping[str, Any]:
        raise NotImplementedError


class CogneePackageBackend:
    """Thin adapter around the optional `cognee` package.

    This class only proves the package boundary is importable in this slice.
    Real model-backed Cognee indexing is intentionally not activated here.
    Tests and smoke use an injected disposable backend.
    """

    def __init__(self) -> None:
        try:
            self._cognee = importlib.import_module("cognee")
        except Exception as exc:
            raise SemanticMemoryProjectionError(
                "COGNEE_DEPENDENCY_UNAVAILABLE",
                "install the optional cognee dependency group to use CogneeProjectionAdapter",
            ) from exc

    @property
    def version(self) -> str:
        return str(getattr(self._cognee, "__version__", "import-ok"))

    def add(self, *, project_id: str, dataset_id: str, document_id: str, text: str, metadata: Mapping[str, Any]) -> None:
        raise SemanticMemoryProjectionError(
            "MEMORY_UNAVAILABLE",
            "runtime Cognee projection is behind the next activation gate; inject a disposable backend for smoke",
        )

    def search(self, *, project_id: str, dataset_id: str, query: str, limit: int) -> list[Mapping[str, Any]]:
        raise SemanticMemoryProjectionError(
            "MEMORY_UNAVAILABLE",
            "runtime Cognee recall is behind the next activation gate; inject a disposable backend for smoke",
        )

    def delete(self, *, project_id: str, dataset_id: str, canonical_ref: str) -> int:
        raise SemanticMemoryProjectionError(
            "MEMORY_UNAVAILABLE",
            "runtime Cognee projection deletion is behind the next activation gate",
        )

    def health(self, *, project_id: str, dataset_id: str) -> Mapping[str, Any]:
        return {"available": True, "backend": "cognee", "version": self.version}


class InMemoryCogneeProjectionBackend:
    """Deterministic disposable backend for synthetic smoke tests."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(self, *, project_id: str, dataset_id: str, document_id: str, text: str, metadata: Mapping[str, Any]) -> None:
        self._documents[(project_id, dataset_id, document_id)] = {
            "text": text,
            "metadata": deepcopy(dict(metadata)),
            "deleted": False,
        }

    def search(self, *, project_id: str, dataset_id: str, query: str, limit: int) -> list[Mapping[str, Any]]:
        query_terms = set(_tokens(query))
        scored = []
        for (doc_project_id, doc_dataset_id, document_id), document in self._documents.items():
            if doc_project_id != project_id or doc_dataset_id != dataset_id or document.get("deleted"):
                continue
            text = str(document["text"])
            text_terms = set(_tokens(text))
            matches = query_terms & text_terms
            if not matches:
                continue
            score = round(len(matches) / max(len(query_terms), 1), 3)
            scored.append((-score, document_id, {"text": text, "score": score, "metadata": deepcopy(document["metadata"])}))
        return [item for _, _, item in sorted(scored)[:limit]]

    def delete(self, *, project_id: str, dataset_id: str, canonical_ref: str) -> int:
        deleted = 0
        for (doc_project_id, doc_dataset_id, _), document in self._documents.items():
            metadata = document.get("metadata", {})
            if (
                doc_project_id == project_id
                and doc_dataset_id == dataset_id
                and metadata.get("canonical_ref") == canonical_ref
                and not document.get("deleted")
            ):
                document["deleted"] = True
                deleted += 1
        return deleted

    def health(self, *, project_id: str, dataset_id: str) -> Mapping[str, Any]:
        count = sum(
            1
            for doc_project_id, doc_dataset_id, _ in self._documents
            if doc_project_id == project_id and doc_dataset_id == dataset_id
        )
        return {"available": True, "backend": "in_memory", "document_count": count}


class CogneeProjectionAdapter:
    """Read-only derived semantic projection adapter for explicit project/dataset scopes."""

    self_improvement = False

    def __init__(self, *, project_id: str, dataset_id: str, backend: CogneeProjectionBackend | None = None) -> None:
        self._binding = ProjectionBinding(
            project_id=safe_id(project_id, "project_id"),
            dataset_id=safe_id(dataset_id, "dataset_id"),
        )
        self._backend = backend if backend is not None else CogneePackageBackend()
        self._indexed_revisions: dict[str, int] = {}
        self._content_hashes: dict[str, str] = {}
        self._last_indexed_revision = 0

    @property
    def binding(self) -> ProjectionBinding:
        return self._binding

    def project(self, events: list[ProjectionEvent | Mapping[str, Any]]) -> dict[str, object]:
        loaded = [load_projection_event(event) if isinstance(event, Mapping) else event for event in events]
        for event in loaded:
            self._authorize_event(event)
        for event in loaded:
            metadata = {
                "project_id": event.project_id,
                "dataset_id": event.dataset_id,
                "canonical_revision": event.canonical_revision,
                "canonical_ref": event.canonical_ref,
                "content_hash": event.content_hash,
                "provenance": deepcopy(dict(event.provenance)),
                "projection_hash": stable_payload_hash(
                    {
                        "project_id": event.project_id,
                        "dataset_id": event.dataset_id,
                        "canonical_revision": event.canonical_revision,
                        "canonical_ref": event.canonical_ref,
                        "content_hash": event.content_hash,
                    }
                ),
            }
            self._backend.add(
                project_id=event.project_id,
                dataset_id=event.dataset_id,
                document_id=event.canonical_ref,
                text=event.bounded_text,
                metadata=metadata,
            )
            self._indexed_revisions[event.canonical_ref] = event.canonical_revision
            self._content_hashes[event.canonical_ref] = event.content_hash
            self._last_indexed_revision = max(self._last_indexed_revision, event.canonical_revision)
        return sanitized_receipt(
            status="OK",
            project_id=self._binding.project_id,
            dataset_id=self._binding.dataset_id,
            reason_code=None,
            projected_count=len(loaded),
            canonical_revisions=[event.canonical_revision for event in loaded],
            content_hashes=[event.content_hash for event in loaded],
        )

    def recall(self, request: RecallRequest | Mapping[str, Any]) -> dict[str, object]:
        loaded = load_recall_request(request) if isinstance(request, Mapping) else request
        self._authorize_request(loaded)
        indexed_revision = self._indexed_revision()
        stale = indexed_revision != loaded.canonical_revision
        if stale:
            raise SemanticMemoryProjectionError("PROJECTION_STALE", "projection must be rebuilt before recall")
        raw_results = self._backend.search(
            project_id=loaded.project_id,
            dataset_id=loaded.dataset_id,
            query=loaded.query,
            limit=loaded.limit,
        )
        results = [self._load_result(item, loaded) for item in raw_results]
        response = recall_response(
            project_id=loaded.project_id,
            dataset_id=loaded.dataset_id,
            canonical_revision=loaded.canonical_revision,
            stale=False,
            results=results,
        )
        response["receipt"] = sanitized_receipt(
            status="OK",
            project_id=loaded.project_id,
            dataset_id=loaded.dataset_id,
            reason_code=None,
            recalled_count=len(results),
            canonical_revisions=[result.canonical_revision for result in results],
            content_hashes=[result.content_hash for result in results],
        )
        return response

    def health(self, *, current_canonical_revision: int) -> HealthStatus:
        indexed_revision = self._indexed_revision()
        stale = indexed_revision != current_canonical_revision
        try:
            state = self._backend.health(project_id=self._binding.project_id, dataset_id=self._binding.dataset_id)
        except SemanticMemoryProjectionError as exc:
            return HealthStatus(
                status="UNAVAILABLE",
                project_id=self._binding.project_id,
                dataset_id=self._binding.dataset_id,
                indexed_canonical_revision=indexed_revision,
                current_canonical_revision=current_canonical_revision,
                stale=True,
                reason_code=exc.reason_code,
            )
        status = "STALE" if stale else "READY"
        if state.get("available") is False:
            status = "UNAVAILABLE"
        return HealthStatus(
            status=status,
            project_id=self._binding.project_id,
            dataset_id=self._binding.dataset_id,
            indexed_canonical_revision=indexed_revision,
            current_canonical_revision=current_canonical_revision,
            stale=stale,
            reason_code="PROJECTION_STALE" if stale else None,
        )

    def forget_projection(self, *, canonical_ref: str) -> dict[str, object]:
        ref = safe_ref(canonical_ref, "canonical_ref")
        deleted = self._backend.delete(project_id=self._binding.project_id, dataset_id=self._binding.dataset_id, canonical_ref=ref)
        revision = self._indexed_revisions.pop(ref, None)
        content_hash = self._content_hashes.pop(ref, None)
        return sanitized_receipt(
            status="OK",
            project_id=self._binding.project_id,
            dataset_id=self._binding.dataset_id,
            reason_code=None,
            deleted_count=deleted,
            canonical_revisions=[revision] if revision is not None else [],
            content_hashes=[content_hash] if content_hash is not None else [],
        )

    def _authorize_event(self, event: ProjectionEvent) -> None:
        if event.project_id != self._binding.project_id:
            raise SemanticMemoryProjectionError("CROSS_PROJECT_RECALL_FORBIDDEN", "event project_id does not match adapter")
        if event.dataset_id != self._binding.dataset_id:
            raise SemanticMemoryProjectionError("CROSS_PROJECT_RECALL_FORBIDDEN", "event dataset_id does not match adapter")

    def _authorize_request(self, request: RecallRequest) -> None:
        if request.project_id != self._binding.project_id:
            raise SemanticMemoryProjectionError("CROSS_PROJECT_RECALL_FORBIDDEN", "request project_id does not match adapter")
        if request.dataset_id != self._binding.dataset_id:
            raise SemanticMemoryProjectionError("CROSS_PROJECT_RECALL_FORBIDDEN", "request dataset_id does not match adapter")

    def _load_result(self, item: Mapping[str, Any], request: RecallRequest) -> RecallResult:
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            raise SemanticMemoryProjectionError("MEMORY_UNAVAILABLE", "backend result metadata is missing")
        result_project_id = safe_id(metadata.get("project_id"), "project_id")
        result_dataset_id = safe_id(metadata.get("dataset_id"), "dataset_id")
        if result_project_id != request.project_id or result_dataset_id != request.dataset_id:
            raise SemanticMemoryProjectionError("CROSS_PROJECT_RECALL_FORBIDDEN", "backend returned a foreign result")
        canonical_revision = int(metadata.get("canonical_revision", 0))
        canonical_ref = safe_ref(metadata.get("canonical_ref"), "canonical_ref")
        content_hash = str(metadata.get("content_hash", ""))
        provenance = metadata.get("provenance")
        if not isinstance(provenance, Mapping):
            raise SemanticMemoryProjectionError("MEMORY_UNAVAILABLE", "backend result provenance is missing")
        return RecallResult(
            project_id=result_project_id,
            dataset_id=result_dataset_id,
            canonical_revision=canonical_revision,
            canonical_ref=canonical_ref,
            content_hash=content_hash,
            score=float(item.get("score", 0.0)),
            bounded_text=str(item.get("text", ""))[:512],
            provenance=deepcopy(dict(provenance)),
        )

    def _indexed_revision(self) -> int:
        if not self._indexed_revisions:
            return self._last_indexed_revision
        return max(self._indexed_revisions.values())


async def cognee_dependency_probe() -> str:
    backend = CogneePackageBackend()
    await asyncio.sleep(0)
    return backend.version


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.lower())

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.cognee_projection_adapter import CogneeProjectionAdapter, InMemoryCogneeProjectionBackend
from core.semantic_memory_projection import (
    SEMANTIC_MEMORY_PROJECTION_EVENT_SCHEMA,
    SEMANTIC_MEMORY_RECALL_REQUEST_SCHEMA,
    SemanticMemoryProjectionError,
    sha256_text,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "synthetic_project_alpha"
DATASET_ID = "synthetic_dataset_blue"


def event(text: str, *, project_id: str = PROJECT_ID, dataset_id: str = DATASET_ID, revision: int = 7) -> dict[str, object]:
    return {
        "schema": SEMANTIC_MEMORY_PROJECTION_EVENT_SCHEMA,
        "project_id": project_id,
        "dataset_id": dataset_id,
        "canonical_revision": revision,
        "canonical_ref": f"synthetic/{project_id}/{dataset_id}/event-001",
        "content_hash": sha256_text(text),
        "bounded_text": text,
        "provenance": {
            "source_kind": "synthetic_fixture",
            "source_ref": "tests/cognee_projection/synthetic_event_001",
        },
    }


def request(query: str, *, project_id: str = PROJECT_ID, dataset_id: str = DATASET_ID, revision: int = 7) -> dict[str, object]:
    return {
        "schema": SEMANTIC_MEMORY_RECALL_REQUEST_SCHEMA,
        "project_id": project_id,
        "dataset_id": dataset_id,
        "query": query,
        "canonical_revision": revision,
        "limit": 5,
    }


def test_cognee_optional_package_imports_when_dependency_group_is_installed() -> None:
    cognee = pytest.importorskip("cognee")
    assert getattr(cognee, "__version__", "import-ok")


def test_pyproject_declares_cognee_as_optional_dependency_only() -> None:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cognee==1.4.0" in data["project"]["optional-dependencies"]["cognee"]
    assert all("cognee" not in dependency.lower() for dependency in data["project"]["dependencies"])


def test_project_and_recall_with_disposable_backend_preserves_bindings_and_public_receipt() -> None:
    backend = InMemoryCogneeProjectionBackend()
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=backend)
    synthetic = event("Alpha gasket torque note for synthetic recall only")

    receipt = adapter.project([synthetic])
    assert receipt == {
        "schema": "skeleton.semantic_memory_projection_receipt.v1",
        "status": "OK",
        "project_id": PROJECT_ID,
        "dataset_id": DATASET_ID,
        "reason_code": None,
        "counts": {"projected": 1, "recalled": 0, "deleted": 0},
        "canonical_revisions": [7],
        "content_hashes": [synthetic["content_hash"]],
    }

    response = adapter.recall(request("gasket torque"))
    assert response["status"] == "OK"
    assert response["project_id"] == PROJECT_ID
    assert response["dataset_id"] == DATASET_ID
    assert response["receipt"]["counts"] == {"projected": 0, "recalled": 1, "deleted": 0}
    [result] = response["results"]
    assert result["project_id"] == PROJECT_ID
    assert result["dataset_id"] == DATASET_ID
    assert result["canonical_revision"] == 7
    assert result["canonical_ref"] == synthetic["canonical_ref"]
    assert result["content_hash"] == synthetic["content_hash"]
    assert result["provenance"] == synthetic["provenance"]


def test_dataset_isolation_smoke_blocks_cross_dataset_recall() -> None:
    backend = InMemoryCogneeProjectionBackend()
    adapter_a = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=backend)
    adapter_b = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id="synthetic_dataset_red", backend=backend)
    adapter_a.project([event("Blue dataset spindle setting synthetic")])
    adapter_b.project([event("Red dataset spindle setting synthetic", dataset_id="synthetic_dataset_red")])

    blue = adapter_a.recall(request("spindle", dataset_id=DATASET_ID))
    red = adapter_b.recall(request("spindle", dataset_id="synthetic_dataset_red"))

    assert len(blue["results"]) == 1
    assert blue["results"][0]["dataset_id"] == DATASET_ID
    assert len(red["results"]) == 1
    assert red["results"][0]["dataset_id"] == "synthetic_dataset_red"


@pytest.mark.parametrize(
    ("project_id", "dataset_id", "reason_code"),
    [
        ("", DATASET_ID, "PROJECT_ID_AMBIGUOUS"),
        ("*", DATASET_ID, "PROJECT_ID_AMBIGUOUS"),
        ("all", DATASET_ID, "PROJECT_ID_AMBIGUOUS"),
        (PROJECT_ID, "", "DATASET_ID_AMBIGUOUS"),
        (PROJECT_ID, "*", "DATASET_ID_AMBIGUOUS"),
    ],
)
def test_adapter_requires_explicit_project_and_dataset_scope(project_id: str, dataset_id: str, reason_code: str) -> None:
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        CogneeProjectionAdapter(project_id=project_id, dataset_id=dataset_id, backend=InMemoryCogneeProjectionBackend())
    assert excinfo.value.reason_code == reason_code


def test_project_rejects_mismatched_scope() -> None:
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=InMemoryCogneeProjectionBackend())
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        adapter.project([event("Foreign project synthetic", project_id="synthetic_project_beta")])
    assert excinfo.value.reason_code == "CROSS_PROJECT_RECALL_FORBIDDEN"


def test_recall_rejects_cross_project_scope() -> None:
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=InMemoryCogneeProjectionBackend())
    adapter.project([event("Local only synthetic token")])
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        adapter.recall(request("token", project_id="synthetic_project_beta"))
    assert excinfo.value.reason_code == "CROSS_PROJECT_RECALL_FORBIDDEN"


def test_stale_projection_fails_visible_before_backend_search() -> None:
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=InMemoryCogneeProjectionBackend())
    adapter.project([event("Synthetic stale check")])
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        adapter.recall(request("stale", revision=8))
    assert excinfo.value.reason_code == "PROJECTION_STALE"
    assert adapter.health(current_canonical_revision=8).reason_code == "PROJECTION_STALE"


def test_projection_event_requires_revision_ref_hash_and_bounded_text() -> None:
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=InMemoryCogneeProjectionBackend())
    broken = event("Synthetic hash mismatch")
    broken["content_hash"] = "0" * 64
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        adapter.project([broken])
    assert excinfo.value.reason_code == "CONTENT_HASH_MISMATCH"

    missing_ref = event("Synthetic missing ref")
    del missing_ref["canonical_ref"]
    with pytest.raises(SemanticMemoryProjectionError) as missing:
        adapter.project([missing_ref])
    assert missing.value.reason_code == "INVALID_PROJECTION_EVENT"


def test_forget_is_adapter_local_and_never_uses_canonical_delete_surface() -> None:
    class DeleteTrackingBackend(InMemoryCogneeProjectionBackend):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[tuple[str, str, str]] = []

        def delete(self, *, project_id: str, dataset_id: str, canonical_ref: str) -> int:
            self.deleted.append((project_id, dataset_id, canonical_ref))
            return super().delete(project_id=project_id, dataset_id=dataset_id, canonical_ref=canonical_ref)

    backend = DeleteTrackingBackend()
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=backend)
    synthetic = event("Synthetic forget marker")
    adapter.project([synthetic])
    receipt = adapter.forget_projection(canonical_ref=str(synthetic["canonical_ref"]))

    assert backend.deleted == [(PROJECT_ID, DATASET_ID, synthetic["canonical_ref"])]
    assert receipt["counts"]["deleted"] == 1
    assert adapter.recall(request("forget"))["results"] == []


def test_backend_foreign_result_is_blocked() -> None:
    class ForeignResultBackend(InMemoryCogneeProjectionBackend):
        def search(self, *, project_id: str, dataset_id: str, query: str, limit: int) -> list[Mapping[str, Any]]:
            results = super().search(project_id=project_id, dataset_id=dataset_id, query=query, limit=limit)
            altered: list[dict[str, Any]] = []
            for result in results:
                clone = dict(result)
                metadata = dict(clone["metadata"])
                metadata["project_id"] = "synthetic_project_beta"
                clone["metadata"] = metadata
                altered.append(clone)
            return altered

    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=ForeignResultBackend())
    adapter.project([event("Synthetic foreign result marker")])
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        adapter.recall(request("marker"))
    assert excinfo.value.reason_code == "CROSS_PROJECT_RECALL_FORBIDDEN"


def test_improve_is_not_exposed() -> None:
    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=InMemoryCogneeProjectionBackend())
    assert adapter.self_improvement is False
    assert not hasattr(adapter, "improve")


def test_schemas_validate_synthetic_event_response_and_receipt() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    event_schema = json.loads((ROOT / "schemas" / "semantic_memory_projection_event.schema.json").read_text(encoding="utf-8"))
    request_schema = json.loads((ROOT / "schemas" / "semantic_memory_recall_request.schema.json").read_text(encoding="utf-8"))
    response_schema = json.loads((ROOT / "schemas" / "semantic_memory_recall_response.schema.json").read_text(encoding="utf-8"))
    receipt_schema = json.loads((ROOT / "schemas" / "semantic_memory_projection_receipt.schema.json").read_text(encoding="utf-8"))
    synthetic = event("Synthetic schema marker")
    jsonschema.Draft202012Validator(event_schema).validate(synthetic)
    jsonschema.Draft202012Validator(request_schema).validate(request("marker"))

    adapter = CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID, backend=InMemoryCogneeProjectionBackend())
    receipt = adapter.project([synthetic])
    response = adapter.recall(request("marker"))
    jsonschema.Draft202012Validator(receipt_schema).validate(receipt)
    jsonschema.Draft202012Validator(response_schema).validate(response)


def test_dependency_failure_uses_visible_reason_code(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> object:
        if name == "cognee":
            raise ModuleNotFoundError("synthetic missing cognee")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(SemanticMemoryProjectionError) as excinfo:
        CogneeProjectionAdapter(project_id=PROJECT_ID, dataset_id=DATASET_ID)
    assert excinfo.value.reason_code == "COGNEE_DEPENDENCY_UNAVAILABLE"

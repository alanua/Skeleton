from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.cognee_local_runtime import (
    COGNEE_PROJECTION_DOCUMENT_SCHEMA,
    CogneeLocalRuntimeError,
    CogneeWorkerClient,
    opaque_dataset_name,
    opaque_scope_hash,
    projection_document,
)
from core.cognee_projection_adapter import (
    CogneePackageBackend,
    CogneeProjectionAdapter,
    DisposableInMemoryCogneeBackend,
)
from core.semantic_memory_projection import (
    SEMANTIC_PROJECTION_EVENT_SCHEMA,
    SEMANTIC_RECALL_REQUEST_SCHEMA,
    canonical_json_hash,
    projection_text_hash,
)

PROJECT = "synthetic_project"
DATASET = "dataset_phase_0"


def provider_env() -> dict[str, str]:
    return {
        "SKELETON_COGNEE_LLM_PROVIDER": "ollama",
        "SKELETON_COGNEE_LLM_MODEL": "qwen2.5:3b",
        "SKELETON_COGNEE_LLM_ENDPOINT": "http://127.0.0.1:11434",
        "SKELETON_COGNEE_EMBEDDING_PROVIDER": "ollama",
        "SKELETON_COGNEE_EMBEDDING_MODEL": "nomic-embed-text",
        "SKELETON_COGNEE_EMBEDDING_DIMENSIONS": "768",
        "SKELETON_COGNEE_EMBEDDING_ENDPOINT": "http://127.0.0.1:11434",
        "SKELETON_COGNEE_HUGGINGFACE_TOKENIZER": "sentence-transformers/all-MiniLM-L6-v2",
    }


def event(text: str = "semantic memory fact") -> dict[str, object]:
    value_hash = canonical_json_hash({"value": "fact"})
    return {
        "schema": SEMANTIC_PROJECTION_EVENT_SCHEMA,
        "project_id": PROJECT,
        "dataset_id": DATASET,
        "canonical_revision": 1,
        "canonical_ref": "skeleton:fact.001",
        "content_hash": value_hash,
        "projection_text_hash": projection_text_hash(text),
        "bounded_text": text,
        "provenance": [
            {
                "kind": "exact_source",
                "ref": "skeleton:fact.001",
                "evidence_hash": value_hash,
            }
        ],
    }


def request() -> dict[str, object]:
    return {
        "schema": SEMANTIC_RECALL_REQUEST_SCHEMA,
        "project_id": PROJECT,
        "dataset_id": DATASET,
        "query": "semantic memory",
        "current_canonical_revision": 1,
        "limit": 5,
    }


def test_in_memory_backend_emits_exact_top_level_provenance() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    adapter.project(event())
    response = adapter.recall(request())
    result = response["results"][0]
    assert result["provenance"] == [
        {
            "canonical_ref": "skeleton:fact.001",
            "canonical_revision": 1,
            "value_hash": event()["content_hash"],
            "content_hash": event()["content_hash"],
            "source_kind": "canonical_sqlite",
        }
    ]


def test_projection_document_has_exact_binding() -> None:
    payload = event()
    document = projection_document(
        project_id=PROJECT,
        dataset_id=DATASET,
        canonical_ref=str(payload["canonical_ref"]),
        canonical_revision=1,
        content_hash=str(payload["content_hash"]),
        projection_text_hash=str(payload["projection_text_hash"]),
        bounded_text=str(payload["bounded_text"]),
    )
    assert document["schema"] == COGNEE_PROJECTION_DOCUMENT_SCHEMA
    assert document["opaque_scope_hash"] == opaque_scope_hash(PROJECT, DATASET)
    assert document["provenance"][0]["source_kind"] == "canonical_sqlite"


def test_worker_client_consumes_only_worker_candidates(tmp_path: Path) -> None:
    dataset = opaque_dataset_name(PROJECT, DATASET)
    payload = event()
    document = projection_document(
        project_id=PROJECT,
        dataset_id=DATASET,
        canonical_ref=str(payload["canonical_ref"]),
        canonical_revision=1,
        content_hash=str(payload["content_hash"]),
        projection_text_hash=str(payload["projection_text_hash"]),
        bounded_text=str(payload["bounded_text"]),
    )
    calls: list[str] = []

    def runner(command: list[str], request_json: str, env: dict[str, str]):
        del command, env
        worker_request = json.loads(request_json)
        calls.append(worker_request["operation"])
        if worker_request["operation"] == "project":
            result = {"projected": True}
        elif worker_request["operation"] == "health":
            result = {"ready": True, "reason": "ready"}
        elif worker_request["operation"] == "recall":
            result = {
                "candidates": [
                    {
                        "canonical_ref": document["canonical_ref"],
                        "canonical_revision": 1,
                        "content_hash": document["content_hash"],
                        "projection_text_hash": document["projection_text_hash"],
                        "score": 1.0,
                        "provenance": document["provenance"],
                    }
                ]
            }
        else:
            result = {"forgotten": True}
        return (
            0,
            json.dumps(
                {
                    "schema": "skeleton.cognee_worker.response.v1",
                    "ok": True,
                    "result": result,
                }
            ),
            "",
        )

    venv_python = tmp_path / "cognee_runtime" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    client = CogneeWorkerClient(tmp_path, env=provider_env(), runner=runner)
    client.project(dataset_name=dataset, document=document)
    results = client.recall(
        dataset_name=dataset,
        opaque_scope_hash=opaque_scope_hash(PROJECT, DATASET),
        query="semantic",
        current_canonical_revision=1,
        limit=5,
    )
    assert results[0]["canonical_ref"] == "skeleton:fact.001"
    assert calls == ["project", "recall"]
    receipt_text = next((tmp_path / "cognee_runtime" / "receipts").glob("*.json")).read_text()
    assert "semantic memory fact" not in receipt_text
    assert "skeleton:fact.001" not in receipt_text


def test_package_backend_uses_worker_and_returns_provenance(tmp_path: Path) -> None:
    dataset = opaque_dataset_name(PROJECT, DATASET)
    payload = event()
    document = projection_document(
        project_id=PROJECT,
        dataset_id=DATASET,
        canonical_ref=str(payload["canonical_ref"]),
        canonical_revision=1,
        content_hash=str(payload["content_hash"]),
        projection_text_hash=str(payload["projection_text_hash"]),
        bounded_text=str(payload["bounded_text"]),
    )

    class FakeClient:
        def project(self, **kwargs):
            assert kwargs["dataset_name"] == dataset

        def recall(self, **kwargs):
            return (
                {
                    "canonical_ref": document["canonical_ref"],
                    "canonical_revision": 1,
                    "content_hash": document["content_hash"],
                    "projection_text_hash": document["projection_text_hash"],
                    "score": 1.0,
                    "provenance": document["provenance"],
                },
            )

        def health(self, **kwargs):
            return {
                "ready": True,
                "indexed_canonical_revision": 1,
                "event_count": 1,
            }

        def forget(self, **kwargs):
            return 1

    backend = CogneePackageBackend(
        private_root=tmp_path,
        runtime_enabled=True,
        env=provider_env(),
        client=FakeClient(),  # type: ignore[arg-type]
    )
    adapter = CogneeProjectionAdapter(backend)
    adapter.project(payload)
    result = adapter.recall(request())["results"][0]
    assert result["provenance"][0]["canonical_ref"] == "skeleton:fact.001"


def test_cloud_credentials_fail_closed(tmp_path: Path) -> None:
    env = provider_env()
    env["OPENAI_API_KEY"] = "forbidden"
    with pytest.raises(CogneeLocalRuntimeError) as exc:
        CogneeWorkerClient(tmp_path, env=env)
    assert exc.value.reason_code == "inherited_cloud_credentials_rejected"


def test_old_dependency_contract_remains_compatible(monkeypatch, tmp_path: Path) -> None:
    del tmp_path
    from importlib.machinery import ModuleSpec

    from core.semantic_memory_projection import (
        COGNEE_DEPENDENCY_UNAVAILABLE,
        COGNEE_RUNTIME_NOT_IMPLEMENTED,
    )

    monkeypatch.setattr(
        "core.cognee_projection_adapter.importlib.util.find_spec", lambda _name: None
    )
    unavailable = CogneePackageBackend(runtime_enabled=False)
    health = CogneeProjectionAdapter(unavailable).health(
        project_id=PROJECT, dataset_id=DATASET, current_canonical_revision=0
    )
    assert health["reason_codes"] == [COGNEE_DEPENDENCY_UNAVAILABLE]

    monkeypatch.setattr(
        "core.cognee_projection_adapter.importlib.util.find_spec",
        lambda name: ModuleSpec(name, loader=None),
    )
    present = CogneePackageBackend(runtime_enabled=True)
    health = CogneeProjectionAdapter(present).health(
        project_id=PROJECT, dataset_id=DATASET, current_canonical_revision=0
    )
    assert health["reason_codes"] == [COGNEE_RUNTIME_NOT_IMPLEMENTED]

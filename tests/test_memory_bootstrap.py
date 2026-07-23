from __future__ import annotations

from pathlib import Path

import pytest

from core.cognee_projection_adapter import CogneeProjectionAdapter, DisposableInMemoryCogneeBackend
from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
    MemoryBootstrapError,
    clear_memory_bootstrap_cache,
    private_memory_bootstrap,
)
from core.private_memory_stack import PrivateMemoryStack
from core.semantic_memory_projection import (
    SEMANTIC_PROJECTION_EVENT_SCHEMA,
    canonical_json_hash,
    projection_text_hash,
)


def _request(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
        "project_id": "skeleton",
        "dataset_id": "skeleton.notes",
        "query": "ventilation runbook",
        "exact_keys": ["skeleton.notes:note1"],
    }
    payload.update(updates)
    return payload


def test_private_bootstrap_composes_real_stack_and_derives_gateway_revision(tmp_path: Path) -> None:
    clear_memory_bootstrap_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    mutation = stack.put(
        namespace="skeleton.notes",
        fact_id="note1",
        value={"summary": "ventilation runbook", "tags": ["ops"], "relationships": [{"kind": "supports", "target": "runbook"}]},
    )

    result = private_memory_bootstrap(_request(), private_root=tmp_path)

    assert result.status == "READY"
    assert result.canonical_revision == mutation["canonical_revision"]
    assert result.context["exact"][0]["value"]["summary"] == "ventilation runbook"
    assert result.context["mempalace"]["fresh"] is True
    assert result.context["graphify"]["fresh"] is True
    assert result.public_receipt["canonical_revision"] == mutation["canonical_revision"]
    assert "value" not in result.public_receipt
    assert str(tmp_path) not in str(result.public_receipt)


def test_private_bootstrap_lists_bounded_dataset_when_exact_keys_absent(tmp_path: Path) -> None:
    clear_memory_bootstrap_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="note1", value={"summary": "alpha"})
    stack.put(namespace="skeleton.notes", fact_id="note2", value={"summary": "beta"})

    result = private_memory_bootstrap(_request(exact_keys=[]), private_root=tmp_path)

    assert len(result.context["exact"]) == 2
    assert {item["key"] for item in result.context["exact"]} == {"note1", "note2"}


def test_private_bootstrap_selects_ready_cognee_before_mempalace_and_cache_invalidates_on_revision(
    tmp_path: Path,
) -> None:
    clear_memory_bootstrap_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    first = stack.put(namespace="skeleton.notes", fact_id="note1", value={"summary": "cognee ventilation"})
    backend = DisposableInMemoryCogneeBackend()
    adapter = CogneeProjectionAdapter(backend)
    text = "cognee ventilation"
    adapter.project(
        {
            "schema": SEMANTIC_PROJECTION_EVENT_SCHEMA,
            "project_id": "skeleton",
            "dataset_id": "skeleton.notes",
            "canonical_revision": first["canonical_revision"],
            "canonical_ref": "skeleton.notes:note1",
            "content_hash": canonical_json_hash({"summary": text}),
            "projection_text_hash": projection_text_hash(text),
            "bounded_text": text,
            "provenance": [{"ref": "skeleton.notes:note1", "kind": "exact_source"}],
        }
    )

    boot1 = private_memory_bootstrap(_request(), private_root=tmp_path, cognee_adapter=adapter)
    boot2 = private_memory_bootstrap(_request(), private_root=tmp_path, cognee_adapter=adapter)
    second = stack.put(namespace="skeleton.notes", fact_id="note2", value={"summary": "revision changed"})
    boot3 = private_memory_bootstrap(_request(), private_root=tmp_path, cognee_adapter=adapter)

    assert boot1.context["selected_projection"] == "cognee"
    assert boot2 is boot1
    assert boot3 is not boot1
    assert boot3.canonical_revision == second["canonical_revision"]


def test_private_bootstrap_fails_closed_for_missing_root_public_scope_and_foreign_project(tmp_path: Path) -> None:
    clear_memory_bootstrap_cache()
    with pytest.raises(MemoryBootstrapError, match="private storage is not initialized"):
        private_memory_bootstrap(_request(), private_root=tmp_path)
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    with pytest.raises(MemoryBootstrapError, match="foreign private project"):
        private_memory_bootstrap(_request(project_id="other"), private_root=tmp_path)
    with pytest.raises(MemoryBootstrapError, match="wildcard"):
        private_memory_bootstrap(_request(dataset_id="skeleton.*"), private_root=tmp_path)

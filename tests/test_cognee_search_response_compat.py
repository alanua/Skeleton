from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core import cognee_worker_bootstrap as bootstrap
from core.cognee_search_response_compat import (
    install_cognee_search_response_compat,
)

DATASET = "sk_" + "a" * 48
CHUNKS = SimpleNamespace(value="CHUNKS")


def _install(monkeypatch) -> None:
    def base_stage_wrapper(operation, operation_name, reason):
        del operation_name, reason
        return operation

    monkeypatch.setattr(bootstrap, "_stage_wrapper", base_stage_wrapper)
    assert install_cognee_search_response_compat() is True


def _wrapped_search(monkeypatch, result):
    _install(monkeypatch)

    async def search(**kwargs):
        del kwargs
        return result

    module = SimpleNamespace(search=search)
    assert bootstrap.install_cognee_operation_wrappers(module) is True
    return module.search


def test_direct_chunks_are_minimized_and_bound_to_opaque_dataset(monkeypatch) -> None:
    direct = [
        {
            "text": "{}",
            "score": 0.9,
            "id": "chunk",
            "type": "DocumentChunk",
            "chunk_size": 2,
            "chunk_index": 0,
            "document_id": "private-document-reference",
            "document_name": "private-name",
            "metadata": {"private": "blocked"},
        }
    ]
    search = _wrapped_search(monkeypatch, direct)

    result = asyncio.run(
        search(query_type=CHUNKS, datasets=[DATASET], query_text="probe")
    )

    assert result == [
        {
            "dataset_id": None,
            "dataset_name": DATASET,
            "dataset_tenant_id": None,
            "search_result": [{"text": "{}", "score": 0.9}],
        }
    ]
    assert "private" not in repr(result)


def test_existing_dataset_envelope_is_unchanged(monkeypatch) -> None:
    envelope = [
        {
            "dataset_id": "synthetic",
            "dataset_name": DATASET,
            "dataset_tenant_id": None,
            "search_result": [{"text": "{}"}],
        }
    ]
    search = _wrapped_search(monkeypatch, envelope)

    result = asyncio.run(
        search(query_type=CHUNKS, datasets=[DATASET], query_text="probe")
    )

    assert result is envelope


def test_empty_result_is_unchanged(monkeypatch) -> None:
    search = _wrapped_search(monkeypatch, [])
    assert (
        asyncio.run(search(query_type=CHUNKS, datasets=[DATASET], query_text="probe"))
        == []
    )


def test_foreign_or_non_opaque_dataset_is_not_wrapped(monkeypatch) -> None:
    direct = [{"text": "{}", "document_name": "private"}]
    search = _wrapped_search(monkeypatch, direct)

    result = asyncio.run(
        search(query_type=CHUNKS, datasets=["foreign_dataset"], query_text="probe")
    )

    assert result is direct


def test_chunk_without_text_is_not_wrapped(monkeypatch) -> None:
    malformed = [{"content": "{}", "score": 1.0}]
    search = _wrapped_search(monkeypatch, malformed)

    result = asyncio.run(
        search(query_type=CHUNKS, datasets=[DATASET], query_text="probe")
    )

    assert result is malformed


def test_non_finite_or_boolean_score_is_omitted(monkeypatch) -> None:
    direct = [
        {"text": "{}", "score": float("nan")},
        {"text": "{}", "score": True},
    ]
    search = _wrapped_search(monkeypatch, direct)

    result = asyncio.run(
        search(query_type=CHUNKS, datasets=[DATASET], query_text="probe")
    )

    assert result[0]["search_result"] == [{"text": "{}"}, {"text": "{}"}]


def test_non_chunks_search_is_not_wrapped(monkeypatch) -> None:
    direct = [{"text": "{}", "document_name": "private"}]
    search = _wrapped_search(monkeypatch, direct)

    result = asyncio.run(
        search(
            query_type=SimpleNamespace(value="GRAPH_COMPLETION"),
            datasets=[DATASET],
            query_text="probe",
        )
    )

    assert result is direct

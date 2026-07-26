from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.family_document_local_inference import RESPONSE_SCHEMA_ID, TOPIC_ALIASES
from core.local_inference_adapters import build_default_registry
from core.local_inference_runtime import (
    FileLock,
    InferenceQueue,
    InferenceRuntimeError,
    LocalInferenceWorker,
    OllamaClient,
)


class FakeClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        timeout_seconds: int,
        format_schema: dict[str, object] | None = None,
    ) -> str:
        assert model == "qwen2.5:1.5b"
        assert timeout_seconds == 30
        assert "JSON" in prompt
        assert format_schema is not None
        assert format_schema["properties"]["schema"]["const"] == RESPONSE_SCHEMA_ID  # type: ignore[index]
        self.calls += 1
        return self.outputs.pop(0)


def request_payload() -> dict[str, object]:
    return {
        "ocr_text": "Synthetic German decision for person-a.",
        "allowed_subject_aliases": ["person-a", "person-b", "person-c"],
        "source_kind": "mfp",
    }


def accepted_output() -> dict[str, object]:
    evidence = {key: [key] for key in ("owner", "topic", "jurisdiction", "date", "document_type", "issuer")}
    return {
        "schema": RESPONSE_SCHEMA_ID,
        "route": "ACCEPT",
        "principal_subject_alias": "person-a",
        "linked_subject_aliases": ["person-a"],
        "topic_alias": TOPIC_ALIASES[1],
        "jurisdiction_country": "Germany",
        "document_date": "2026-07-20",
        "date_precision": "day",
        "document_type": "Bescheid",
        "issuer": "Synthetic Authority",
        "summary": "Synthetic decision.",
        "confidence": {
            "overall": 0.95,
            "owner": 0.95,
            "topic": 0.95,
            "jurisdiction": 0.95,
            "date": 0.95,
            "document_type": 0.95,
            "issuer": 0.95,
        },
        "evidence": evidence,
        "event_candidates": [],
        "reason_codes": [],
    }


def test_queue_idempotency_and_done_result(tmp_path: Path) -> None:
    queue = InferenceQueue(tmp_path)
    request_id, created = queue.submit(
        request_type="family_document.classify",
        model="qwen2.5:1.5b",
        payload=request_payload(),
        idempotency_key="synthetic-document-sha256",
        timeout_seconds=30,
    )
    duplicate_id, duplicate_created = queue.submit(
        request_type="family_document.classify",
        model="qwen2.5:1.5b",
        payload=request_payload(),
        idempotency_key="synthetic-document-sha256",
        timeout_seconds=30,
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate_id == request_id
    assert not list((tmp_path / "state").glob("idempotency-*.json"))

    client = FakeClient([json.dumps(accepted_output())])
    worker = LocalInferenceWorker(
        queue,
        build_default_registry(),
        client,  # type: ignore[arg-type]
        allowed_models={"qwen2.5:1.5b"},
    )
    assert worker.process_one() is True
    result = queue.read_result(request_id)
    assert result is not None
    assert result["status"] == "DONE"
    assert queue.status()["counts"]["done"] == 1
    assert client.calls == 1


def test_invalid_model_output_retries_then_quarantines(tmp_path: Path) -> None:
    queue = InferenceQueue(tmp_path)
    queue.submit(
        request_type="family_document.classify",
        model="qwen2.5:1.5b",
        payload=request_payload(),
        idempotency_key="invalid-output-document",
        max_attempts=2,
        timeout_seconds=30,
    )
    client = FakeClient(["not-json", "not-json"])
    worker = LocalInferenceWorker(
        queue,
        build_default_registry(),
        client,  # type: ignore[arg-type]
        allowed_models={"qwen2.5:1.5b"},
    )
    assert worker.process_one() is True
    retry_file = next((tmp_path / "retry").glob("*.json"))
    envelope = json.loads(retry_file.read_text(encoding="utf-8"))
    envelope["next_attempt_at"] = 0
    retry_file.write_text(json.dumps(envelope), encoding="utf-8")
    assert worker.process_one() is True
    assert queue.status()["counts"]["quarantine"] == 1
    assert client.calls == 2


def test_model_allowlist_fails_closed(tmp_path: Path) -> None:
    queue = InferenceQueue(tmp_path)
    queue.submit(
        request_type="family_document.classify",
        model="unapproved:latest",
        payload=request_payload(),
        idempotency_key="unapproved-model-document",
        max_attempts=1,
        timeout_seconds=30,
    )
    worker = LocalInferenceWorker(
        queue,
        build_default_registry(),
        FakeClient([]),  # type: ignore[arg-type]
        allowed_models={"qwen2.5:1.5b"},
    )
    assert worker.process_one() is True
    assert queue.status()["counts"]["quarantine"] == 1


def test_stale_processing_is_recovered(tmp_path: Path) -> None:
    queue = InferenceQueue(tmp_path)
    queue.submit(
        request_type="family_document.classify",
        model="qwen2.5:1.5b",
        payload=request_payload(),
        idempotency_key="stale-processing-document",
        timeout_seconds=30,
    )
    assert queue.claim_next() is not None
    processing = next((tmp_path / "processing").glob("*.json"))
    os.utime(processing, (1, 1))
    assert queue.recover_stale_processing(stale_after_seconds=1, now=1000) == 1
    assert queue.status()["counts"]["retry"] == 1


def test_worker_lock_is_single_instance(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    with FileLock(lock_path, nonblocking=True):
        with pytest.raises(InferenceRuntimeError):
            with FileLock(lock_path, nonblocking=True):
                pass


def test_ollama_endpoint_must_be_loopback() -> None:
    OllamaClient("http://127.0.0.1:11434")
    OllamaClient("http://localhost:11434")
    with pytest.raises(InferenceRuntimeError):
        OllamaClient("https://127.0.0.1:11434")
    with pytest.raises(InferenceRuntimeError):
        OllamaClient("http://example.com:11434")


def test_idempotency_conflict_fails_closed(tmp_path: Path) -> None:
    queue = InferenceQueue(tmp_path)
    queue.submit(
        request_type="family_document.classify",
        model="qwen2.5:1.5b",
        payload=request_payload(),
        idempotency_key="conflicting-document-key",
        timeout_seconds=30,
    )
    changed = request_payload()
    changed["ocr_text"] = "Different payload"
    with pytest.raises(InferenceRuntimeError) as caught:
        queue.submit(
            request_type="family_document.classify",
            model="qwen2.5:1.5b",
            payload=changed,
            idempotency_key="conflicting-document-key",
            timeout_seconds=30,
        )
    assert caught.value.reason_code == "idempotency_conflict"


def test_stale_processing_with_completed_result_finalizes_without_reinference(
    tmp_path: Path,
) -> None:
    queue = InferenceQueue(tmp_path)
    request_id, _created = queue.submit(
        request_type="family_document.classify",
        model="qwen2.5:1.5b",
        payload=request_payload(),
        idempotency_key="completed-before-done-move",
        timeout_seconds=30,
    )
    envelope = queue.claim_next()
    assert envelope is not None
    result = {
        "schema": "skeleton.local_inference.result.v1",
        "request_id": request_id,
        "request_type": "family_document.classify",
        "status": "DONE",
        "model": "qwen2.5:1.5b",
        "attempt": 1,
        "completed_at": "2026-07-26T20:00:00Z",
        "reason_codes": [],
        "output": accepted_output(),
    }
    (tmp_path / "results" / f"{request_id}.json").write_text(
        json.dumps(result), encoding="utf-8"
    )
    processing = tmp_path / "processing" / f"{request_id}.json"
    os.utime(processing, (1, 1))
    assert queue.recover_stale_processing(stale_after_seconds=1, now=1000) == 1
    assert queue.status()["counts"]["done"] == 1
    assert queue.status()["counts"]["retry"] == 0

from __future__ import annotations

import pytest

from core.family_document_local_inference import (
    RESPONSE_SCHEMA_ID,
    TOPIC_ALIASES,
    bind_family_subject_aliases,
    build_family_document_prompt,
    validate_family_document_output,
)
from core.local_inference_adapters import InferenceValidationError


def payload() -> dict[str, object]:
    return {
        "ocr_text": "Bescheid fuer Person A, ausgestellt durch Behoerde am 2026-07-20.",
        "allowed_subject_aliases": ["person-a", "person-b", "person-c"],
        "languages": ["de"],
        "source_kind": "mfp",
        "page_count": 2,
        "mime_type": "application/pdf",
    }


def output(route: str = "ACCEPT") -> dict[str, object]:
    evidence = {
        "owner": ["Person A"],
        "topic": ["residence decision"],
        "jurisdiction": ["German authority"],
        "date": ["2026-07-20"],
        "document_type": ["Bescheid"],
        "issuer": ["Behoerde"],
    }
    return {
        "schema": RESPONSE_SCHEMA_ID,
        "route": route,
        "principal_subject_alias": "person-a" if route == "ACCEPT" else None,
        "linked_subject_aliases": ["person-a"] if route == "ACCEPT" else [],
        "topic_alias": TOPIC_ALIASES[1] if route == "ACCEPT" else None,
        "jurisdiction_country": "Germany" if route == "ACCEPT" else None,
        "document_date": "2026-07-20" if route == "ACCEPT" else None,
        "date_precision": "day" if route == "ACCEPT" else "unknown",
        "document_type": "Bescheid" if route == "ACCEPT" else None,
        "issuer": "Behoerde" if route == "ACCEPT" else None,
        "summary": "Synthetic official decision.",
        "confidence": {
            "overall": 0.95 if route == "ACCEPT" else 0.4,
            "owner": 0.95,
            "topic": 0.9,
            "jurisdiction": 0.9,
            "date": 0.95,
            "document_type": 0.9,
            "issuer": 0.9,
        },
        "evidence": evidence,
        "event_candidates": [],
        "reason_codes": [] if route == "ACCEPT" else ["OWNER_UNCERTAIN"],
    }


def test_prompt_is_bounded_and_forbids_side_effects() -> None:
    prompt = build_family_document_prompt(payload())
    assert "Return exactly one JSON object" in prompt
    assert "Never emit paths" in prompt
    assert "person-a" in prompt


def test_valid_accept_and_review_outputs() -> None:
    assert validate_family_document_output(output(), payload())["route"] == "ACCEPT"
    assert validate_family_document_output(output("REVIEW"), payload())["route"] == "REVIEW"


def test_accept_requires_confidence_and_required_values() -> None:
    value = output()
    value["confidence"]["overall"] = 0.79  # type: ignore[index]
    with pytest.raises(InferenceValidationError):
        validate_family_document_output(value, payload())


def test_output_rejects_side_effect_property() -> None:
    value = output()
    value["shell_command"] = "mv file"
    with pytest.raises(InferenceValidationError):
        validate_family_document_output(value, payload())


def test_handoff_ingestor_queues_completed_ocr_packet(tmp_path) -> None:
    from core.family_document_local_inference import FamilyDocumentHandoffIngestor
    from core.local_inference_runtime import InferenceQueue

    queue = InferenceQueue(tmp_path / "queue")
    handoff = tmp_path / "handoff"
    ingestor = FamilyDocumentHandoffIngestor(
        handoff,
        queue,
        model="qwen2.5:1.5b",
        allowed_subject_aliases=("person-a", "person-b", "person-c"),
        timeout_seconds=30,
    )
    packet = {
        "schema": "skeleton.family_document_inference_handoff.v1",
        "idempotency_key": "synthetic-handoff-document",
        "payload": {
            key: value
            for key, value in payload().items()
            if key != "allowed_subject_aliases"
        },
    }
    (handoff / "pending" / "packet.json").write_text(
        __import__("json").dumps(packet), encoding="utf-8"
    )
    assert ingestor.ingest_one() is True
    assert queue.status()["counts"]["pending"] == 1
    assert ingestor.status()["accepted"] == 1
    receipt = __import__("json").loads(
        (handoff / "receipts" / "packet.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "QUEUED"
    assert isinstance(receipt["request_id"], str)


def test_invalid_handoff_routes_to_review(tmp_path) -> None:
    from core.family_document_local_inference import FamilyDocumentHandoffIngestor
    from core.local_inference_runtime import InferenceQueue

    queue = InferenceQueue(tmp_path / "queue")
    handoff = tmp_path / "handoff"
    ingestor = FamilyDocumentHandoffIngestor(
        handoff,
        queue,
        model="qwen2.5:1.5b",
        allowed_subject_aliases=("person-a", "person-b", "person-c"),
    )
    (handoff / "pending" / "bad.json").write_text("{}", encoding="utf-8")
    assert ingestor.ingest_one() is True
    assert ingestor.status()["review"] == 1
    assert queue.status()["counts"]["pending"] == 0


def test_output_rejects_alias_not_in_request() -> None:
    value = output()
    value["principal_subject_alias"] = "invented-person"
    value["linked_subject_aliases"] = ["invented-person"]
    with pytest.raises(InferenceValidationError):
        validate_family_document_output(value, payload())


def test_family_alias_binding_rejects_packet_override() -> None:
    raw = {
        "ocr_text": "Synthetic",
        "source_kind": "mfp",
    }
    bound = bind_family_subject_aliases(raw, ("person-a", "person-b", "person-c"))
    assert bound["allowed_subject_aliases"] == ["person-a", "person-b", "person-c"]
    with pytest.raises(InferenceValidationError):
        bind_family_subject_aliases(payload(), ("person-a", "person-b", "person-c"))


def test_invalid_dates_fail_closed() -> None:
    value = output()
    value["document_date"] = "2026-02-31"
    with pytest.raises(InferenceValidationError):
        validate_family_document_output(value, payload())


def test_handoff_recovers_interrupted_claim(tmp_path) -> None:
    import json

    from core.family_document_local_inference import FamilyDocumentHandoffIngestor
    from core.local_inference_runtime import InferenceQueue

    queue = InferenceQueue(tmp_path / "queue")
    handoff = tmp_path / "handoff"
    ingestor = FamilyDocumentHandoffIngestor(
        handoff,
        queue,
        model="qwen2.5:1.5b",
        allowed_subject_aliases=("person-a", "person-b", "person-c"),
    )
    packet = {
        "schema": "skeleton.family_document_inference_handoff.v1",
        "idempotency_key": "interrupted-handoff-document",
        "payload": {
            "ocr_text": "Synthetic interrupted packet",
            "source_kind": "mfp",
        },
    }
    (handoff / "processing" / "interrupted.json").write_text(
        json.dumps(packet), encoding="utf-8"
    )
    assert ingestor.ingest_one() is True
    assert ingestor.status()["accepted"] == 1
    assert queue.status()["counts"]["pending"] == 1

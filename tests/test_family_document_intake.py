from __future__ import annotations

import hashlib

import pytest

from core.family_document_intake import build_family_document_record, build_intake_request
from core.family_document_taxonomy import classify_family_document_text


def test_intake_record_excludes_raw_ocr_text_and_paths(tmp_path) -> None:
    path = tmp_path / "synthetic.txt"
    path.write_text("private-like synthetic document text", encoding="utf-8")
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    request = build_intake_request(path, source_id="source-1", source_sha256=source_sha256)

    record = build_family_document_record(request, {"route": "ACCEPT", "summary": "Synthetic"})
    rendered = __import__("json").dumps(record, sort_keys=True)

    assert record["schema"] == "skeleton.family_document_record.v1"
    assert "private-like synthetic document text" not in rendered
    assert str(path) not in rendered
    assert record["classification"]["summary"] == "Synthetic"


def test_intake_rejects_source_changed_after_stable_file_gate(tmp_path) -> None:
    path = tmp_path / "synthetic.txt"
    path.write_text("version one", encoding="utf-8")
    stable_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text("version two", encoding="utf-8")

    with pytest.raises(ValueError, match="source changed after stable-file gate"):
        build_intake_request(path, source_id="source-1", source_sha256=stable_sha256)


def test_local_classifier_accepts_only_unambiguous_evidence() -> None:
    classification = classify_family_document_text(
        "Finanzamt Bescheid Steuer für Person-A. Sehr geehrte Person-A, Entscheidung zur Steuer.",
        ("Person-A", "Person-B", "Person-C"),
    )
    assert classification["route"] == "ACCEPT"
    assert classification["principal_subject_alias"] == "Person-A"
    assert classification["issuer"] == "Finanzamt"
    assert classification["document_type"] == "Bescheid"
    assert classification["topic_alias"] == "04 work_tax_and_business"


def test_local_classifier_routes_ambiguous_owner_to_review() -> None:
    classification = classify_family_document_text(
        "Jobcenter Bescheid Bürgergeld für Person-A und Person-B.",
        ("Person-A", "Person-B", "Person-C"),
    )
    assert classification["route"] == "REVIEW"
    assert "OWNER_AMBIGUOUS" in classification["reason_codes"]


def test_mutation_before_ocr_rejected(tmp_path, monkeypatch):
    import core.family_document_intake as module

    path = tmp_path / "synthetic.txt"
    path.write_bytes(b"synthetic original")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original = module.read_snapshot
    calls = []

    def mutate(candidate, expected=()):
        calls.append(1)
        if len(calls) == 2:
            path.write_bytes(b"synthetic replacement")
        return original(candidate, expected)

    monkeypatch.setattr(module, "read_snapshot", mutate)
    monkeypatch.setattr(module, "read_local_document", lambda _: pytest.fail("OCR must not run"))
    with pytest.raises(ValueError):
        build_intake_request(path, source_id="synthetic", source_sha256=digest)


def test_record_schema_supports_optional_bank_evidence_and_legacy_records() -> None:
    import json
    from pathlib import Path

    import jsonschema

    from core.family_document_intake import FamilyDocumentIntakeRequest

    schema = json.loads((Path(__file__).parents[1] / "schemas/family_document_record.schema.json").read_text())
    request = FamilyDocumentIntakeRequest(
        source_id="synthetic-bank", source_sha256="a" * 64, ocr_text="synthetic"
    )
    legacy = build_family_document_record(request, {"route": "REVIEW"})
    jsonschema.validate(legacy, schema)
    classification = classify_family_document_text(
        "MBS Girovertrag\nKontoinhaber: Subject-A\nKontoführungsgebühr: 2,00 EUR monatlich",
        ("Subject-A", "Subject-B", "Subject-C"),
    )
    record = build_family_document_record(request, classification)
    assert record["classification"]["bank_contract"] == classification["bank_contract"]
    jsonschema.validate(record, schema)
    classification["bank_contract"]["fees"][0]["amount"] = "invented"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, schema)

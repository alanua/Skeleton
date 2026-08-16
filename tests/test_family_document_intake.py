from __future__ import annotations

import hashlib

import pytest

from core.family_document_intake import build_family_document_record, build_intake_request


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

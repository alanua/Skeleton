from __future__ import annotations

from pathlib import Path

import pytest

from core.local_document_ocr import LocalDocumentOcrError, LocalOcrResult, run_local_ocr


def test_text_ocr_is_local_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "doc.txt"
    source.write_text("Synthetic OCR text", encoding="utf-8")

    result = run_local_ocr(source)

    assert result == LocalOcrResult(text="Synthetic OCR text", engine="local_text", page_count=1)


def test_pdf_without_local_recognizer_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF synthetic\n%%EOF")

    with pytest.raises(LocalDocumentOcrError) as excinfo:
        run_local_ocr(source)

    assert excinfo.value.reason_code == "LOCAL_OCR_UNAVAILABLE"


def test_corrupt_encrypted_and_unsupported_cases_fail_visibly(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pdf"
    encrypted = tmp_path / "encrypted.pdf"
    unsupported = tmp_path / "payload.bin"
    corrupt.write_bytes(b"%PDF synthetic without trailer")
    encrypted.write_bytes(b"%PDF /Encrypt synthetic\n%%EOF")
    unsupported.write_bytes(b"synthetic")

    with pytest.raises(LocalDocumentOcrError) as corrupt_exc:
        run_local_ocr(corrupt, recognizer=lambda path: "unused")
    with pytest.raises(LocalDocumentOcrError) as encrypted_exc:
        run_local_ocr(encrypted, recognizer=lambda path: "unused")
    with pytest.raises(LocalDocumentOcrError) as unsupported_exc:
        run_local_ocr(unsupported)

    assert corrupt_exc.value.reason_code == "DOCUMENT_UNREADABLE"
    assert encrypted_exc.value.reason_code == "DOCUMENT_UNREADABLE"
    assert unsupported_exc.value.reason_code == "UNSUPPORTED_DOCUMENT_TYPE"

from __future__ import annotations

import sys

import pytest

import core.local_document_ocr as ocr
from core.local_document_ocr import LocalDocumentOcrError, read_local_document, read_local_document_text


def test_reads_bounded_utf8_text(tmp_path) -> None:
    path = tmp_path / "scan.txt"
    path.write_text("synthetic document", encoding="utf-8")

    result = read_local_document(path)

    assert result.text == "synthetic document"
    assert result.page_count == 1
    assert result.mime_type == "text/plain"
    assert read_local_document_text(path) == "synthetic document"


def test_empty_document_fails_closed(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(LocalDocumentOcrError):
        read_local_document_text(path)


def test_pdf_uses_extracted_page_count(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"synthetic-pdf")
    monkeypatch.setattr(ocr, "_pdf_text", lambda _path: "PDF text")
    monkeypatch.setattr(ocr, "_pdf_page_count", lambda _path: 7)

    result = read_local_document(path)

    assert result.text == "PDF text"
    assert result.page_count == 7
    assert result.mime_type == "application/pdf"


def test_image_uses_local_ocr_path(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scan.png"
    path.write_bytes(b"synthetic-image")
    monkeypatch.setattr(ocr, "_image_text", lambda _path: "image text")

    result = read_local_document(path)

    assert result.text == "image text"
    assert result.page_count == 1
    assert result.mime_type == "image/png"


def test_office_uses_local_conversion_path(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scan.docx"
    path.write_bytes(b"synthetic-office")
    monkeypatch.setattr(ocr, "_office_text", lambda _path: ("office text", 4))

    result = read_local_document(path)

    assert result.text == "office text"
    assert result.page_count == 4


def test_dependency_preflight_reports_missing_provider(monkeypatch) -> None:
    monkeypatch.setattr(ocr, "PDFTOTEXT", sys.executable)
    monkeypatch.setattr(ocr, "PDFINFO", "/missing/pdfinfo")
    monkeypatch.setattr(ocr, "OCRMY_PDF", "/missing/ocrmypdf")

    status = ocr.local_ocr_dependency_status(suffixes=(".pdf",))

    assert status["status"] == "MISSING"
    assert status["missing"] == ["ocrmypdf", "pdfinfo"]


def test_dependency_preflight_accepts_text_without_external_provider() -> None:
    status = ocr.require_local_ocr_dependencies(suffixes=(".txt",))

    assert status["status"] == "READY"


def test_bounded_provider_nonzero_fails_closed() -> None:
    with pytest.raises(LocalDocumentOcrError, match="provider failed"):
        ocr._run((sys.executable, "-c", "raise SystemExit(3)"), timeout=5, max_output=1024)


def test_bounded_provider_output_overflow_fails_closed() -> None:
    with pytest.raises(LocalDocumentOcrError, match="output limit"):
        ocr._run((sys.executable, "-c", "print('x' * 4096)"), timeout=5, max_output=128)


def test_bounded_provider_timeout_kills_process_group() -> None:
    with pytest.raises(LocalDocumentOcrError, match="timeout"):
        ocr._run((sys.executable, "-c", "import time; time.sleep(5)"), timeout=1, max_output=1024)

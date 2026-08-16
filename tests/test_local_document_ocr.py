from __future__ import annotations

from pathlib import Path

import pytest

from core.local_document_ocr import (
    CommandResult,
    LocalDocumentOcr,
    LocalDocumentOcrError,
    OcrConfig,
    read_local_document,
    read_local_document_text,
)


def test_reads_bounded_utf8_text(tmp_path) -> None:
    path = tmp_path / "scan.txt"
    path.write_text("synthetic document", encoding="utf-8")

    assert read_local_document_text(path) == "synthetic document"


def test_empty_document_fails_closed(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(LocalDocumentOcrError):
        read_local_document_text(path)


def _config() -> OcrConfig:
    return OcrConfig(
        executables={
            "pdftotext": "/test/pdftotext",
            "pdfinfo": "/test/pdfinfo",
            "ocrmypdf": "/test/ocrmypdf",
            "tesseract": "/test/tesseract",
            "libreoffice": "/test/libreoffice",
        }
    )


def test_text_layer_pdf_uses_pdftotext_and_actual_page_count(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-pdf")
    calls: list[str] = []

    def runner(argv, cwd: Path, timeout: int, max_output: int) -> CommandResult:
        del cwd, timeout, max_output
        calls.append(argv[0])
        if argv[0].endswith("pdfinfo"):
            return CommandResult(0, b"Pages:          4\n", b"")
        if argv[0].endswith("pdftotext"):
            return CommandResult(0, b"Synthetic text layer\n", b"")
        raise AssertionError(argv)

    result = read_local_document(source, ocr=LocalDocumentOcr(_config(), runner=runner))

    assert result.text == "Synthetic text layer"
    assert result.page_count == 4
    assert result.providers == ("pdftotext",)
    assert not any(call.endswith("ocrmypdf") for call in calls)


def test_image_only_pdf_uses_ocr_fallback_without_changing_source_hash(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-image-pdf")

    def runner(argv, cwd: Path, timeout: int, max_output: int) -> CommandResult:
        del cwd, timeout, max_output
        if argv[0].endswith("pdfinfo"):
            return CommandResult(0, b"Pages: 2\n", b"")
        if argv[0].endswith("pdftotext"):
            if str(argv[-2]).endswith("searchable.pdf"):
                return CommandResult(0, b"OCR result\n", b"")
            return CommandResult(0, b"\n", b"")
        if argv[0].endswith("ocrmypdf"):
            Path(argv[-1]).write_bytes(b"synthetic-searchable-pdf")
            return CommandResult(0, b"", b"")
        raise AssertionError(argv)

    result = read_local_document(source, ocr=LocalDocumentOcr(_config(), runner=runner))

    assert result.text == "OCR result"
    assert result.page_count == 2
    assert result.providers == ("ocrmypdf", "pdftotext")
    assert len(result.source_sha256) == 64


def test_image_uses_local_tesseract(tmp_path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"synthetic-image")

    def runner(argv, cwd: Path, timeout: int, max_output: int) -> CommandResult:
        del cwd, timeout, max_output
        assert argv[0].endswith("tesseract")
        return CommandResult(0, b"Image OCR\n", b"")

    result = read_local_document(source, ocr=LocalDocumentOcr(_config(), runner=runner))

    assert result.text == "Image OCR"
    assert result.page_count == 1
    assert result.mime_type == "image/png"


def test_pdf_page_count_failure_is_fail_closed(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-pdf")

    def runner(argv, cwd: Path, timeout: int, max_output: int) -> CommandResult:
        del cwd, timeout, max_output
        if argv[0].endswith("pdfinfo"):
            return CommandResult(0, b"not-a-page-count", b"")
        raise AssertionError(argv)

    with pytest.raises(LocalDocumentOcrError, match="pdf_page_count_missing"):
        read_local_document(source, ocr=LocalDocumentOcr(_config(), runner=runner))

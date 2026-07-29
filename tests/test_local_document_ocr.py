from __future__ import annotations

from pathlib import Path

import pytest

from core.local_document_ocr import CommandResult, LocalDocumentOcr, OcrConfig, OcrError


def config() -> OcrConfig:
    return OcrConfig(executables={"pdftotext": "/usr/bin/pdftotext", "ocrmypdf": "/usr/bin/ocrmypdf", "tesseract": "/usr/bin/tesseract", "libreoffice": "/usr/bin/libreoffice"})


def test_pdf_uses_pdftotext_when_text_exists(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"; source.write_bytes(b"pdf")
    calls = []
    def runner(argv, cwd, timeout, max_output):
        del cwd, timeout, max_output
        calls.append(tuple(argv)); return CommandResult(0, b"Issuer: Jobcenter\nNotice", b"")
    result = LocalDocumentOcr(config(), runner=runner).extract(source)
    assert result.providers == ("pdftotext",)
    assert calls[0][0] == "/usr/bin/pdftotext"


def test_pdf_falls_back_to_ocrmypdf_then_pdftotext(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"; source.write_bytes(b"pdf")
    calls = []
    def runner(argv, cwd, timeout, max_output):
        del timeout, max_output
        calls.append(tuple(argv))
        if argv[0] == "/usr/bin/pdftotext" and len(calls) == 1: return CommandResult(0, b"", b"")
        if argv[0] == "/usr/bin/ocrmypdf": Path(argv[-1]).write_bytes(b"ocr-pdf"); return CommandResult(0, b"", b"")
        return CommandResult(0, b"Recovered text", b"")
    result = LocalDocumentOcr(config(), runner=runner).extract(source)
    assert result.providers == ("ocrmypdf", "pdftotext")


def test_office_is_converted_to_pdf_before_extraction(tmp_path: Path) -> None:
    source = tmp_path / "a.docx"; source.write_bytes(b"docx")
    calls = []
    def runner(argv, cwd, timeout, max_output):
        del timeout, max_output
        calls.append(tuple(argv))
        if argv[0] == "/usr/bin/libreoffice": (cwd / "a.pdf").write_bytes(b"pdf"); return CommandResult(0, b"", b"")
        return CommandResult(0, b"Converted text", b"")
    result = LocalDocumentOcr(config(), runner=runner).extract(source)
    assert result.providers == ("libreoffice", "pdftotext")
    assert calls[0][:4] == ("/usr/bin/libreoffice", "--headless", "--convert-to", "pdf")


def test_image_uses_tesseract_with_fixed_languages(tmp_path: Path) -> None:
    source = tmp_path / "a.png"; source.write_bytes(b"png")
    seen = []
    def runner(argv, cwd, timeout, max_output):
        del cwd, timeout, max_output
        seen.append(tuple(argv)); return CommandResult(0, b"Image text", b"")
    result = LocalDocumentOcr(config(), runner=runner).extract(source)
    assert result.providers == ("tesseract",)
    assert seen[0] == ("/usr/bin/tesseract", str(source.resolve()), "stdout", "-l", "eng+deu+ukr", "--psm", "6")


def test_executables_must_be_exact_absolute_paths() -> None:
    with pytest.raises(OcrError):
        OcrConfig(executables={"pdftotext": "pdftotext", "ocrmypdf": "/x", "tesseract": "/x", "libreoffice": "/x"})

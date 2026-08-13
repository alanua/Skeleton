from __future__ import annotations

import zipfile

import pytest

from core.local_document_ocr import ALLOWED_EXTENSIONS, LocalDocumentOCRError, extract_local_document


def test_exact_allowlist_contains_required_formats() -> None:
    assert ALLOWED_EXTENSIONS == (
        ".pdf",
        ".tif",
        ".tiff",
        ".png",
        ".jpg",
        ".jpeg",
        ".txt",
        ".doc",
        ".docx",
        ".odt",
        ".rtf",
        ".xls",
        ".xlsx",
        ".ods",
    )


def test_txt_extraction_is_local_and_hashed(tmp_path) -> None:
    source = tmp_path / "notice.txt"
    source.write_text("Issuer: Synthetic Office\nresidence notice 2026-07-20", encoding="utf-8")
    result = extract_local_document(source)
    assert result.text.startswith("Issuer: Synthetic")
    assert result.source_sha256
    assert result.extractor == "plain-text-allowlist"


def test_docx_zip_xml_extraction(tmp_path) -> None:
    source = tmp_path / "doc.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", "<w:document xmlns:w='w'><w:t>Issuer: Synthetic School certificate 2026-01-02</w:t></w:document>")
    result = extract_local_document(source)
    assert "Synthetic School" in result.text


def test_unapproved_extension_fails_closed(tmp_path) -> None:
    source = tmp_path / "secret.exe"
    source.write_bytes(b"no")
    with pytest.raises(LocalDocumentOCRError):
        extract_local_document(source)


def test_xls_allowlist_extracts_local_text(tmp_path) -> None:
    source = tmp_path / "sheet.xls"
    source.write_text("Issuer: Synthetic Bank contract 2026-03-04", encoding="utf-8")
    result = extract_local_document(source)
    assert "Synthetic Bank" in result.text

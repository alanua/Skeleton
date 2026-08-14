from __future__ import annotations

import pytest

from core.local_document_ocr import LocalDocumentOcrError, read_local_document_text


def test_reads_bounded_utf8_text(tmp_path) -> None:
    path = tmp_path / "scan.txt"
    path.write_text("synthetic document", encoding="utf-8")

    assert read_local_document_text(path) == "synthetic document"


def test_empty_document_fails_closed(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(LocalDocumentOcrError):
        read_local_document_text(path)

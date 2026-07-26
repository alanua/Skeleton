from __future__ import annotations

from pathlib import Path

from core.family_document_intake import _minimal_pdf
from core.local_document_ocr import STRICT_SEPARATOR_MARKER, extract_local_ocr_text, recognize_local_page


def test_local_separator_recognition_is_strict(tmp_path: Path) -> None:
    pdf = tmp_path / "separator.pdf"
    pdf.write_bytes(_minimal_pdf(["", STRICT_SEPARATOR_MARKER, f"x {STRICT_SEPARATOR_MARKER}"]))

    assert recognize_local_page(pdf, 0).strict_separator is False
    assert recognize_local_page(pdf, 1).strict_separator is True
    ambiguous = recognize_local_page(pdf, 2)
    assert ambiguous.strict_separator is False
    assert ambiguous.ambiguous_separator is True


def test_local_ocr_receipt_is_aggregate_only(tmp_path: Path) -> None:
    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(_minimal_pdf(["private text"]))

    receipt = extract_local_ocr_text(pdf)

    assert receipt["page_count"] == 1
    assert "private text" not in str(receipt)

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


STRICT_SEPARATOR_MARKER = "SKELETON_MFP_PHYSICAL_SEPARATOR_V1"
_PAGE_RE = re.compile(rb"%%SKELETON_PAGE(?::(?P<label>[^\n\r%]*))?")


@dataclass(frozen=True)
class LocalPageRecognition:
    page_index: int
    strict_separator: bool
    ambiguous_separator: bool = False


def synthetic_pdf_page_texts(path: str | Path) -> list[str]:
    data = Path(path).read_bytes()
    matches = list(_PAGE_RE.finditer(data))
    if matches:
        pages: list[str] = []
        for match in matches:
            raw = (match.group("label") or b"").decode("utf-8", errors="ignore").strip()
            pages.append(raw)
        return pages
    page_count = len(re.findall(rb"/Type\s*/Page\b", data))
    pages = max(0, page_count - len(re.findall(rb"/Type\s*/Pages\b", data)))
    return ["" for _ in range(pages or 1)]


def recognize_local_page(path: str | Path, page_index: int) -> LocalPageRecognition:
    pages = synthetic_pdf_page_texts(path)
    text = pages[page_index] if 0 <= page_index < len(pages) else ""
    count = text.count(STRICT_SEPARATOR_MARKER)
    return LocalPageRecognition(
        page_index=page_index,
        strict_separator=count == 1 and text.strip() == STRICT_SEPARATOR_MARKER,
        ambiguous_separator=count > 1 or (count == 1 and text.strip() != STRICT_SEPARATOR_MARKER),
    )


def extract_local_ocr_text(path: str | Path) -> dict[str, object]:
    pages = synthetic_pdf_page_texts(path)
    return {
        "schema": "skeleton.local_document_ocr_receipt.v1",
        "status": "DONE",
        "page_count": len(pages),
        "private_text_in_public_receipt": False,
    }

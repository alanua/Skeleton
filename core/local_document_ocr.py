from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class LocalDocumentOcrError(RuntimeError):
    """Raised when a local document cannot be converted to bounded text."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    page_count: int
    mime_type: str
    source_sha256: str


def read_local_document_text(path: Path, *, max_bytes: int = 2_000_000) -> str:
    data = path.read_bytes()
    if not data:
        raise LocalDocumentOcrError("document is empty")
    if len(data) > max_bytes:
        raise LocalDocumentOcrError("document exceeds local OCR byte limit")
    text = data.decode("utf-8", errors="ignore").strip()
    if not text:
        raise LocalDocumentOcrError("document produced no text")
    return text[:24000]

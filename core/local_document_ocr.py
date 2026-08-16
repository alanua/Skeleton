from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class LocalDocumentOcrError(RuntimeError):
    """Raised when a local document cannot be converted to bounded text."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    page_count: int
    mime_type: str
    source_sha256: str


PDFTOTEXT = "/usr/bin/pdftotext"
PDFINFO = "/usr/bin/pdfinfo"
TESSERACT = "/usr/bin/tesseract"
OCRMY_PDF = "/usr/bin/ocrmypdf"


def _run(argv: Sequence[str], *, timeout: int = 120, max_output: int = 2_000_000) -> str:
    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalDocumentOcrError("local OCR timeout") from exc
    output = completed.stdout or ""
    error = completed.stderr or ""
    if len(output.encode("utf-8", errors="ignore")) + len(error.encode("utf-8", errors="ignore")) > max_output:
        raise LocalDocumentOcrError("local OCR output limit exceeded")
    if completed.returncode != 0:
        raise LocalDocumentOcrError("local OCR provider failed")
    return output


def _pdf_page_count(path: Path) -> int:
    output = _run((PDFINFO, str(path)), timeout=30, max_output=128_000)
    for line in output.splitlines():
        if line.lower().startswith("pages:"):
            try:
                pages = int(line.split(":", 1)[1].strip())
            except ValueError as exc:
                raise LocalDocumentOcrError("invalid PDF page count") from exc
            if pages <= 0:
                raise LocalDocumentOcrError("invalid PDF page count")
            return pages
    raise LocalDocumentOcrError("PDF page count unavailable")


def _pdf_text(path: Path) -> str:
    output = _run((PDFTOTEXT, "-layout", "-nopgbrk", str(path), "-"), timeout=90)
    text = output.strip()
    if text:
        return text
    if not Path(OCRMY_PDF).is_file():
        raise LocalDocumentOcrError("PDF has no text layer and OCR provider is unavailable")
    with tempfile.TemporaryDirectory(prefix="skeleton-mfp-ocr-") as tmp:
        searchable = Path(tmp) / "searchable.pdf"
        _run(
            (OCRMY_PDF, "--skip-text", "--deskew", "--rotate-pages", str(path), str(searchable)),
            timeout=180,
            max_output=512_000,
        )
        text = _run((PDFTOTEXT, "-layout", "-nopgbrk", str(searchable), "-"), timeout=90).strip()
    if not text:
        raise LocalDocumentOcrError("document produced no text")
    return text


def _image_text(path: Path) -> str:
    output = _run((TESSERACT, str(path), "stdout", "-l", "deu+eng+ukr+rus"), timeout=120)
    text = output.strip()
    if not text:
        raise LocalDocumentOcrError("document produced no text")
    return text


def read_local_document(path: Path, *, max_bytes: int = 50_000_000) -> OcrResult:
    stat = path.stat()
    if stat.st_size <= 0:
        raise LocalDocumentOcrError("document is empty")
    if stat.st_size > max_bytes:
        raise LocalDocumentOcrError("document exceeds local OCR byte limit")
    data_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(path)
        pages = _pdf_page_count(path)
        mime_type = "application/pdf"
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        text = _image_text(path)
        pages = 1
        mime_type = "image/tiff" if suffix in {".tif", ".tiff"} else f"image/{'jpeg' if suffix in {'.jpg', '.jpeg'} else 'png'}"
    else:
        data = path.read_bytes()
        text = data.decode("utf-8", errors="ignore").strip()
        pages = 1
        mime_type = "text/plain"
        if not text:
            raise LocalDocumentOcrError("document produced no text")
    return OcrResult(text=text[:24000], page_count=pages, mime_type=mime_type, source_sha256=data_hash)


def read_local_document_text(path: Path, *, max_bytes: int = 50_000_000) -> str:
    return read_local_document(path, max_bytes=max_bytes).text

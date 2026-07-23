from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class LocalDocumentOcrError(ValueError):
    """Raised when local-only OCR cannot produce text."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LocalOcrResult:
    text: str
    engine: str
    page_count: int = 1


SUPPORTED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt"})


def run_local_ocr(
    source: Path,
    *,
    recognizer: Callable[[Path], str | LocalOcrResult] | None = None,
    supported_extensions: Iterable[str] = SUPPORTED_EXTENSIONS,
) -> LocalOcrResult:
    """Extract text through a caller-injected local recognizer.

    The default implementation intentionally handles only plain text. Scanned
    formats must provide a local recognizer; there is no cloud fallback.
    """

    path = Path(source)
    suffix = path.suffix.lower()
    supported = {ext.lower() for ext in supported_extensions}
    if suffix not in supported:
        raise LocalDocumentOcrError("UNSUPPORTED_DOCUMENT_TYPE", "document type is not supported")
    if _looks_encrypted_or_corrupt(path):
        raise LocalDocumentOcrError("DOCUMENT_UNREADABLE", "document is corrupt or encrypted")
    if suffix == ".txt" and recognizer is None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise LocalDocumentOcrError("DOCUMENT_UNREADABLE", "text document is not valid utf-8") from exc
        return _validated_result(text, engine="local_text")
    if recognizer is None:
        raise LocalDocumentOcrError("LOCAL_OCR_UNAVAILABLE", "local OCR recognizer is required")
    try:
        result = recognizer(path)
    except LocalDocumentOcrError:
        raise
    except Exception as exc:
        raise LocalDocumentOcrError("LOCAL_OCR_FAILED", "local OCR recognizer failed") from exc
    if isinstance(result, LocalOcrResult):
        return _validated_result(result.text, engine=result.engine, page_count=result.page_count)
    return _validated_result(str(result), engine="local_injected")


def _validated_result(text: str, *, engine: str, page_count: int = 1) -> LocalOcrResult:
    if not text.strip():
        raise LocalDocumentOcrError("OCR_EMPTY_TEXT", "OCR produced no text")
    return LocalOcrResult(text=text, engine=engine, page_count=max(1, int(page_count)))


def _looks_encrypted_or_corrupt(path: Path) -> bool:
    try:
        head = path.read_bytes()[:2048]
    except OSError:
        return True
    lowered = head.lower()
    if b"%%eof" not in lowered and path.suffix.lower() == ".pdf":
        return True
    return b"/encrypt" in lowered or b"encrypted" in lowered or b"corrupt" in lowered

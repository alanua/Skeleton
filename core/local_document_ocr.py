from __future__ import annotations

import hashlib
import mimetypes
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

ALLOWED_EXTENSIONS: tuple[str, ...] = (
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
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
ZIP_TEXT_EXTENSIONS = {".docx", ".odt", ".xlsx", ".ods"}
MAX_EXTRACTED_CHARS = 24000


class LocalDocumentOCRError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LocalDocumentExtraction:
    schema: str
    source_sha256: str
    mime_type: str
    extension: str
    text: str
    page_count: int
    layout: tuple[dict[str, object], ...]
    extractor: str
    reason_codes: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "source_sha256": self.source_sha256,
            "mime_type": self.mime_type,
            "extension": self.extension,
            "text": self.text,
            "page_count": self.page_count,
            "layout": list(self.layout),
            "extractor": self.extractor,
            "reason_codes": list(self.reason_codes),
        }


def extract_local_document(path: str | Path) -> LocalDocumentExtraction:
    source = Path(path)
    extension = source.suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise LocalDocumentOCRError("extension_not_allowed", "document extension is not approved")
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    text, extractor, reasons = _extract_text(source, data, extension)
    text = _clean_text(text)[:MAX_EXTRACTED_CHARS]
    if not text:
        reasons.append("NO_LOCAL_TEXT_EXTRACTED")
    page_count = _page_count(data, extension)
    layout = (
        {
            "page": index,
            "kind": "local_text" if text else "local_binary",
            "char_count": len(text) if index == 1 else 0,
        }
        for index in range(1, page_count + 1)
    )
    return LocalDocumentExtraction(
        schema="skeleton.local_document_extraction.v1",
        source_sha256=digest,
        mime_type=mime_type,
        extension=extension,
        text=text,
        page_count=page_count,
        layout=tuple(layout),
        extractor=extractor,
        reason_codes=tuple(reasons),
    )


def _extract_text(source: Path, data: bytes, extension: str) -> tuple[str, str, list[str]]:
    if extension in {".txt", ".doc", ".xls"}:
        return (_decode_text(data), "plain-text-allowlist", [])
    if extension == ".rtf":
        return (_rtf_to_text(_decode_text(data)), "rtf-local-stripper", [])
    if extension in ZIP_TEXT_EXTENSIONS:
        return (_zip_xml_text(source), "zip-xml-local-extractor", [])
    if extension == ".pdf":
        return (_pdf_text(data), "pdf-local-byte-extractor", [])
    if extension in IMAGE_EXTENSIONS:
        return ("", "image-local-layout-only", ["IMAGE_OCR_ENGINE_NOT_CONFIGURED"])
    raise LocalDocumentOCRError("extension_not_allowed", "document extension is not approved")


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _rtf_to_text(value: str) -> str:
    value = re.sub(r"\\'[0-9a-fA-F]{2}", " ", value)
    value = re.sub(r"\\[a-zA-Z]+\d* ?", " ", value)
    value = value.replace("{", " ").replace("}", " ")
    return value


def _zip_xml_text(source: Path) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(source) as archive:
        for name in sorted(archive.namelist()):
            if not name.lower().endswith(".xml"):
                continue
            if not _approved_xml_member(name):
                continue
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            for element in root.iter():
                if element.text:
                    chunks.append(element.text)
    return " ".join(chunks)


def _approved_xml_member(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(("word/", "xl/", "content.xml", "styles.xml"))


def _pdf_text(data: bytes) -> str:
    decoded = _decode_text(data)
    matches = re.findall(r"\(([^()]{1,400})\)\s*T[Jj]", decoded)
    if matches:
        return " ".join(matches)
    printable = "".join(character if character.isprintable() else " " for character in decoded)
    return printable if len(printable.split()) >= 4 else ""


def _page_count(data: bytes, extension: str) -> int:
    if extension == ".pdf":
        return max(1, len(re.findall(rb"/Type\s*/Page\b", data)))
    return 1


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())

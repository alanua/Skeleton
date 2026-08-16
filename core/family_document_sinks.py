from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA
from core.private_memory_history import canonical_json, content_hash, safe_token


FAMILY_DOCUMENT_RECORD_SCHEMA = "skeleton.family_document_record.v1"
FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA = "skeleton.family_document_archive_receipt.v1"
_ALLOWED_SOURCE_SUFFIXES = {
    ".txt",
    ".pdf",
    ".tif",
    ".tiff",
    ".png",
    ".jpg",
    ".jpeg",
    ".doc",
    ".docx",
    ".odt",
    ".rtf",
    ".xls",
    ".xlsx",
    ".ods",
}


class FamilyDocumentArchiveError(RuntimeError):
    """Raised when the archive or MemoryGateway commit fails."""


class FileFamilyDocumentArchive:
    def __init__(self, root: Path) -> None:
        self.root = root

    def archive(
        self,
        record: Mapping[str, Any],
        *,
        source_path: Path | None = None,
        private_context: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        del private_context
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record_id = safe_token(str(record.get("record_id", "")), "record_id")
        original_sha256: str | None = None
        archive_label: str | None = None
        if source_path is not None:
            original_sha256, archive_label = self._archive_original(record_id, record, source_path)

        records = self.root / "records"
        records.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = records / f"{record_id}.json"
        payload = canonical_json(record)
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            state = "DUPLICATE_IDENTICAL"
        elif path.exists():
            raise FamilyDocumentArchiveError("record id reused with different payload")
        else:
            temporary = path.with_name(path.name + ".part")
            temporary.write_text(payload, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
            state = "NEW_ARCHIVE"
        if path.read_text(encoding="utf-8") != payload:
            raise FamilyDocumentArchiveError("record archive readback failed")
        return {
            "schema": FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA,
            "status": "DONE",
            "archive_state": state,
            "record_id": record_id,
            "record_hash": content_hash(record),
            "original_sha256": original_sha256,
            "archive_label": archive_label,
        }

    def _archive_original(
        self,
        record_id: str,
        record: Mapping[str, Any],
        source_path: Path,
    ) -> tuple[str, str]:
        source = Path(source_path)
        try:
            source_stat = source.stat()
        except OSError as exc:
            raise FamilyDocumentArchiveError("source archive unavailable") from exc
        if source.is_symlink() or not source.is_file() or source_stat.st_size <= 0:
            raise FamilyDocumentArchiveError("source archive invalid")
        suffix = source.suffix.casefold()
        if suffix not in _ALLOWED_SOURCE_SUFFIXES:
            raise FamilyDocumentArchiveError("source archive suffix invalid")
        expected = record.get("source_sha256")
        actual = _sha256_file(source)
        if isinstance(expected, str) and expected and actual != expected:
            raise FamilyDocumentArchiveError("source archive hash mismatch")

        relative = _archive_relative_path(record, record_id=record_id, suffix=suffix)
        target = self.root / "originals" / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            if _sha256_file(target) != actual:
                raise FamilyDocumentArchiveError("original archive conflict")
            return actual, str(Path("originals") / relative)

        temporary = target.with_name(target.name + ".part")
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        temporary.chmod(0o600)
        if _sha256_file(temporary) != actual:
            temporary.unlink(missing_ok=True)
            raise FamilyDocumentArchiveError("original archive readback failed")
        temporary.replace(target)
        if _sha256_file(target) != actual:
            raise FamilyDocumentArchiveError("original archive promotion failed")
        return actual, str(Path("originals") / relative)


class MemoryGatewayFamilyDocumentArchive:
    def __init__(self, gateway: MemoryGateway) -> None:
        self.gateway = gateway

    def archive(
        self,
        record: Mapping[str, Any],
        *,
        source_path: Path | None = None,
        private_context: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        del source_path
        record_id = safe_token(str(record.get("record_id", "")), "record_id")
        canonical_value = dict(record)
        if private_context:
            canonical_value["private_context"] = dict(private_context)
        mutation = {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "family_documents",
            "fact_namespace": "family_document",
            "fact_id": record_id,
            "value": canonical_value,
            "source_hash": content_hash(canonical_value),
            "actor_ref": "family-document-intake",
            "reason_code": "family-document-intake",
            "approval_ref": "local-private-intake",
            "idempotency_key": f"family-document-{record_id}",
        }
        response = self.gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "skeleton",
                "command": "skeleton.memory.private_mutate",
                "payload": mutation,
            }
        )["payload"]
        readback = self.gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "skeleton",
                "command": "skeleton.memory.private_read_exact",
                "payload": {
                    "project_id": "skeleton",
                    "dataset_id": "family_documents",
                    "canonical_ref": f"family_document:{record_id}",
                },
            }
        )["payload"]
        value = readback.get("value") if isinstance(readback, Mapping) else None
        if (
            not isinstance(readback, Mapping)
            or readback.get("authoritative") is not True
            or not isinstance(value, Mapping)
            or value.get("record_id") != record_id
            or value.get("record_hash") != record.get("record_hash")
            or value.get("source_sha256") != record.get("source_sha256")
        ):
            raise FamilyDocumentArchiveError("MemoryGateway exact readback mismatch")
        if private_context:
            stored_context = value.get("private_context")
            if not isinstance(stored_context, Mapping) or content_hash(stored_context) != content_hash(private_context):
                raise FamilyDocumentArchiveError("MemoryGateway private context readback mismatch")
        return {
            "schema": FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA,
            "status": "DONE",
            "archive_state": "CANONICAL_COMMITTED",
            "record_id": record_id,
            "record_hash": content_hash(record),
            "canonical_ref": f"family_document:{record_id}",
            "canonical_revision": readback.get("canonical_revision"),
            "mutation_status": response.get("status") if isinstance(response, Mapping) else None,
            "authoritative": True,
        }


class CompositeFamilyDocumentArchive:
    """Archive the immutable source first, then commit and read back MemoryGateway."""

    def __init__(
        self,
        file_archive: FileFamilyDocumentArchive,
        memory_archive: MemoryGatewayFamilyDocumentArchive,
    ) -> None:
        self.file_archive = file_archive
        self.memory_archive = memory_archive

    def archive(
        self,
        record: Mapping[str, Any],
        *,
        source_path: Path | None = None,
        private_context: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        file_receipt = self.file_archive.archive(record, source_path=source_path)
        source_hash = record.get("source_sha256")
        if source_path is not None and isinstance(source_hash, str):
            if file_receipt.get("original_sha256") != source_hash:
                raise FamilyDocumentArchiveError("archive verification must precede MemoryGateway")
        canonical_record = dict(record)
        archive_label = file_receipt.get("archive_label")
        if isinstance(archive_label, str) and archive_label:
            canonical_record["archive"] = {
                "storage_label": archive_label,
                "source_sha256": file_receipt.get("original_sha256"),
                "readback_verified": True,
            }
        memory_receipt = self.memory_archive.archive(
            canonical_record,
            private_context=private_context,
        )
        return {
            **file_receipt,
            "status": "DONE",
            "canonical_ref": memory_receipt.get("canonical_ref"),
            "canonical_revision": memory_receipt.get("canonical_revision"),
            "authoritative": memory_receipt.get("authoritative") is True,
        }


def _archive_relative_path(record: Mapping[str, Any], *, record_id: str, suffix: str) -> Path:
    classification = record.get("classification")
    if not isinstance(classification, Mapping):
        classification = {}
    route = str(classification.get("route") or "REVIEW").upper()
    owner = _segment(classification.get("principal_subject_alias"), "99_review")
    topic = _segment(classification.get("topic_alias"), "99_review")
    jurisdiction = _segment(classification.get("jurisdiction_country"), "unknown")
    raw_date = classification.get("document_date")
    year = str(raw_date)[:4] if isinstance(raw_date, str) and re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", raw_date) else "unknown"
    if route != "ACCEPT":
        owner = "99_review"
    document_type = _segment(classification.get("document_type"), "document")
    issuer = _segment(classification.get("issuer"), "unknown_issuer")
    date_label = _segment(raw_date, year)
    filename = f"{date_label}_{document_type}_{issuer}_{record_id[-10:]}{suffix}"
    return Path(owner, topic, jurisdiction, year, filename)


def _segment(value: object, default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    normalized = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE).strip("._-")
    if not normalized or normalized in {".", ".."}:
        return default
    return normalized[:80]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

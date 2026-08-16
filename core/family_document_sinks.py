from __future__ import annotations

import hashlib
import os
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
    ) -> dict[str, object]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record_id = safe_token(str(record.get("record_id", "")), "record_id")
        original_sha256: str | None = None
        if source_path is not None:
            original_sha256 = self._archive_original(record_id, record, source_path)

        path = self.root / f"{record_id}.json"
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
            "archive_label": f"family_documents/{record_id}",
        }

    def _archive_original(
        self,
        record_id: str,
        record: Mapping[str, Any],
        source_path: Path,
    ) -> str:
        source = Path(source_path)
        try:
            source_stat = source.stat()
        except OSError as exc:
            raise FamilyDocumentArchiveError("source archive unavailable") from exc
        if not source.is_file() or source_stat.st_size <= 0:
            raise FamilyDocumentArchiveError("source archive invalid")
        suffix = source.suffix.casefold()
        if suffix not in _ALLOWED_SOURCE_SUFFIXES:
            raise FamilyDocumentArchiveError("source archive suffix invalid")
        expected = record.get("source_sha256")
        actual = _sha256_file(source)
        if isinstance(expected, str) and expected and actual != expected:
            raise FamilyDocumentArchiveError("source archive hash mismatch")

        originals = self.root / "originals"
        originals.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = originals / f"{record_id}{suffix}"
        if target.exists():
            if _sha256_file(target) != actual:
                raise FamilyDocumentArchiveError("original archive conflict")
            return actual

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
        return actual


class MemoryGatewayFamilyDocumentArchive:
    def __init__(self, gateway: MemoryGateway) -> None:
        self.gateway = gateway

    def archive(
        self,
        record: Mapping[str, Any],
        *,
        source_path: Path | None = None,
    ) -> dict[str, object]:
        del source_path
        record_id = safe_token(str(record.get("record_id", "")), "record_id")
        mutation = {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "family_documents",
            "fact_namespace": "family_document",
            "fact_id": record_id,
            "value": dict(record),
            "source_hash": content_hash(record),
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
        ):
            raise FamilyDocumentArchiveError("MemoryGateway exact readback mismatch")
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
    ) -> dict[str, object]:
        file_receipt = self.file_archive.archive(record, source_path=source_path)
        source_hash = record.get("source_sha256")
        if source_path is not None and isinstance(source_hash, str):
            if file_receipt.get("original_sha256") != source_hash:
                raise FamilyDocumentArchiveError("archive verification must precede MemoryGateway")
        memory_receipt = self.memory_archive.archive(record)
        return {
            **file_receipt,
            "status": "DONE",
            "canonical_ref": memory_receipt.get("canonical_ref"),
            "canonical_revision": memory_receipt.get("canonical_revision"),
            "authoritative": memory_receipt.get("authoritative") is True,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

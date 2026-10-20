from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA
from core.private_memory_history import canonical_json, content_hash, safe_token


FAMILY_DOCUMENT_RECORD_SCHEMA = "skeleton.family_document_record.v1"
FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA = "skeleton.family_document_archive_receipt.v1"


class FamilyDocumentArchiveError(RuntimeError):
    """Raised when the archive or MemoryGateway commit fails."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileFamilyDocumentArchive:
    """Compatibility record archive used by tests and non-production callers."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def archive(self, record: Mapping[str, Any], *, source_path: Path | None = None) -> dict[str, object]:
        del source_path
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record_id = safe_token(str(record.get("record_id", "")), "record_id")
        path = self.root / f"{record_id}.json"
        payload = canonical_json(record)
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            state = "DUPLICATE_IDENTICAL"
        elif path.exists():
            raise FamilyDocumentArchiveError("record id reused with different payload")
        else:
            path.write_text(payload, encoding="utf-8")
            path.chmod(0o600)
            state = "NEW_ARCHIVE"
        return {
            "schema": FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA,
            "status": "DONE",
            "archive_state": state,
            "record_id": record_id,
            "record_hash": content_hash(record),
            "storage_label": "Сімейний архів",
        }


class MemoryGatewayFamilyDocumentArchive:
    """Canonical record sink retained for callers that already own binary durability."""

    def __init__(self, gateway: MemoryGateway) -> None:
        self.gateway = gateway

    def archive(self, record: Mapping[str, Any], *, source_path: Path | None = None) -> dict[str, object]:
        del source_path
        return _commit_and_readback(self.gateway, record)


class VerifiedMemoryGatewayFamilyDocumentArchive:
    """Production sink: immutable original readback precedes canonical mutation."""

    _ALLOWED_SUFFIXES = {
        ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt",
        ".doc", ".docx", ".odt", ".rtf",
    }

    def __init__(self, original_root: Path, gateway: MemoryGateway) -> None:
        self.original_root = original_root.expanduser().resolve()
        self.gateway = gateway

    def archive(self, record: Mapping[str, Any], *, source_path: Path | None = None) -> dict[str, object]:
        if source_path is None:
            raise FamilyDocumentArchiveError("source path required for verified archive")
        source = source_path.expanduser().resolve(strict=True)
        expected_hash = str(record.get("source_sha256", ""))
        if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
            raise FamilyDocumentArchiveError("source hash invalid")
        if _sha256_file(source) != expected_hash:
            raise FamilyDocumentArchiveError("source changed before archive")
        record_id = safe_token(str(record.get("record_id", "")), "record_id")
        suffix = source.suffix.lower()
        if suffix not in self._ALLOWED_SUFFIXES:
            raise FamilyDocumentArchiveError("source type not approved for archive")

        self.original_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.original_root.chmod(0o700)
        target = self.original_root / f"{record_id}{suffix}"
        archive_state = self._archive_original(source, target, expected_hash)

        canonical = _commit_and_readback(self.gateway, record)
        return {
            **canonical,
            "archive_state": archive_state,
            "original_sha256": expected_hash,
            "original_readback_verified": True,
            "storage_label": "Сімейний архів",
        }

    def _archive_original(self, source: Path, target: Path, expected_hash: str) -> str:
        if target.exists():
            if not target.is_file() or target.is_symlink() or _sha256_file(target) != expected_hash:
                raise FamilyDocumentArchiveError("existing original archive conflicts")
            return "DUPLICATE_IDENTICAL"

        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=self.original_root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as destination, source.open("rb") as origin:
                shutil.copyfileobj(origin, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            temporary.chmod(0o600)
            if _sha256_file(source) != expected_hash or _sha256_file(temporary) != expected_hash:
                raise FamilyDocumentArchiveError("original archive readback mismatch")
            os.replace(temporary, target)
            target.chmod(0o600)
            if _sha256_file(target) != expected_hash:
                raise FamilyDocumentArchiveError("original archive final readback mismatch")
            return "NEW_ARCHIVE"
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _commit_and_readback(gateway: MemoryGateway, record: Mapping[str, Any]) -> dict[str, object]:
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
    response = gateway.execute(
        {
            "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
            "namespace": "skeleton",
            "command": "skeleton.memory.private_mutate",
            "payload": mutation,
        }
    )
    mutation_payload = response.get("payload")
    if not isinstance(mutation_payload, Mapping):
        raise FamilyDocumentArchiveError("MemoryGateway mutation receipt missing")

    canonical_ref = str(mutation_payload.get("canonical_ref") or f"family_document:{record_id}")
    read_response = gateway.execute(
        {
            "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
            "namespace": "skeleton",
            "command": "skeleton.memory.private_read_exact",
            "payload": {
                "project_id": "skeleton",
                "dataset_id": "family_documents",
                "canonical_ref": canonical_ref,
            },
        }
    )
    read_payload = read_response.get("payload")
    if not isinstance(read_payload, Mapping) or read_payload.get("authoritative") is not True:
        raise FamilyDocumentArchiveError("MemoryGateway exact readback not authoritative")
    if read_payload.get("canonical_ref") != canonical_ref:
        raise FamilyDocumentArchiveError("MemoryGateway exact readback ref mismatch")
    value = read_payload.get("value")
    if not isinstance(value, Mapping):
        raise FamilyDocumentArchiveError("MemoryGateway exact readback value missing")
    if value.get("record_id") != record_id or value.get("source_sha256") != record.get("source_sha256"):
        raise FamilyDocumentArchiveError("MemoryGateway exact readback mismatch")
    if content_hash(value) != content_hash(record):
        raise FamilyDocumentArchiveError("MemoryGateway exact readback hash mismatch")
    if read_payload.get("value_hash") != content_hash(record):
        raise FamilyDocumentArchiveError("MemoryGateway exact readback value hash mismatch")
    return {
        "schema": FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA,
        "status": "DONE",
        "record_id": record_id,
        "record_hash": content_hash(record),
        "canonical_ref": canonical_ref,
        "canonical_revision": mutation_payload.get("canonical_revision"),
        "canonical_readback_verified": True,
        "storage_label": "Сімейний архів",
    }

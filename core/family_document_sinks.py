from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA


class FamilyDocumentSinkError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ArchiveWriteReceipt:
    path: Path
    sha256: str
    bytes_written: int


class VerifiedArchive:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def write_record_once(self, name: str, record: Mapping[str, Any]) -> ArchiveWriteReceipt:
        if "/" in name or "\\" in name or name.startswith("."):
            raise FamilyDocumentSinkError("archive_name_invalid", "archive name is invalid")
        path = self.root / name
        encoded = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise FamilyDocumentSinkError("archive_collision", "archive path already has different content")
            return ArchiveWriteReceipt(path=path, sha256=digest, bytes_written=len(existing))
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        readback = path.read_bytes()
        if readback != encoded or hashlib.sha256(readback).hexdigest() != digest:
            raise FamilyDocumentSinkError("archive_readback_failed", "archive readback verification failed")
        return ArchiveWriteReceipt(path=path, sha256=digest, bytes_written=len(encoded))


class DurableProjectionOutbox:
    """Canonical-first durable projection work queue backed by JSONL."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "family_document_projection_outbox.jsonl"

    def enqueue(self, event: Mapping[str, Any]) -> dict[str, object]:
        encoded = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        work_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        row = {"schema": "skeleton.family_document_projection_outbox.v1", "state": "QUEUED", "work_key": work_key, **dict(event)}
        line = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        if self.path.exists() and work_key in self.path.read_text(encoding="utf-8"):
            return {"status": "DUPLICATE", "work_key": work_key}
        with self.path.open("a", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return {"status": "QUEUED", "work_key": work_key}


def private_put_request(
    *,
    fact_namespace: str,
    fact_id: str,
    value: Mapping[str, Any],
    source_hash: str,
    idempotency_key: str,
    approval_ref: str,
) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": "skeleton.memory.private_mutate",
        "payload": {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "family_documents",
            "fact_namespace": fact_namespace,
            "fact_id": fact_id,
            "value": dict(value),
            "source_hash": source_hash,
            "idempotency_key": idempotency_key,
            "actor_ref": "family-document-intake",
            "reason_code": "family-document-intake",
            "approval_ref": approval_ref,
        },
    }


def aggregate_receipt(*, status: str, duplicate: bool, event_count: int) -> dict[str, object]:
    return {
        "schema": "skeleton.family_document_receipt.v1",
        "privacy": "aggregate_only",
        "status": status,
        "aggregate_counts": {
            "documents_seen": 1,
            "duplicates": 1 if duplicate else 0,
            "event_candidates": event_count,
        },
    }

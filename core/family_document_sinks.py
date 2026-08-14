from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA
from core.private_memory_history import canonical_json, content_hash, safe_token


FAMILY_DOCUMENT_RECORD_SCHEMA = "skeleton.family_document_record.v1"
FAMILY_DOCUMENT_ARCHIVE_RECEIPT_SCHEMA = "skeleton.family_document_archive_receipt.v1"


class FamilyDocumentArchiveError(RuntimeError):
    """Raised when the archive or MemoryGateway commit fails."""


class FileFamilyDocumentArchive:
    def __init__(self, root: Path) -> None:
        self.root = root

    def archive(self, record: Mapping[str, Any]) -> dict[str, object]:
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
        }


class MemoryGatewayFamilyDocumentArchive:
    def __init__(self, gateway: MemoryGateway) -> None:
        self.gateway = gateway

    def archive(self, record: Mapping[str, Any]) -> dict[str, object]:
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
        return self.gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "skeleton",
                "command": "skeleton.memory.private_mutate",
                "payload": mutation,
            }
        )["payload"]

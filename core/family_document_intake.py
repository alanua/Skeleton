from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.family_document_sinks import FAMILY_DOCUMENT_RECORD_SCHEMA
from core.local_document_ocr import read_local_document
from core.private_memory_history import content_hash


FAMILY_DOCUMENT_INTAKE_REQUEST_SCHEMA = "skeleton.family_document_intake_request.v1"
FAMILY_DOCUMENT_EVENT_SCHEMA = "skeleton.family_document_event.v1"


@dataclass(frozen=True)
class FamilyDocumentIntakeRequest:
    source_id: str
    source_sha256: str
    ocr_text: str
    source_kind: str = "mfp"
    page_count: int = 1
    mime_type: str = "text/plain"


def build_intake_request(path: Path, *, source_id: str, source_sha256: str) -> FamilyDocumentIntakeRequest:
    result = read_local_document(path)
    if result.source_sha256 != source_sha256:
        raise ValueError("source changed after stable-file gate")
    return FamilyDocumentIntakeRequest(
        source_id=source_id,
        source_sha256=source_sha256,
        ocr_text=result.text,
        page_count=result.page_count,
        mime_type=result.mime_type,
    )


def build_family_document_record(
    request: FamilyDocumentIntakeRequest,
    classification: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    classification_payload = dict(classification or {})
    record_id = _record_id(request.source_sha256)
    record = {
        "schema": FAMILY_DOCUMENT_RECORD_SCHEMA,
        "record_id": record_id,
        "source_kind": request.source_kind,
        "source_id": request.source_id,
        "source_sha256": request.source_sha256,
        "ocr_text_hash": content_hash({"ocr_text": request.ocr_text}),
        "page_count": request.page_count,
        "mime_type": request.mime_type,
        "classification": _public_classification(classification_payload),
    }
    record["record_hash"] = content_hash(record)
    return record


def _record_id(source_sha256: str) -> str:
    digest = hashlib.sha256(source_sha256.encode("ascii")).hexdigest()
    return f"doc-{digest[:32]}"


def _public_classification(value: Mapping[str, Any]) -> dict[str, object]:
    allowed = {
        "route",
        "principal_subject_alias",
        "linked_subject_aliases",
        "topic_alias",
        "jurisdiction_country",
        "document_date",
        "date_precision",
        "document_type",
        "issuer",
        "summary",
        "confidence",
        "event_candidates",
        "reason_codes",
    }
    return {key: value[key] for key in sorted(allowed) if key in value}

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


RECONCILIATION_SCHEMA = "skeleton.family_document_reconciliation.v1"
PUBLIC_RECEIPT_SCHEMA = "skeleton.family_document_reconciliation_public_receipt.v1"
_ALLOWED_DISPOSITIONS = frozenset({"IMPORT", "DUPLICATE", "VERSION", "REVIEW", "QUARANTINE"})


class FamilyDocumentReconciliationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReconciliationEntry:
    source_id: str
    source_sha256: str
    disposition: str
    reason_code: str
    planned_record_id: str | None = None
    planned_storage_ref: str | None = None
    owner_alias: str | None = None
    topic_alias: str | None = None
    jurisdiction_country: str | None = None
    document_year: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReconciliationEntry":
        allowed = {
            "source_id", "source_sha256", "disposition", "reason_code",
            "planned_record_id", "planned_storage_ref", "owner_alias", "topic_alias",
            "jurisdiction_country", "document_year",
        }
        if set(value) - allowed:
            raise FamilyDocumentReconciliationError("unknown reconciliation field")
        source_id = _bounded(value.get("source_id"), "source_id", 256)
        sha = str(value.get("source_sha256") or "")
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise FamilyDocumentReconciliationError("source sha invalid")
        disposition = str(value.get("disposition") or "")
        if disposition not in _ALLOWED_DISPOSITIONS:
            raise FamilyDocumentReconciliationError("disposition invalid")
        reason = _token(value.get("reason_code"), "reason_code")
        year = value.get("document_year")
        if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200):
            raise FamilyDocumentReconciliationError("document year invalid")
        return cls(
            source_id=source_id,
            source_sha256=sha,
            disposition=disposition,
            reason_code=reason,
            planned_record_id=_optional_bounded(value.get("planned_record_id"), "planned_record_id", 256),
            planned_storage_ref=_optional_bounded(value.get("planned_storage_ref"), "planned_storage_ref", 2048),
            owner_alias=_optional_bounded(value.get("owner_alias"), "owner_alias", 256),
            topic_alias=_optional_bounded(value.get("topic_alias"), "topic_alias", 256),
            jurisdiction_country=_optional_bounded(value.get("jurisdiction_country"), "jurisdiction_country", 64),
            document_year=year,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "disposition": self.disposition,
            "reason_code": self.reason_code,
            "planned_record_id": self.planned_record_id,
            "planned_storage_ref": self.planned_storage_ref,
            "owner_alias": self.owner_alias,
            "topic_alias": self.topic_alias,
            "jurisdiction_country": self.jurisdiction_country,
            "document_year": self.document_year,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationPacket:
    packet_hash: str
    private_packet: Mapping[str, Any]
    public_receipt: Mapping[str, Any]


def build_reconciliation_packet(entries: Sequence[Mapping[str, Any] | ReconciliationEntry]) -> ReconciliationPacket:
    normalized: list[ReconciliationEntry] = []
    for item in entries:
        normalized.append(item if isinstance(item, ReconciliationEntry) else ReconciliationEntry.from_mapping(item))
    normalized.sort(key=lambda item: (item.source_sha256, item.source_id, item.disposition, item.reason_code))
    seen: set[tuple[str, str]] = set()
    for item in normalized:
        identity = (item.source_id, item.source_sha256)
        if identity in seen:
            raise FamilyDocumentReconciliationError("duplicate inventory identity")
        seen.add(identity)
    private_packet = {
        "schema": RECONCILIATION_SCHEMA,
        "mode": "READ_ONLY_PLAN",
        "entries": [item.to_mapping() for item in normalized],
    }
    packet_hash = hashlib.sha256(_canonical(private_packet)).hexdigest()
    dispositions = Counter(item.disposition for item in normalized)
    reasons = Counter(item.reason_code for item in normalized)
    public_receipt = {
        "schema": PUBLIC_RECEIPT_SCHEMA,
        "packet_hash": packet_hash,
        "total_count": len(normalized),
        "import_count": dispositions["IMPORT"],
        "duplicate_count": dispositions["DUPLICATE"],
        "version_count": dispositions["VERSION"],
        "review_count": dispositions["REVIEW"],
        "quarantine_count": dispositions["QUARANTINE"],
        "reason_counts": {key: reasons[key] for key in sorted(reasons)},
        "side_effects": 0,
        "approval_ready": True,
    }
    return ReconciliationPacket(packet_hash, private_packet, public_receipt)


def verify_packet_hash(private_packet: Mapping[str, Any], expected_hash: str) -> bool:
    if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
        return False
    return hashlib.sha256(_canonical(private_packet)).hexdigest() == expected_hash


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise FamilyDocumentReconciliationError(f"{field} invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > limit:
        raise FamilyDocumentReconciliationError(f"{field} invalid")
    return normalized


def _optional_bounded(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    return _bounded(value, field, limit)


def _token(value: Any, field: str) -> str:
    text = _bounded(value, field, 96)
    if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-" for ch in text):
        raise FamilyDocumentReconciliationError(f"{field} invalid")
    return text

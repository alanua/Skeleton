from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA


NOTIFICATION_RECORD_SCHEMA = "skeleton.family_document.notification_record.v1"
NOTIFICATION_RECEIPT_SCHEMA = "skeleton.family_document.notification_receipt.v1"
NOTIFICATION_DATASET_ID = "family_document_notifications"
NOTIFICATION_NAMESPACE = "skeleton.family_document_notifications"
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TERMINAL_RECEIPTS = frozenset({"DONE", "REVIEW", "RETRY", "FAILED", "QUARANTINED"})


class FamilyDocumentNotificationError(RuntimeError):
    """Raised when a notification record cannot be enqueued."""


class FamilyDocumentNotificationSink(Protocol):
    def enqueue(self, record: Mapping[str, object]) -> dict[str, object]: ...


@dataclass(frozen=True)
class SecretReference:
    """Opaque runtime secret pointer; never stores the Telegram token or chat id."""

    provider: str
    name: str

    def to_dict(self) -> dict[str, str]:
        provider = _safe_token(self.provider, "provider")
        name = _safe_token(self.name, "name")
        return {"provider": provider, "name": name}


@dataclass(frozen=True)
class TelegramActivation:
    bot_token: SecretReference
    chat_id: SecretReference

    def to_public_config(self) -> dict[str, object]:
        return {
            "transport": "telegram",
            "bot_token": self.bot_token.to_dict(),
            "chat_id": self.chat_id.to_dict(),
        }


class MemoryGatewayNotificationSink:
    """Stores notification work through the private canonical MemoryGateway path."""

    def __init__(
        self,
        gateway: MemoryGateway,
        *,
        project_id: str = "skeleton",
        dataset_id: str = NOTIFICATION_DATASET_ID,
    ) -> None:
        self._gateway = gateway
        self._project_id = _safe_token(project_id, "project_id")
        self._dataset_id = _safe_token(dataset_id, "dataset_id")

    def enqueue(self, record: Mapping[str, object]) -> dict[str, object]:
        normalized = normalize_notification_record(record)
        notification_id = str(normalized["notification_id"])
        mutation = {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": self._project_id,
            "dataset_id": self._dataset_id,
            "fact_namespace": NOTIFICATION_NAMESPACE,
            "fact_id": notification_id,
            "value": normalized,
            "actor_ref": "family_document_runtime",
            "reason_code": "family-document-notification",
            "approval_ref": "mfp-private-runtime",
            "idempotency_key": notification_id,
        }
        return self._gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "skeleton",
                "command": "skeleton.memory.private_mutate",
                "payload": mutation,
            }
        )


class RecordingNotificationSink:
    """Test/local sink with production-equivalent idempotency semantics."""

    def __init__(self, *, fail_after_record: bool = False) -> None:
        self.records: list[dict[str, object]] = []
        self._ids: set[str] = set()
        self.fail_after_record = fail_after_record

    def enqueue(self, record: Mapping[str, object]) -> dict[str, object]:
        normalized = normalize_notification_record(record)
        classification = "DUPLICATE_IDENTICAL"
        if normalized["notification_id"] not in self._ids:
            self._ids.add(str(normalized["notification_id"]))
            self.records.append(normalized)
            classification = "NEW_MUTATION"
        if self.fail_after_record:
            raise FamilyDocumentNotificationError("telegram_delivery_retryable")
        return {
            "schema": NOTIFICATION_RECEIPT_SCHEMA,
            "status": "QUEUED",
            "idempotency_classification": classification,
            "notification_id": normalized["notification_id"],
        }


def build_intake_notification_record(
    *,
    canonical_document_id: str,
    stable_scan_id: str,
) -> dict[str, object]:
    document_ref = _safe_token(canonical_document_id, "canonical_document_id")
    scan_ref = _safe_token(stable_scan_id, "stable_scan_id")
    notification_id = notification_id_for(
        phase="intake",
        canonical_document_id=document_ref,
        canonical_task_id=None,
        receipt_type="RECEIVED",
    )
    return {
        "schema": NOTIFICATION_RECORD_SCHEMA,
        "notification_id": notification_id,
        "phase": "intake",
        "receipt_type": "RECEIVED",
        "delivery": "telegram_private",
        "canonical_document_ref_hash": _public_hash(document_ref),
        "canonical_task_ref_hash": None,
        "stable_scan_ref_hash": _public_hash(scan_ref),
        "public_message": "MFP scan accepted for private document processing.",
        "retryable": True,
    }


def build_terminal_notification_record(
    *,
    canonical_document_id: str,
    canonical_task_id: str,
    terminal_state: str,
) -> dict[str, object]:
    receipt_type = terminal_receipt_type(terminal_state)
    document_ref = _safe_token(canonical_document_id, "canonical_document_id")
    task_ref = _safe_token(canonical_task_id, "canonical_task_id")
    notification_id = notification_id_for(
        phase="terminal",
        canonical_document_id=document_ref,
        canonical_task_id=task_ref,
        receipt_type=receipt_type,
    )
    return {
        "schema": NOTIFICATION_RECORD_SCHEMA,
        "notification_id": notification_id,
        "phase": "terminal",
        "receipt_type": receipt_type,
        "delivery": "telegram_private",
        "canonical_document_ref_hash": _public_hash(document_ref),
        "canonical_task_ref_hash": _public_hash(task_ref),
        "stable_scan_ref_hash": None,
        "public_message": f"Private document processing reached {receipt_type}.",
        "retryable": True,
    }


def terminal_receipt_type(terminal_state: str) -> str:
    state = str(terminal_state).upper()
    if state in _TERMINAL_RECEIPTS:
        return state
    if state in {"ACCEPTED", "COMPLETED", "SUCCESS"}:
        return "DONE"
    if state in {"AMBIGUOUS", "NEEDS_REVIEW"}:
        return "REVIEW"
    if state in {"RETRYABLE", "RETRY_PENDING"}:
        return "RETRY"
    if state in {"ERROR"}:
        return "FAILED"
    if state in {"QUARANTINE"}:
        return "QUARANTINED"
    raise FamilyDocumentNotificationError("terminal_state_not_notifiable")


def notification_id_for(
    *,
    phase: str,
    canonical_document_id: str,
    canonical_task_id: str | None,
    receipt_type: str,
) -> str:
    basis = {
        "phase": _safe_token(phase, "phase"),
        "document": _safe_token(canonical_document_id, "canonical_document_id"),
        "task": None if canonical_task_id is None else _safe_token(canonical_task_id, "canonical_task_id"),
        "receipt_type": _safe_token(receipt_type, "receipt_type"),
    }
    return "mfp-notify-" + hashlib.sha256(repr(sorted(basis.items())).encode("utf-8")).hexdigest()


def normalize_notification_record(record: Mapping[str, object]) -> dict[str, object]:
    if record.get("schema") != NOTIFICATION_RECORD_SCHEMA:
        raise FamilyDocumentNotificationError("notification_schema_invalid")
    normalized = dict(record)
    for key in ("notification_id", "phase", "receipt_type", "delivery", "public_message"):
        value = normalized.get(key)
        if not isinstance(value, str) or not value:
            raise FamilyDocumentNotificationError("notification_record_invalid")
    _safe_token(str(normalized["notification_id"]), "notification_id")
    if normalized["phase"] not in {"intake", "terminal"}:
        raise FamilyDocumentNotificationError("notification_phase_invalid")
    if normalized["receipt_type"] not in {"RECEIVED", *_TERMINAL_RECEIPTS}:
        raise FamilyDocumentNotificationError("notification_receipt_type_invalid")
    if normalized["delivery"] != "telegram_private":
        raise FamilyDocumentNotificationError("notification_delivery_invalid")
    if len(str(normalized["public_message"])) > 160:
        raise FamilyDocumentNotificationError("notification_message_too_long")
    for key in ("canonical_document_ref_hash", "canonical_task_ref_hash", "stable_scan_ref_hash"):
        value = normalized.get(key)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise FamilyDocumentNotificationError("notification_hash_invalid")
    normalized["retryable"] = bool(normalized.get("retryable", True))
    return normalized


def _safe_token(value: str, label: str) -> str:
    if _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise FamilyDocumentNotificationError(f"{label}_invalid")
    return value


def _public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

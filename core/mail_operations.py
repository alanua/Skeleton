from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Final

from core.scheduler_models import ScheduleSpec


MAIL_BATCH_RECEIPT_SCHEMA: Final = "skeleton.mail_operations.batch_receipt.v1"
MAIL_CASE_SCHEMA: Final = "skeleton.mail_operations.case.v1"
MAIL_INDEX_ENTRY_SCHEMA: Final = "skeleton.mail_operations.index_entry.v1"
MAIL_DEADLINE_SCHEMA: Final = "skeleton.mail_operations.deadline.v1"
MAIL_GITHUB_CI_EVENT_SCHEMA: Final = "skeleton.mail_operations.github_ci_event.v1"
MAIL_TELEGRAM_REPLY_DRAFT_SCHEMA: Final = "skeleton.mail_operations.telegram_reply_draft.v1"
MAIL_LOCAL_INFERENCE_REQUEST_SCHEMA: Final = "skeleton.mail_operations.local_inference_request.v1"
MAIL_FOLLOWUP_TASK_SCHEMA: Final = "skeleton.mail_operations.followup_task.v1"

PRIVACY_BOUNDARY: Final = "PRIVATE_EMAIL_CONTENT_LOCAL_ONLY"
LOCAL_MAIL_CLASSIFIER_REQUEST_TYPE: Final = "mail_operations.classify"
LOCAL_MAIL_CLASSIFIER_MODEL: Final = "local-first-mail-router"

RETENTION_CLASSES: Final = frozenset(
    {
        "none",
        "invoice_retention_7y",
        "invoice_retention_10y",
        "github_ci_public_status",
        "deadline_reference_only",
    }
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PRIVATE_KEYS = frozenset(
    {
        "body",
        "body_text",
        "html",
        "subject",
        "from",
        "to",
        "cc",
        "bcc",
        "reply_to",
        "recipient",
        "recipients",
        "sender",
        "attachment_name",
        "attachment_bytes",
    }
)


class MailOperationsError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MailEnvelope:
    """Local-only source envelope. Private fields are accepted only to hash, never emit."""

    provider: str
    provider_account_ref: str
    provider_message_ref: str
    received_at: int
    private_payload: Mapping[str, Any]
    public_signals: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailEnvelope":
        if not isinstance(value, Mapping):
            raise MailOperationsError("INVALID_ENVELOPE", "mail envelope must be an object")
        return cls(
            provider=_safe_token(value.get("provider"), "provider"),
            provider_account_ref=_safe_token(
                value.get("provider_account_ref"), "provider_account_ref"
            ),
            provider_message_ref=_safe_token(
                value.get("provider_message_ref"), "provider_message_ref"
            ),
            received_at=_non_negative_int(value.get("received_at"), "received_at"),
            private_payload=_mapping(value.get("private_payload"), "private_payload"),
            public_signals=_public_signals(value.get("public_signals")),
        )

    @property
    def idempotency_key(self) -> str:
        return _sha256_hex(
            "\n".join(
                (self.provider, self.provider_account_ref, self.provider_message_ref)
            )
        )

    @property
    def content_hash(self) -> str:
        encoded = json.dumps(
            self.private_payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return _sha256_hex(encoded)


class MailOperationsStore:
    """SQLite-backed read-only mail indexing projection with idempotent inserts."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mail_cases (
                    case_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    provider_account_ref TEXT NOT NULL,
                    provider_message_ref TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    retention_class TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    public_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mail_index_entries (
                    index_entry_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    index_kind TEXT NOT NULL,
                    index_value TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    public_json TEXT NOT NULL,
                    UNIQUE(case_id, index_kind, index_value),
                    FOREIGN KEY(case_id) REFERENCES mail_cases(case_id)
                );

                CREATE TABLE IF NOT EXISTS mail_deadlines (
                    deadline_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    due_at INTEGER NOT NULL CHECK(due_at >= 0),
                    schedule_id TEXT NOT NULL UNIQUE,
                    route_id TEXT NOT NULL,
                    public_json TEXT NOT NULL,
                    UNIQUE(case_id, due_at),
                    FOREIGN KEY(case_id) REFERENCES mail_cases(case_id)
                );
                """
            )

    def ingest_batch(
        self, envelopes: Iterable[MailEnvelope | Mapping[str, Any]], *, now: int
    ) -> dict[str, Any]:
        _non_negative_int(now, "now")
        self.initialize()
        counters: Counter[str] = Counter()
        case_ids: list[str] = []
        index_entry_ids: list[str] = []
        deadline_ids: list[str] = []
        followups: list[dict[str, Any]] = []

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for raw in envelopes:
                    envelope = raw if isinstance(raw, MailEnvelope) else MailEnvelope.from_mapping(raw)
                    case, created_case = _case_for(envelope, now=now)
                    _assert_public_safe(case)
                    case_ids.append(str(case["case_id"]))
                    inserted = connection.execute(
                        """
                        INSERT OR IGNORE INTO mail_cases(
                            case_id, idempotency_key, provider, provider_account_ref,
                            provider_message_ref, content_hash, classification,
                            retention_class, created_at, public_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            case["case_id"],
                            case["idempotency_key"],
                            envelope.provider,
                            envelope.provider_account_ref,
                            envelope.provider_message_ref,
                            case["content_hash"],
                            case["classification"],
                            case["retention_class"],
                            now,
                            _public_json(case),
                        ),
                    ).rowcount == 1
                    counters["created_cases" if inserted else "replayed_cases"] += 1

                    for entry in _index_entries_for(case, envelope, now=now):
                        _assert_public_safe(entry)
                        created = connection.execute(
                            """
                            INSERT OR IGNORE INTO mail_index_entries(
                                index_entry_id, case_id, index_kind, index_value,
                                created_at, public_json
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                entry["index_entry_id"],
                                entry["case_id"],
                                entry["index_kind"],
                                entry["index_value"],
                                now,
                                _public_json(entry),
                            ),
                        ).rowcount == 1
                        if created:
                            index_entry_ids.append(str(entry["index_entry_id"]))

                    deadline = _deadline_for(case, envelope)
                    if deadline is not None:
                        _assert_public_safe(deadline)
                        created = connection.execute(
                            """
                            INSERT OR IGNORE INTO mail_deadlines(
                                deadline_id, case_id, due_at, schedule_id,
                                route_id, public_json
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                deadline["deadline_id"],
                                deadline["case_id"],
                                deadline["due_at"],
                                deadline["schedule_id"],
                                deadline["route_id"],
                                _public_json(deadline),
                            ),
                        ).rowcount == 1
                        if created:
                            deadline_ids.append(str(deadline["deadline_id"]))
                    if created_case and inserted:
                        followups.extend(_followups_for(case))
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        receipt = {
            "schema": MAIL_BATCH_RECEIPT_SCHEMA,
            "privacy_boundary": PRIVACY_BOUNDARY,
            "status": "DONE",
            "created_cases": counters["created_cases"],
            "replayed_cases": counters["replayed_cases"],
            "created_index_entries": len(index_entry_ids),
            "created_deadlines": len(deadline_ids),
            "case_refs": sorted(set(case_ids)),
            "index_entry_refs": sorted(index_entry_ids),
            "deadline_refs": sorted(deadline_ids),
            "followup_tasks": tuple(followups[:3]),
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }
        _assert_public_safe(receipt)
        return receipt

    def counts(self) -> dict[str, int]:
        self.initialize()
        with self._connect() as connection:
            return {
                "cases": _count(connection, "mail_cases"),
                "index_entries": _count(connection, "mail_index_entries"),
                "deadlines": _count(connection, "mail_deadlines"),
            }

    def list_deadline_records(self) -> tuple[dict[str, Any], ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT public_json FROM mail_deadlines ORDER BY deadline_id"
            ).fetchall()
        return tuple(json.loads(str(row[0])) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def build_local_inference_request(envelope: MailEnvelope | Mapping[str, Any]) -> dict[str, Any]:
    normalized = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    payload = {
        "privacy_boundary": PRIVACY_BOUNDARY,
        "content_ref_hash": normalized.content_hash,
        "allowed_public_signals": dict(normalized.public_signals),
        "provider": normalized.provider,
        "provider_account_ref": normalized.provider_account_ref,
        "provider_message_ref": normalized.provider_message_ref,
    }
    request = {
        "schema": MAIL_LOCAL_INFERENCE_REQUEST_SCHEMA,
        "request_type": LOCAL_MAIL_CLASSIFIER_REQUEST_TYPE,
        "model": LOCAL_MAIL_CLASSIFIER_MODEL,
        "payload": payload,
        "idempotency_key": "mail-classify:" + normalized.idempotency_key,
        "public_safe": True,
        "private_payloads_included": False,
    }
    _assert_public_safe(request)
    return request


def correlate_github_ci_event(envelope: MailEnvelope | Mapping[str, Any]) -> dict[str, Any] | None:
    normalized = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    signals = normalized.public_signals
    if signals.get("service_identity") not in {"github-actions", "github-notifications"}:
        return None
    repo = _optional_repo(signals.get("repo"))
    workflow = _safe_text(signals.get("workflow"), "workflow", max_length=96, default="unknown")
    run_id = _safe_token(signals.get("run_id", "run-unknown"), "run_id")
    status = _safe_text(signals.get("status"), "status", max_length=32, default="unknown")
    commit_sha = _safe_commit_sha(signals.get("commit_sha"))
    event = {
        "schema": MAIL_GITHUB_CI_EVENT_SCHEMA,
        "case_id": _case_id(normalized),
        "service_identity": signals["service_identity"],
        "repo": repo,
        "workflow": workflow,
        "run_id": run_id,
        "commit_sha": commit_sha,
        "status": status,
        "evidence_ref": "mail-ci:" + normalized.idempotency_key[:32],
        "public_safe": True,
        "private_payloads_included": False,
    }
    _assert_public_safe(event)
    return event


def build_telegram_reply_draft_contract(
    *,
    case_id: str,
    local_draft_ref: str,
    actor_reference: str,
    reason_code: str,
) -> dict[str, Any]:
    contract = {
        "schema": MAIL_TELEGRAM_REPLY_DRAFT_SCHEMA,
        "case_id": _safe_token(case_id, "case_id"),
        "local_draft_ref": _safe_token(local_draft_ref, "local_draft_ref"),
        "actor_reference": _safe_actor_ref(actor_reference),
        "reason_code": _safe_reason(reason_code),
        "allowed_actions": ("approve_send", "revise_local", "discard_local"),
        "requires_local_private_mail_client": True,
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }
    _assert_public_safe(contract)
    return contract


def deadline_to_schedule_spec(deadline: Mapping[str, Any]) -> ScheduleSpec:
    payload = {
        "privacy_boundary": "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
        "bounded": True,
        "approved_capabilities": [],
        "requested_capabilities": [],
        "task_packet": {
            "schema": "skeleton.mail_operations.deadline_reference.v1",
            "deadline_id": _safe_token(deadline.get("deadline_id"), "deadline_id"),
            "case_id": _safe_token(deadline.get("case_id"), "case_id"),
            "retention_class": _safe_text(deadline.get("retention_class"), "retention_class"),
            "public_safe": True,
            "private_payloads_included": False,
        },
    }
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": _safe_token(deadline.get("schedule_id"), "schedule_id"),
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": _non_negative_int(deadline.get("due_at"), "due_at"),
            "timezone": "UTC",
            "route_type": "notify",
            "route_id": _safe_token(deadline.get("route_id"), "route_id"),
            "approval_policy": "notify_only",
            "overlap_policy": "skip",
            "misfire_policy": "run_once",
            "payload": payload,
        }
    )


def assert_public_mail_payload(value: Mapping[str, Any]) -> None:
    _assert_public_safe(value)


def _case_for(envelope: MailEnvelope, *, now: int) -> tuple[dict[str, Any], bool]:
    classification, retention = _classify(envelope.public_signals)
    case = {
        "schema": MAIL_CASE_SCHEMA,
        "case_id": _case_id(envelope),
        "idempotency_key": envelope.idempotency_key,
        "provider": envelope.provider,
        "provider_account_ref": envelope.provider_account_ref,
        "provider_message_ref": envelope.provider_message_ref,
        "content_hash": envelope.content_hash,
        "classification": classification,
        "retention_class": retention,
        "received_at": envelope.received_at,
        "created_at": now,
        "github_ci_event": correlate_github_ci_event(envelope),
        "public_safe": True,
        "private_payloads_included": False,
    }
    return case, True


def _classify(signals: Mapping[str, Any]) -> tuple[str, str]:
    signal_values = {
        str(item).lower()
        for item in _sequence(signals.get("labels")) + _sequence(signals.get("attachment_kinds"))
    }
    service_identity = signals.get("service_identity")
    if service_identity in {"github-actions", "github-notifications"}:
        return "github_ci_event", "github_ci_public_status"
    if "invoice" in signal_values or "invoice_pdf" in signal_values:
        years = signals.get("retention_years")
        if years == 7:
            return "invoice", "invoice_retention_7y"
        return "invoice", "invoice_retention_10y"
    if "deadline" in signal_values or isinstance(signals.get("deadline_epoch"), int):
        return "deadline", "deadline_reference_only"
    return "reference", "none"


def _index_entries_for(
    case: Mapping[str, Any], envelope: MailEnvelope, *, now: int
) -> tuple[dict[str, Any], ...]:
    values = [
        ("classification", str(case["classification"])),
        ("retention_class", str(case["retention_class"])),
    ]
    service = envelope.public_signals.get("service_identity")
    if isinstance(service, str):
        values.append(("service_identity", service))
    entries = []
    for kind, value in values:
        entry_id = "mailidx_" + _sha256_hex(f"{case['case_id']}\n{kind}\n{value}")[:32]
        entries.append(
            {
                "schema": MAIL_INDEX_ENTRY_SCHEMA,
                "index_entry_id": entry_id,
                "case_id": case["case_id"],
                "index_kind": kind,
                "index_value": value,
                "created_at": now,
                "public_safe": True,
                "private_payloads_included": False,
            }
        )
    return tuple(entries)


def _deadline_for(case: Mapping[str, Any], envelope: MailEnvelope) -> dict[str, Any] | None:
    due_at = envelope.public_signals.get("deadline_epoch")
    if not isinstance(due_at, int) or isinstance(due_at, bool) or due_at < 0:
        return None
    deadline_id = "maildl_" + _sha256_hex(f"{case['case_id']}\n{due_at}")[:32]
    schedule_id = "maildeadline." + deadline_id[-24:]
    return {
        "schema": MAIL_DEADLINE_SCHEMA,
        "deadline_id": deadline_id,
        "case_id": case["case_id"],
        "due_at": due_at,
        "schedule_id": schedule_id,
        "route_id": "mail.deadline.notify",
        "retention_class": case["retention_class"],
        "public_safe": True,
        "private_payloads_included": False,
    }


def _followups_for(case: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    base = {
        "schema": MAIL_FOLLOWUP_TASK_SCHEMA,
        "source_case_ref": case["case_id"],
        "privacy_boundary": PRIVACY_BOUNDARY,
        "public_safe": True,
        "private_payloads_included": False,
    }
    candidates = [
        ("mail-cleanup-action-guard", "cleanup/actions side effects require explicit action gate"),
        ("mail-provider-live-integration", "live provider connector must prove read-only dry-run first"),
        ("mail-index-backfill-metrics", "add aggregate observability for local index freshness"),
    ]
    return tuple(
        {
            **base,
            "task_id": task_id,
            "title": title,
            "bounded": True,
            "external_side_effects_allowed": False,
        }
        for task_id, title in candidates
    )


def _public_signals(value: object) -> Mapping[str, Any]:
    signals = _mapping(value, "public_signals")
    _assert_public_safe(signals)
    return dict(signals)


def _assert_public_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _PRIVATE_KEYS:
                raise MailOperationsError("PRIVATE_FIELD_IN_PUBLIC_PAYLOAD", key)
            _assert_public_safe(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_public_safe(item)
        return
    if isinstance(value, str):
        if _EMAIL_RE.search(value):
            raise MailOperationsError("PRIVATE_EMAIL_ADDRESS_IN_PUBLIC_PAYLOAD", value)


def _public_json(value: Mapping[str, Any]) -> str:
    _assert_public_safe(value)
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MailOperationsError(f"INVALID_{field.upper()}", f"{field} must be an object")
    return value


def _sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise MailOperationsError(f"INVALID_{field.upper()}", f"{field} must be a safe token")
    return value


def _safe_text(
    value: object, field: str, *, max_length: int = 128, default: str | None = None
) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise MailOperationsError(f"INVALID_{field.upper()}", f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > max_length:
        raise MailOperationsError(f"INVALID_{field.upper()}", f"{field} is invalid")
    _assert_public_safe(normalized)
    return normalized


def _safe_reason(value: object) -> str:
    text = _safe_text(value, "reason_code", max_length=96)
    if re.fullmatch(r"[A-Z0-9_]+", text) is None:
        raise MailOperationsError("INVALID_REASON_CODE", "reason_code must be upper snake case")
    return text


def _safe_actor_ref(value: object) -> str:
    text = _safe_text(value, "actor_reference", max_length=96)
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9._/-]{1,63}", text) is None:
        raise MailOperationsError("INVALID_ACTOR_REFERENCE", "actor_reference is invalid")
    return text


def _optional_repo(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None:
        raise MailOperationsError("INVALID_REPO", "repo must be public-safe owner/name")
    return value


def _safe_commit_sha(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise MailOperationsError("INVALID_COMMIT_SHA", "commit_sha is invalid")
    return value.lower()


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MailOperationsError(f"INVALID_{field.upper()}", f"{field} must be a non-negative integer")
    return value


def _case_id(envelope: MailEnvelope) -> str:
    return "mailcase_" + envelope.idempotency_key[:32]


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])

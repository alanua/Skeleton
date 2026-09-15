from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4


REFRESH_TRIGGER_NOT_RELEASE_PROOF = "REFRESH_TRIGGER_NOT_RELEASE_PROOF"
RECEIPT_SCHEMA = "skeleton.mail_media_refresh_trigger_receipt.v1"
STORE_SCHEMA = "skeleton.mail_media_refresh_trigger_store.v1"
URL_RE = re.compile(r"https?://[^\s<>()\"']+")


class AmbiguousSinkAcceptance(RuntimeError):
    """Raised when the sink may have accepted the intent before failing."""


class HomeMediaRefreshSink(Protocol):
    supports_idempotent_intent_ref: bool

    def is_available(self) -> bool: ...

    def emit_refresh(self, *, intent_ref: str, candidate_kind: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    provider_ref: str
    canonical_work_patterns: tuple[str, ...]
    dedupe_interval_seconds: int = 3600
    continuation_ttl_seconds: int = 7 * 24 * 3600


@dataclass(frozen=True)
class ProviderNotice:
    provider_ref: str
    message_ref: str
    account_ref: str
    body: str
    observed_at: datetime


@dataclass(frozen=True)
class _IntentRecord:
    intent_ref: str
    state: str
    first_seen_at: datetime
    claim_token: str | None


class MailMediaRefreshStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mail_media_refresh_intents (
                    intent_ref TEXT PRIMARY KEY,
                    schema TEXT NOT NULL,
                    provider_ref_hash TEXT NOT NULL,
                    work_ref_hash TEXT NOT NULL,
                    notice_ref_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    claim_token TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    sink_receipt_ref TEXT,
                    recoverable_reason TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS mail_media_refresh_window_idx
                ON mail_media_refresh_intents(provider_ref_hash, work_ref_hash, first_seen_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS mail_media_refresh_notice_idx
                ON mail_media_refresh_intents(notice_ref_hash)
                """
            )

    def claim_for_emit(
        self,
        *,
        provider_ref_hash: str,
        work_ref_hash: str,
        notice_ref_hash: str,
        observed_at: datetime,
        now: datetime,
        dedupe_interval_seconds: int,
        continuation_ttl_seconds: int,
        sink_available: bool,
    ) -> tuple[str, _IntentRecord]:
        window_start = now - timedelta(seconds=dedupe_interval_seconds)
        stale_before = now - timedelta(seconds=continuation_ttl_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._find_exact_notice(conn, notice_ref_hash)
            if row is None:
                row = self._find_window_match(conn, provider_ref_hash, work_ref_hash, window_start)
            if row is None:
                intent_ref = _intent_ref(provider_ref_hash, work_ref_hash, notice_ref_hash)
                state = "CLAIMED" if sink_available else "WAITING"
                claim_token = _claim_token() if sink_available else None
                conn.execute(
                    """
                    INSERT INTO mail_media_refresh_intents (
                        intent_ref, schema, provider_ref_hash, work_ref_hash, notice_ref_hash,
                        first_seen_at, updated_at, state, claim_token, attempt_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent_ref,
                        STORE_SCHEMA,
                        provider_ref_hash,
                        work_ref_hash,
                        notice_ref_hash,
                        _iso(observed_at),
                        _iso(now),
                        state,
                        claim_token,
                        1 if sink_available else 0,
                    ),
                )
                conn.commit()
                return (
                    "claimed_new" if sink_available else "waiting_new",
                    _IntentRecord(intent_ref, state, observed_at, claim_token),
                )

            record = _record_from_row(row)
            if record.state == "EMITTED":
                conn.commit()
                return "already_emitted", record
            if record.state in {"CLAIMED", "RECOVERABLE_AMBIGUOUS"}:
                conn.commit()
                return "recoverable_incomplete", record
            if record.first_seen_at < stale_before:
                conn.execute(
                    """
                    UPDATE mail_media_refresh_intents
                    SET state = ?, updated_at = ?, recoverable_reason = ?
                    WHERE intent_ref = ? AND state = ?
                    """,
                    ("RECOVERABLE_STALE_WAITING", _iso(now), "continuation_ttl_elapsed", record.intent_ref, "WAITING"),
                )
                conn.commit()
                return "recoverable_stale", _IntentRecord(
                    record.intent_ref, "RECOVERABLE_STALE_WAITING", record.first_seen_at, None
                )
            if not sink_available:
                conn.commit()
                return "waiting_existing", record
            claim_token = _claim_token()
            updated = conn.execute(
                """
                UPDATE mail_media_refresh_intents
                SET state = ?, claim_token = ?, updated_at = ?, attempt_count = attempt_count + 1
                WHERE intent_ref = ? AND state = ?
                """,
                ("CLAIMED", claim_token, _iso(now), record.intent_ref, "WAITING"),
            ).rowcount
            conn.commit()
            if updated != 1:
                return "recoverable_incomplete", record
            return "claimed_waiting", _IntentRecord(record.intent_ref, "CLAIMED", record.first_seen_at, claim_token)

    def mark_emitted(self, *, intent_ref: str, claim_token: str, sink_receipt: Mapping[str, Any], now: datetime) -> bool:
        sink_receipt_ref = _stable_ref("sink-receipt", _canonical_json(sink_receipt))
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE mail_media_refresh_intents
                SET state = ?, updated_at = ?, sink_receipt_ref = ?, recoverable_reason = NULL
                WHERE intent_ref = ? AND state = ? AND claim_token = ?
                """,
                ("EMITTED", _iso(now), sink_receipt_ref, intent_ref, "CLAIMED", claim_token),
            ).rowcount
            return updated == 1

    def mark_ambiguous(self, *, intent_ref: str, claim_token: str, now: datetime) -> bool:
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE mail_media_refresh_intents
                SET state = ?, updated_at = ?, recoverable_reason = ?
                WHERE intent_ref = ? AND state = ? AND claim_token = ?
                """,
                ("RECOVERABLE_AMBIGUOUS", _iso(now), "sink_acceptance_ambiguous", intent_ref, "CLAIMED", claim_token),
            ).rowcount
            return updated == 1

    def _find_exact_notice(self, conn: sqlite3.Connection, notice_ref_hash: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM mail_media_refresh_intents
            WHERE notice_ref_hash = ?
            ORDER BY first_seen_at DESC
            LIMIT 1
            """,
            (notice_ref_hash,),
        ).fetchone()

    def _find_window_match(
        self,
        conn: sqlite3.Connection,
        provider_ref_hash: str,
        work_ref_hash: str,
        window_start: datetime,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM mail_media_refresh_intents
            WHERE provider_ref_hash = ? AND work_ref_hash = ? AND first_seen_at >= ?
            ORDER BY first_seen_at DESC
            LIMIT 1
            """,
            (provider_ref_hash, work_ref_hash, _iso(window_start)),
        ).fetchone()


def process_provider_notice(
    notice: ProviderNotice,
    *,
    config: ProviderRuntimeConfig,
    store: MailMediaRefreshStore,
    sink: HomeMediaRefreshSink | None,
    now: datetime,
) -> dict[str, Any]:
    _validate_notice(notice, config)
    now = _aware_utc(now)
    observed_at = _aware_utc(notice.observed_at)
    provider_ref_hash = _stable_ref("provider", notice.provider_ref)
    notice_ref_hash = _stable_ref("notice", f"{notice.provider_ref}\0{notice.account_ref}\0{notice.message_ref}")
    work_refs = _canonical_work_refs(notice.body, config.canonical_work_patterns)
    if not work_refs:
        return _receipt("BLOCKED", reason="unresolved_work", blocked_count=1)

    sink_available = sink is not None and sink.is_available()
    sink_idempotent = bool(getattr(sink, "supports_idempotent_intent_ref", False)) if sink is not None else False
    outcomes: list[dict[str, str]] = []
    counts = {
        "candidate_count": len(work_refs),
        "intent_count": 0,
        "waiting_count": 0,
        "emitted_count": 0,
        "deduped_count": 0,
        "recoverable_count": 0,
        "blocked_count": 0,
    }

    for work_ref in work_refs:
        work_ref_hash = _stable_ref("work", f"{notice.provider_ref}\0{work_ref}")
        classification, record = store.claim_for_emit(
            provider_ref_hash=provider_ref_hash,
            work_ref_hash=work_ref_hash,
            notice_ref_hash=notice_ref_hash,
            observed_at=observed_at,
            now=now,
            dedupe_interval_seconds=config.dedupe_interval_seconds,
            continuation_ttl_seconds=config.continuation_ttl_seconds,
            sink_available=sink_available,
        )
        counts["intent_count"] += 1
        if classification in {"waiting_new", "waiting_existing"}:
            counts["waiting_count"] += 1
            outcomes.append({"intent_ref": record.intent_ref, "state": "WAITING", "classification": classification})
            continue
        if classification == "already_emitted":
            counts["deduped_count"] += 1
            outcomes.append({"intent_ref": record.intent_ref, "state": "EMITTED", "classification": classification})
            continue
        if classification.startswith("recoverable"):
            counts["recoverable_count"] += 1
            outcomes.append({"intent_ref": record.intent_ref, "state": record.state, "classification": classification})
            continue
        if record.claim_token is None:
            counts["recoverable_count"] += 1
            outcomes.append({"intent_ref": record.intent_ref, "state": "RECOVERABLE_INCOMPLETE", "classification": "missing_claim"})
            continue
        if not sink_idempotent or sink is None:
            store.mark_ambiguous(intent_ref=record.intent_ref, claim_token=record.claim_token, now=now)
            counts["recoverable_count"] += 1
            outcomes.append(
                {
                    "intent_ref": record.intent_ref,
                    "state": "RECOVERABLE_AMBIGUOUS",
                    "classification": "sink_idempotency_unsupported",
                }
            )
            continue
        try:
            sink_receipt = sink.emit_refresh(
                intent_ref=record.intent_ref,
                candidate_kind=REFRESH_TRIGGER_NOT_RELEASE_PROOF,
            )
        except AmbiguousSinkAcceptance:
            store.mark_ambiguous(intent_ref=record.intent_ref, claim_token=record.claim_token, now=now)
            counts["recoverable_count"] += 1
            outcomes.append(
                {
                    "intent_ref": record.intent_ref,
                    "state": "RECOVERABLE_AMBIGUOUS",
                    "classification": "sink_acceptance_ambiguous",
                }
            )
            continue
        if not store.mark_emitted(
            intent_ref=record.intent_ref,
            claim_token=record.claim_token,
            sink_receipt=sink_receipt,
            now=now,
        ):
            counts["recoverable_count"] += 1
            outcomes.append(
                {"intent_ref": record.intent_ref, "state": "RECOVERABLE_INCOMPLETE", "classification": "lost_claim"}
            )
            continue
        counts["emitted_count"] += 1
        outcomes.append({"intent_ref": record.intent_ref, "state": "EMITTED", "classification": classification})

    status = _aggregate_status(counts)
    return _receipt(status, outcomes=outcomes, **counts)


def _canonical_work_refs(body: str, patterns: Sequence[str]) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for url in URL_RE.findall(body):
        clean = url.rstrip(".,;]")
        for pattern in patterns:
            match = re.search(pattern, clean)
            if match is None:
                continue
            value = match.groupdict().get("work") or match.group(1)
            canonical = re.sub(r"[^a-z0-9_.:-]+", "-", value.lower()).strip("-")
            if canonical and canonical not in seen:
                seen.add(canonical)
                refs.append(canonical)
            break
    return tuple(refs)


def _receipt(status: str, *, reason: str | None = None, outcomes: Sequence[Mapping[str, str]] = (), **counts: int) -> dict[str, Any]:
    aggregate = {
        "candidate_count": counts.get("candidate_count", 0),
        "intent_count": counts.get("intent_count", 0),
        "waiting_count": counts.get("waiting_count", 0),
        "emitted_count": counts.get("emitted_count", 0),
        "deduped_count": counts.get("deduped_count", 0),
        "recoverable_count": counts.get("recoverable_count", 0),
        "blocked_count": counts.get("blocked_count", 0),
    }
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "candidate_kind": REFRESH_TRIGGER_NOT_RELEASE_PROOF,
        "aggregate": aggregate,
        "outcomes": list(outcomes),
    }
    if reason is not None:
        receipt["reason"] = reason
    return receipt


def _aggregate_status(counts: Mapping[str, int]) -> str:
    if counts.get("emitted_count", 0):
        return "EMITTED"
    if counts.get("waiting_count", 0):
        return "WAITING"
    if counts.get("deduped_count", 0):
        return "DEDUPED_ALREADY_EMITTED"
    if counts.get("recoverable_count", 0):
        return "RECOVERABLE"
    return "BLOCKED"


def _validate_notice(notice: ProviderNotice, config: ProviderRuntimeConfig) -> None:
    if notice.provider_ref != config.provider_ref:
        raise ValueError("provider_config_mismatch")
    if config.dedupe_interval_seconds <= 0:
        raise ValueError("dedupe_interval_must_be_positive")
    if config.continuation_ttl_seconds <= 0:
        raise ValueError("continuation_ttl_must_be_positive")


def _record_from_row(row: sqlite3.Row) -> _IntentRecord:
    return _IntentRecord(
        intent_ref=str(row["intent_ref"]),
        state=str(row["state"]),
        first_seen_at=datetime.fromisoformat(str(row["first_seen_at"])),
        claim_token=None if row["claim_token"] is None else str(row["claim_token"]),
    )


def _intent_ref(provider_ref_hash: str, work_ref_hash: str, notice_ref_hash: str) -> str:
    return _stable_ref("intent", f"{provider_ref_hash}\0{work_ref_hash}\0{notice_ref_hash}")


def _stable_ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"mail-media-refresh:{namespace}:v1\0{value}".encode("utf-8")).hexdigest()
    return f"{namespace}_{digest[:32]}"


def _claim_token() -> str:
    return f"claim_{uuid4().hex}"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _aware_utc(value).isoformat()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

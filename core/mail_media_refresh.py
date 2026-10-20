from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping, Protocol


RECEIPT_SCHEMA = "skeleton.mail_media_refresh_receipt.v1"
INTENT_SCHEMA = "skeleton.mail_media_refresh_intent.v1"
TRIGGER_REFRESH_NOT_RELEASE_PROOF = "REFRESH_TRIGGER_NOT_RELEASE_PROOF"

RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"
AMBIGUOUS = "AMBIGUOUS"

CLAIMED = "CLAIMED"
RECOVERING = "RECOVERING"
WAITING = "WAITING"
EMITTED = "EMITTED"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"

ACCEPTED = "ACCEPTED"
WAITING_SINK = "WAITING"

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_TERMINAL_STATES = frozenset({EMITTED, RECOVERY_REQUIRED})


class MailMediaRefreshError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MailMediaObservation:
    provider: str
    provider_local_ref: str
    observed_at: int
    received_at: int | None = None
    trigger_reason: str = TRIGGER_REFRESH_NOT_RELEASE_PROOF


@dataclass(frozen=True)
class CanonicalResolveResult:
    status: str
    canonical_work_ref: str | None = None
    reason: str = "RESOLVER_RESULT"


class CanonicalHomeMediaResolver(Protocol):
    def resolve(self, observation: MailMediaObservation) -> CanonicalResolveResult:
        """Resolve provider-local mail observations to the canonical Home Media identity."""


@dataclass(frozen=True)
class MailMediaIntent:
    intent_ref: str
    canonical_work_ref: str
    observed_bucket_start: int
    observed_bucket_end: int
    trigger_reason: str

    def to_sink_payload(self) -> dict[str, Any]:
        return {
            "schema": INTENT_SCHEMA,
            "intent_ref": self.intent_ref,
            "canonical_work_ref": self.canonical_work_ref,
            "observed_bucket_start": self.observed_bucket_start,
            "observed_bucket_end": self.observed_bucket_end,
            "trigger_reason": self.trigger_reason,
            "release_proof": False,
            "home_video_mutation": False,
        }


@dataclass(frozen=True)
class MailMediaSinkResult:
    status: str
    reason: str = "SINK_ACCEPTED"


class MailMediaIntentSink(Protocol):
    idempotent_acceptance_by_intent_ref: bool

    def submit(self, intent: MailMediaIntent) -> MailMediaSinkResult:
        """Accept the intent; idempotency by intent_ref is required for recovery replay."""


@dataclass(frozen=True)
class MailMediaIntentRecord:
    intent_ref: str
    canonical_work_ref: str
    observed_bucket_start: int
    observed_bucket_end: int
    state: str
    reason: str
    created_at: int
    updated_at: int
    claimed_at: int | None
    recovery_attempts: int

    def intent(self) -> MailMediaIntent:
        return MailMediaIntent(
            intent_ref=self.intent_ref,
            canonical_work_ref=self.canonical_work_ref,
            observed_bucket_start=self.observed_bucket_start,
            observed_bucket_end=self.observed_bucket_end,
            trigger_reason=TRIGGER_REFRESH_NOT_RELEASE_PROOF,
        )

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "intent_ref": self.intent_ref,
            "state": self.state,
            "reason": self.reason,
            "observed_bucket_start": self.observed_bucket_start,
            "observed_bucket_end": self.observed_bucket_end,
            "recovery_attempts": self.recovery_attempts,
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }


class MailMediaRefreshStore:
    """Transactional intent outbox for mail-triggered Home Media refreshes."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mail_media_refresh_intents (
                    intent_ref TEXT PRIMARY KEY,
                    canonical_work_ref TEXT NOT NULL,
                    observed_bucket_start INTEGER NOT NULL CHECK(observed_bucket_start >= 0),
                    observed_bucket_end INTEGER NOT NULL CHECK(observed_bucket_end >= 0),
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0),
                    claimed_at INTEGER,
                    recovery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(recovery_attempts >= 0),
                    last_sink_status TEXT,
                    UNIQUE(canonical_work_ref, observed_bucket_start)
                );

                CREATE INDEX IF NOT EXISTS idx_mail_media_refresh_state
                    ON mail_media_refresh_intents(state, updated_at, intent_ref);
                """
            )

    def pre_side_effect_claim(
        self,
        *,
        intent: MailMediaIntent,
        now: int,
    ) -> tuple[MailMediaIntentRecord, bool]:
        _timestamp(now, "now")
        with self._transaction() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO mail_media_refresh_intents (
                    intent_ref, canonical_work_ref, observed_bucket_start,
                    observed_bucket_end, state, reason, created_at, updated_at,
                    claimed_at, recovery_attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    intent.intent_ref,
                    intent.canonical_work_ref,
                    intent.observed_bucket_start,
                    intent.observed_bucket_end,
                    CLAIMED,
                    "CLAIM_COMMITTED_BEFORE_SINK",
                    now,
                    now,
                    now,
                ),
            )
            row = self._row_for_intent(connection, intent.intent_ref)
            if row is None:
                row = connection.execute(
                    """
                    SELECT * FROM mail_media_refresh_intents
                     WHERE canonical_work_ref = ? AND observed_bucket_start = ?
                    """,
                    (intent.canonical_work_ref, intent.observed_bucket_start),
                ).fetchone()
            if row is None:
                raise MailMediaRefreshError("CLAIM_FAILED", "intent claim was not persisted")
            return self._record_from_row(row), inserted.rowcount == 1

    def mark_after_sink(
        self,
        *,
        intent_ref: str,
        expected_states: frozenset[str],
        sink_result: MailMediaSinkResult,
        now: int,
    ) -> MailMediaIntentRecord:
        _timestamp(now, "now")
        if sink_result.status == ACCEPTED:
            state = EMITTED
            reason = "SINK_ACCEPTED"
        elif sink_result.status == WAITING_SINK:
            state = WAITING
            reason = "SINK_WAITING"
        else:
            state = RECOVERY_REQUIRED
            reason = "SINK_NOT_ACCEPTED"
        with self._transaction() as connection:
            placeholders = ",".join("?" for _ in expected_states)
            result = connection.execute(
                f"""
                UPDATE mail_media_refresh_intents
                   SET state = ?, reason = ?, updated_at = ?, last_sink_status = ?
                 WHERE intent_ref = ? AND state IN ({placeholders})
                """,
                [state, reason, now, sink_result.status, intent_ref, *sorted(expected_states)],
            )
            if result.rowcount != 1:
                raise MailMediaRefreshError("INTENT_STATE_CONFLICT", "intent state changed")
            row = self._row_for_intent(connection, intent_ref)
            assert row is not None
            return self._record_from_row(row)

    def claim_waiting(self, *, intent_ref: str, now: int) -> MailMediaIntentRecord | None:
        _timestamp(now, "now")
        with self._transaction() as connection:
            result = connection.execute(
                """
                UPDATE mail_media_refresh_intents
                   SET state = ?, reason = 'WAITING_RESUME_CLAIMED', updated_at = ?, claimed_at = ?
                 WHERE intent_ref = ? AND state = ?
                """,
                (RECOVERING, now, now, intent_ref, WAITING),
            )
            if result.rowcount != 1:
                return None
            row = self._row_for_intent(connection, intent_ref)
            assert row is not None
            return self._record_from_row(row)

    def claim_stale_for_recovery(
        self,
        *,
        now: int,
        stale_after_seconds: int,
        max_recovery_attempts: int,
    ) -> MailMediaIntentRecord | None:
        _timestamp(now, "now")
        if stale_after_seconds <= 0:
            raise MailMediaRefreshError("INVALID_STALE_AFTER", "stale_after_seconds must be positive")
        if max_recovery_attempts <= 0:
            raise MailMediaRefreshError(
                "INVALID_MAX_RECOVERY_ATTEMPTS",
                "max_recovery_attempts must be positive",
            )
        cutoff = max(0, now - stale_after_seconds)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM mail_media_refresh_intents
                 WHERE state IN (?, ?)
                   AND claimed_at IS NOT NULL
                   AND claimed_at <= ?
                   AND recovery_attempts < ?
                 ORDER BY claimed_at, intent_ref
                 LIMIT 1
                """,
                (CLAIMED, RECOVERING, cutoff, max_recovery_attempts),
            ).fetchone()
            if row is None:
                self._escalate_exhausted(connection, now=now, cutoff=cutoff, max_recovery_attempts=max_recovery_attempts)
                return None
            record = self._record_from_row(row)
            result = connection.execute(
                """
                UPDATE mail_media_refresh_intents
                   SET state = ?, reason = 'CLAIMED_RECOVERY_IN_PROGRESS',
                       updated_at = ?, claimed_at = ?, recovery_attempts = recovery_attempts + 1
                 WHERE intent_ref = ? AND state IN (?, ?) AND recovery_attempts = ?
                """,
                (
                    RECOVERING,
                    now,
                    now,
                    record.intent_ref,
                    CLAIMED,
                    RECOVERING,
                    record.recovery_attempts,
                ),
            )
            if result.rowcount != 1:
                return None
            self._escalate_exhausted(connection, now=now, cutoff=cutoff, max_recovery_attempts=max_recovery_attempts)
            updated = self._row_for_intent(connection, record.intent_ref)
            assert updated is not None
            return self._record_from_row(updated)

    def mark_recovery_required(self, *, intent_ref: str, reason: str, now: int) -> MailMediaIntentRecord:
        _timestamp(now, "now")
        _reason(reason)
        with self._transaction() as connection:
            result = connection.execute(
                """
                UPDATE mail_media_refresh_intents
                   SET state = ?, reason = ?, updated_at = ?
                 WHERE intent_ref = ? AND state NOT IN (?, ?)
                """,
                (RECOVERY_REQUIRED, reason, now, intent_ref, EMITTED, RECOVERY_REQUIRED),
            )
            if result.rowcount != 1:
                raise MailMediaRefreshError("INTENT_STATE_CONFLICT", "intent state changed")
            row = self._row_for_intent(connection, intent_ref)
            assert row is not None
            return self._record_from_row(row)

    def get(self, intent_ref: str) -> MailMediaIntentRecord | None:
        with self._connect() as connection:
            row = self._row_for_intent(connection, intent_ref)
            return None if row is None else self._record_from_row(row)

    def list_records(self) -> tuple[MailMediaIntentRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM mail_media_refresh_intents ORDER BY created_at, intent_ref"
            ).fetchall()
            return tuple(self._record_from_row(row) for row in rows)

    def public_counts(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, reason, COUNT(*) FROM mail_media_refresh_intents GROUP BY state, reason"
            ).fetchall()
        return {
            "schema": RECEIPT_SCHEMA,
            "counts": [
                {"state": str(row[0]), "reason": str(row[1]), "count": int(row[2])}
                for row in rows
            ],
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }

    @staticmethod
    def _row_for_intent(connection: sqlite3.Connection, intent_ref: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM mail_media_refresh_intents WHERE intent_ref = ?",
            (intent_ref,),
        ).fetchone()

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> MailMediaIntentRecord:
        return MailMediaIntentRecord(
            intent_ref=str(row["intent_ref"]),
            canonical_work_ref=str(row["canonical_work_ref"]),
            observed_bucket_start=int(row["observed_bucket_start"]),
            observed_bucket_end=int(row["observed_bucket_end"]),
            state=str(row["state"]),
            reason=str(row["reason"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            claimed_at=None if row["claimed_at"] is None else int(row["claimed_at"]),
            recovery_attempts=int(row["recovery_attempts"]),
        )

    @staticmethod
    def _escalate_exhausted(
        connection: sqlite3.Connection,
        *,
        now: int,
        cutoff: int,
        max_recovery_attempts: int,
    ) -> None:
        connection.execute(
            """
            UPDATE mail_media_refresh_intents
               SET state = ?, reason = 'CLAIMED_RECOVERY_EXHAUSTED', updated_at = ?
             WHERE state IN (?, ?, ?)
               AND claimed_at IS NOT NULL
               AND claimed_at <= ?
               AND recovery_attempts >= ?
            """,
            (
                RECOVERY_REQUIRED,
                now,
                CLAIMED,
                RECOVERING,
                WAITING,
                cutoff,
                max_recovery_attempts,
            ),
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


class MailMediaRefreshProcessor:
    def __init__(
        self,
        *,
        store: MailMediaRefreshStore,
        resolver: CanonicalHomeMediaResolver,
        sink: MailMediaIntentSink,
        dedupe_window_seconds: int,
        max_recovery_attempts: int = 2,
    ) -> None:
        if dedupe_window_seconds <= 0:
            raise MailMediaRefreshError("INVALID_DEDUPE_WINDOW", "dedupe window must be positive")
        if max_recovery_attempts <= 0:
            raise MailMediaRefreshError(
                "INVALID_MAX_RECOVERY_ATTEMPTS",
                "max recovery attempts must be positive",
            )
        self.store = store
        self.resolver = resolver
        self.sink = sink
        self.dedupe_window_seconds = dedupe_window_seconds
        self.max_recovery_attempts = max_recovery_attempts

    def ingest(self, observation: MailMediaObservation, *, now: int) -> dict[str, Any]:
        _timestamp(now, "now")
        self.store.initialize()
        resolution = self.resolver.resolve(observation)
        if resolution.status != RESOLVED:
            return _blocked_receipt(
                reason="CANONICAL_WORK_UNRESOLVED"
                if resolution.status == UNRESOLVED
                else "CANONICAL_WORK_AMBIGUOUS",
                status="NOOP",
                observed_at=observation.observed_at,
            )
        canonical_work_ref = _canonical_ref(resolution.canonical_work_ref)
        intent = build_intent(
            canonical_work_ref=canonical_work_ref,
            observed_at=observation.observed_at,
            dedupe_window_seconds=self.dedupe_window_seconds,
        )
        record, created = self.store.pre_side_effect_claim(intent=intent, now=now)
        if not created:
            return self._continue_existing(record, now=now)
        sink_result = self.sink.submit(intent)
        record = self.store.mark_after_sink(
            intent_ref=intent.intent_ref,
            expected_states=frozenset({CLAIMED}),
            sink_result=sink_result,
            now=now,
        )
        return _receipt(record, "created")

    def recover_claimed(
        self,
        *,
        now: int,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        self.store.initialize()
        record = self.store.claim_stale_for_recovery(
            now=now,
            stale_after_seconds=stale_after_seconds,
            max_recovery_attempts=self.max_recovery_attempts,
        )
        if record is None:
            return {
                "schema": RECEIPT_SCHEMA,
                "status": "NOOP",
                "reason": "NO_RECOVERABLE_CLAIMED_INTENT",
                "recovered": 0,
                "public_safe": True,
                "private_payloads_included": False,
                "external_side_effects_executed": False,
            }
        if not self.sink.idempotent_acceptance_by_intent_ref:
            record = self.store.mark_recovery_required(
                intent_ref=record.intent_ref,
                reason="RECOVERY_REQUIRES_IDEMPOTENT_SINK_BY_INTENT_REF",
                now=now,
            )
            return _receipt(record, "recovery_required")
        sink_result = self.sink.submit(record.intent())
        record = self.store.mark_after_sink(
            intent_ref=record.intent_ref,
            expected_states=frozenset({RECOVERING}),
            sink_result=sink_result,
            now=now,
        )
        return _receipt(record, "recovered")

    def _continue_existing(self, record: MailMediaIntentRecord, *, now: int) -> dict[str, Any]:
        if record.state == EMITTED:
            return _receipt(record, "already_emitted")
        if record.state == RECOVERY_REQUIRED:
            return _receipt(record, "recovery_required")
        if record.state == WAITING:
            claimed = self.store.claim_waiting(intent_ref=record.intent_ref, now=now)
            if claimed is None:
                current = self.store.get(record.intent_ref)
                assert current is not None
                return _receipt(current, "waiting_claim_conflict")
            sink_result = self.sink.submit(claimed.intent())
            updated = self.store.mark_after_sink(
                intent_ref=claimed.intent_ref,
                expected_states=frozenset({RECOVERING}),
                sink_result=sink_result,
                now=now,
            )
            return _receipt(updated, "waiting_continued")
        return _receipt(record, "already_claimed")


def build_intent(
    *,
    canonical_work_ref: str,
    observed_at: int,
    dedupe_window_seconds: int,
) -> MailMediaIntent:
    canonical = _canonical_ref(canonical_work_ref)
    _timestamp(observed_at, "observed_at")
    if dedupe_window_seconds <= 0:
        raise MailMediaRefreshError("INVALID_DEDUPE_WINDOW", "dedupe window must be positive")
    bucket_start = (observed_at // dedupe_window_seconds) * dedupe_window_seconds
    bucket_end = bucket_start + dedupe_window_seconds - 1
    material = {
        "canonical_work_ref": canonical,
        "observed_bucket_start": bucket_start,
        "trigger_reason": TRIGGER_REFRESH_NOT_RELEASE_PROOF,
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    return MailMediaIntent(
        intent_ref=f"mail_media_refresh:{digest[:32]}",
        canonical_work_ref=canonical,
        observed_bucket_start=bucket_start,
        observed_bucket_end=bucket_end,
        trigger_reason=TRIGGER_REFRESH_NOT_RELEASE_PROOF,
    )


def _receipt(record: MailMediaIntentRecord, action: str) -> dict[str, Any]:
    receipt = record.public_receipt()
    receipt.update(
        {
            "status": record.state,
            "action": action,
            "intent_count": 1,
            "external_side_effects_executed": action in {
                "created",
                "waiting_continued",
                "recovered",
            }
            and record.state in {EMITTED, WAITING},
        }
    )
    return receipt


def _blocked_receipt(*, reason: str, status: str, observed_at: int) -> dict[str, Any]:
    _timestamp(observed_at, "observed_at")
    return {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "reason": reason,
        "intent_count": 0,
        "observed_bucket_start": None,
        "observed_bucket_end": None,
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _canonical_ref(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_REF_RE.fullmatch(value):
        raise MailMediaRefreshError("INVALID_CANONICAL_WORK_REF", "canonical work ref is invalid")
    return value


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MailMediaRefreshError(
            f"INVALID_{field.upper()}",
            f"{field} must be a non-negative integer",
        )
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise MailMediaRefreshError("INVALID_REASON", "reason is invalid")
    return value

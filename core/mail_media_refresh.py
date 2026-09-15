from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlparse


REFRESH_TRIGGER_NOT_RELEASE_PROOF = "REFRESH_TRIGGER_NOT_RELEASE_PROOF"
SCHEMA_VERSION = "skeleton.mail_media_refresh.sqlite.v1"
PUBLIC_RECEIPT_SCHEMA = "skeleton.mail_media_refresh.public_receipt.v1"

_SAFE_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_PROVIDER_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_SAFE_CANONICAL_REF_RE = re.compile(r"^home-media-work:[A-Za-z0-9_.:-]{1,240}$")


@dataclass(frozen=True)
class ProviderWorkEvidence:
    provider: str
    provider_work_ref: str
    observed_at: datetime
    notice_ref: str
    trigger_reason: str = REFRESH_TRIGGER_NOT_RELEASE_PROOF


@dataclass(frozen=True)
class CanonicalResolution:
    status: Literal["RESOLVED", "WAITING", "UNRESOLVED", "AMBIGUOUS"]
    canonical_work_ref: str | None = None
    reason_code: str = "canonical_resolution_pending"

    @classmethod
    def resolved(cls, canonical_work_ref: str) -> "CanonicalResolution":
        return cls("RESOLVED", canonical_work_ref, "canonical_work_resolved")

    @classmethod
    def waiting(cls, reason_code: str = "canonical_resolution_waiting") -> "CanonicalResolution":
        return cls("WAITING", None, reason_code)

    @classmethod
    def unresolved(cls, reason_code: str = "canonical_work_unresolved") -> "CanonicalResolution":
        return cls("UNRESOLVED", None, reason_code)

    @classmethod
    def ambiguous(cls, reason_code: str = "canonical_work_ambiguous") -> "CanonicalResolution":
        return cls("AMBIGUOUS", None, reason_code)


@dataclass(frozen=True)
class RefreshIntent:
    intent_ref: str
    canonical_work_ref: str
    observed_at: datetime
    trigger_reason: str


@dataclass(frozen=True)
class SinkResult:
    status: Literal["ACCEPTED", "WAITING", "AMBIGUOUS", "REJECTED"]
    reason_code: str = "sink_accepted"


class CanonicalHomeMediaWorkResolver(Protocol):
    def resolve(self, evidence: ProviderWorkEvidence) -> CanonicalResolution:
        """Map provider-local evidence to the real canonical Home Media work ref."""


class RefreshIntentSink(Protocol):
    def emit(self, intent: RefreshIntent) -> SinkResult:
        """Idempotently accept a stable intent_ref before performing external work."""


class MailMediaRefreshStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _initialize(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS mail_media_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mail_media_refresh_intents (
                    intent_ref TEXT PRIMARY KEY,
                    canonical_work_ref TEXT NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    provider_hash TEXT NOT NULL,
                    provider_work_ref_hash TEXT NOT NULL,
                    notice_ref_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claim_token TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    emitted_at TEXT,
                    sink_reason_code TEXT
                );
                CREATE INDEX IF NOT EXISTS mail_media_refresh_dedupe_idx
                    ON mail_media_refresh_intents (
                        canonical_work_ref,
                        trigger_reason,
                        observed_at
                    );
                """
            )
            con.execute(
                "INSERT OR IGNORE INTO mail_media_meta (key, value) VALUES (?, ?)",
                ("schema_version", SCHEMA_VERSION),
            )

    def count_intents(self) -> int:
        with self.connect() as con:
            row = con.execute("SELECT COUNT(*) AS count FROM mail_media_refresh_intents").fetchone()
        return int(row["count"])

    def intent_status(self, intent_ref: str) -> str | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT status FROM mail_media_refresh_intents WHERE intent_ref = ?",
                (intent_ref,),
            ).fetchone()
        return None if row is None else str(row["status"])


class MailMediaRefreshProcessor:
    def __init__(
        self,
        store: MailMediaRefreshStore,
        *,
        resolver: CanonicalHomeMediaWorkResolver,
        sink: RefreshIntentSink,
        dedupe_interval: timedelta,
    ):
        if dedupe_interval.total_seconds() < 0:
            raise ValueError("dedupe_interval_must_be_non_negative")
        self.store = store
        self.resolver = resolver
        self.sink = sink
        self.dedupe_interval = dedupe_interval

    def process_notice(self, evidence: ProviderWorkEvidence) -> dict[str, object]:
        evidence = _validate_evidence(evidence)
        resolution = self.resolver.resolve(evidence)
        if resolution.status != "RESOLVED":
            return _public_receipt(
                status="WAITING" if resolution.status == "WAITING" else "FAILED_CLOSED",
                reason_codes=(resolution.reason_code,),
                unresolved_count=1,
            )
        canonical_work_ref = _validate_canonical_work_ref(resolution.canonical_work_ref)
        now = _utc_now_iso()
        observed_at = _utc_iso(evidence.observed_at)
        lower = _utc_iso(evidence.observed_at - self.dedupe_interval)
        upper = _utc_iso(evidence.observed_at + self.dedupe_interval)
        intent = RefreshIntent(
            intent_ref=_intent_ref(canonical_work_ref, evidence.trigger_reason, evidence.observed_at),
            canonical_work_ref=canonical_work_ref,
            observed_at=evidence.observed_at,
            trigger_reason=evidence.trigger_reason,
        )
        claim_token = _private_hash(("claim", intent.intent_ref, now))
        provider_hash = _private_hash(("provider", evidence.provider))
        provider_work_ref_hash = _private_hash(("provider_work_ref", evidence.provider_work_ref))
        notice_ref_hash = _private_hash(("notice_ref", evidence.notice_ref))

        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                """
                SELECT intent_ref, status
                FROM mail_media_refresh_intents
                WHERE canonical_work_ref = ?
                  AND trigger_reason = ?
                  AND observed_at BETWEEN ? AND ?
                  AND status IN ('CLAIMED', 'WAITING', 'EMITTED')
                ORDER BY observed_at ASC, intent_ref ASC
                LIMIT 1
                """,
                (canonical_work_ref, evidence.trigger_reason, lower, upper),
            ).fetchone()
            if existing is not None:
                con.commit()
                return _public_receipt(
                    status="DEDUPED",
                    reason_codes=("deduped_within_observed_interval",),
                    deduped_count=1,
                    intent_ref=str(existing["intent_ref"]),
                )
            con.execute(
                """
                INSERT INTO mail_media_refresh_intents (
                    intent_ref, canonical_work_ref, trigger_reason, observed_at,
                    provider_hash, provider_work_ref_hash, notice_ref_hash,
                    status, claim_token, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'CLAIMED', ?, ?, ?)
                """,
                (
                    intent.intent_ref,
                    canonical_work_ref,
                    evidence.trigger_reason,
                    observed_at,
                    provider_hash,
                    provider_work_ref_hash,
                    notice_ref_hash,
                    claim_token,
                    now,
                    now,
                ),
            )
            con.commit()
        return self._emit_claimed(intent, claim_token)

    def resume_waiting(self) -> dict[str, object]:
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """
                SELECT intent_ref, canonical_work_ref, observed_at, trigger_reason
                FROM mail_media_refresh_intents
                WHERE status = 'WAITING'
                ORDER BY observed_at ASC, intent_ref ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                con.commit()
                return _public_receipt(status="NOOP", reason_codes=("no_waiting_intent",))
            claim_token = _private_hash(("resume", row["intent_ref"], _utc_now_iso()))
            con.execute(
                """
                UPDATE mail_media_refresh_intents
                SET status = 'CLAIMED', claim_token = ?, updated_at = ?
                WHERE intent_ref = ? AND status = 'WAITING'
                """,
                (claim_token, _utc_now_iso(), row["intent_ref"]),
            )
            if con.total_changes < 1:
                con.commit()
                return _public_receipt(status="NOOP", reason_codes=("claim_lost",))
            con.commit()
        intent = RefreshIntent(
            intent_ref=str(row["intent_ref"]),
            canonical_work_ref=str(row["canonical_work_ref"]),
            observed_at=_parse_utc(str(row["observed_at"])),
            trigger_reason=str(row["trigger_reason"]),
        )
        return self._emit_claimed(intent, claim_token)

    def claim_intent_for_test(self, intent_ref: str) -> bool:
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            result = con.execute(
                """
                UPDATE mail_media_refresh_intents
                SET status = 'CLAIMED', claim_token = ?, updated_at = ?
                WHERE intent_ref = ? AND status = 'WAITING'
                """,
                (_private_hash(("test-claim", intent_ref, _utc_now_iso())), _utc_now_iso(), intent_ref),
            )
            con.commit()
        return result.rowcount == 1

    def public_receipt(self) -> dict[str, object]:
        with self.store.connect() as con:
            rows = con.execute(
                "SELECT status, intent_ref FROM mail_media_refresh_intents ORDER BY intent_ref"
            ).fetchall()
        counts: dict[str, int] = {}
        opaque_refs: list[str] = []
        for row in rows:
            status = str(row["status"])
            counts[status] = counts.get(status, 0) + 1
            opaque_refs.append(_private_hash(("public-intent", row["intent_ref"])))
        return {
            "schema": PUBLIC_RECEIPT_SCHEMA,
            "intent_counts": counts,
            "opaque_intent_refs": opaque_refs,
            "reason_codes": tuple(sorted(counts)),
        }

    def _emit_claimed(self, intent: RefreshIntent, claim_token: str) -> dict[str, object]:
        result = self.sink.emit(intent)
        now = _utc_now_iso()
        if result.status == "ACCEPTED":
            status = "EMITTED"
            emitted_at = now
            receipt_status = "EMITTED"
            emitted_count = 1
        elif result.status == "WAITING":
            status = "WAITING"
            emitted_at = None
            receipt_status = "WAITING"
            emitted_count = 0
        elif result.status == "AMBIGUOUS":
            status = "FAILED_AMBIGUOUS"
            emitted_at = None
            receipt_status = "FAILED_CLOSED"
            emitted_count = 0
        else:
            status = "FAILED_CLOSED"
            emitted_at = None
            receipt_status = "FAILED_CLOSED"
            emitted_count = 0

        with self.store.connect() as con:
            con.execute(
                """
                UPDATE mail_media_refresh_intents
                SET status = ?, updated_at = ?, emitted_at = ?, sink_reason_code = ?
                WHERE intent_ref = ? AND status = 'CLAIMED' AND claim_token = ?
                """,
                (status, now, emitted_at, result.reason_code, intent.intent_ref, claim_token),
            )
        return _public_receipt(
            status=receipt_status,
            reason_codes=(result.reason_code,),
            emitted_count=emitted_count,
            waiting_count=1 if status == "WAITING" else 0,
            intent_ref=intent.intent_ref,
        )


def parse_provider_runtime_url(provider: str, url: str) -> ProviderWorkEvidence:
    if _SAFE_PROVIDER_RE.fullmatch(provider) is None:
        raise ValueError("provider_invalid")
    parsed = urlparse(url)
    provider_work_ref = ""
    if provider == "synthetic-stream":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "watch":
            provider_work_ref = parts[-1]
        else:
            provider_work_ref = parse_qs(parsed.query).get("work", [""])[0]
    else:
        raise ValueError("provider_matcher_unavailable")
    if _SAFE_PROVIDER_REF_RE.fullmatch(provider_work_ref) is None:
        raise ValueError("provider_work_ref_invalid")
    return ProviderWorkEvidence(
        provider=provider,
        provider_work_ref=provider_work_ref,
        observed_at=datetime.now(UTC),
        notice_ref=_private_hash(("provider-url", provider, url)),
    )


def _validate_evidence(evidence: ProviderWorkEvidence) -> ProviderWorkEvidence:
    if _SAFE_PROVIDER_RE.fullmatch(evidence.provider) is None:
        raise ValueError("provider_invalid")
    if _SAFE_PROVIDER_REF_RE.fullmatch(evidence.provider_work_ref) is None:
        raise ValueError("provider_work_ref_invalid")
    if not evidence.notice_ref:
        raise ValueError("notice_ref_required")
    if evidence.trigger_reason != REFRESH_TRIGGER_NOT_RELEASE_PROOF:
        raise ValueError("unsupported_trigger_reason")
    observed_at = evidence.observed_at
    if observed_at.tzinfo is None:
        raise ValueError("observed_at_timezone_required")
    return ProviderWorkEvidence(
        provider=evidence.provider,
        provider_work_ref=evidence.provider_work_ref,
        observed_at=observed_at.astimezone(UTC),
        notice_ref=evidence.notice_ref,
        trigger_reason=evidence.trigger_reason,
    )


def _validate_canonical_work_ref(value: str | None) -> str:
    if not isinstance(value, str) or _SAFE_CANONICAL_REF_RE.fullmatch(value) is None:
        raise ValueError("canonical_work_ref_invalid")
    return value


def _intent_ref(canonical_work_ref: str, trigger_reason: str, observed_at: datetime) -> str:
    return "mail-media-refresh:" + _private_hash(
        ("intent", canonical_work_ref, trigger_reason, _utc_iso(observed_at))
    )[:48]


def _public_receipt(
    *,
    status: str,
    reason_codes: tuple[str, ...],
    emitted_count: int = 0,
    waiting_count: int = 0,
    deduped_count: int = 0,
    unresolved_count: int = 0,
    intent_ref: str | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": PUBLIC_RECEIPT_SCHEMA,
        "status": status,
        "counts": {
            "emitted": emitted_count,
            "waiting": waiting_count,
            "deduped": deduped_count,
            "unresolved": unresolved_count,
        },
        "reason_codes": reason_codes,
    }
    if intent_ref is not None:
        receipt["opaque_intent_ref"] = _private_hash(("public-intent", intent_ref))
    return receipt


def _utc_now_iso() -> str:
    return _utc_iso(datetime.now(UTC))


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _private_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

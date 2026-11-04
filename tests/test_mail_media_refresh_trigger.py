from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from core.mail_media_refresh_trigger import (
    REFRESH_TRIGGER_NOT_RELEASE_PROOF,
    AmbiguousSinkAcceptance,
    MailMediaRefreshStore,
    ProviderNotice,
    ProviderRuntimeConfig,
    process_provider_notice,
)


PRIVATE_PROVIDER = "synthetic-private-provider-marker"
PRIVATE_ACCOUNT = "synthetic-private-account-marker"
PRIVATE_MESSAGE = "synthetic-private-message-marker"
PRIVATE_WORK = "Synthetic.Private.Work.Marker"
PRIVATE_URL = f"https://synthetic.invalid/watch/{PRIVATE_WORK}"
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class RecordingSink:
    supports_idempotent_intent_ref = True

    def __init__(self, *, available: bool = True, ambiguous: bool = False, reentrant: Any = None) -> None:
        self.available = available
        self.ambiguous = ambiguous
        self.reentrant = reentrant
        self.calls: list[dict[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def emit_refresh(self, *, intent_ref: str, candidate_kind: str) -> Mapping[str, Any]:
        self.calls.append({"intent_ref": intent_ref, "candidate_kind": candidate_kind})
        if self.reentrant is not None:
            self.reentrant()
        if self.ambiguous:
            raise AmbiguousSinkAcceptance("synthetic ambiguous acceptance")
        return {"accepted": True, "intent_ref": intent_ref}


class NonIdempotentSink(RecordingSink):
    supports_idempotent_intent_ref = False


def config(interval: int = 3600) -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        provider_ref=PRIVATE_PROVIDER,
        canonical_work_patterns=(r"/watch/(?P<work>[A-Za-z0-9_.:-]+)",),
        dedupe_interval_seconds=interval,
    )


def notice(
    *,
    body: str | None = None,
    message_ref: str = PRIVATE_MESSAGE,
    observed_at: datetime = NOW,
) -> ProviderNotice:
    return ProviderNotice(
        provider_ref=PRIVATE_PROVIDER,
        message_ref=message_ref,
        account_ref=PRIVATE_ACCOUNT,
        body=body if body is not None else f"provider update: {PRIVATE_URL}",
        observed_at=observed_at,
    )


def trigger(
    tmp_path: Path,
    sink: RecordingSink | None,
    item: ProviderNotice | None = None,
    *,
    now: datetime = NOW,
    interval: int = 3600,
) -> dict[str, Any]:
    return process_provider_notice(
        item or notice(),
        config=config(interval),
        store=MailMediaRefreshStore(tmp_path / "refresh.sqlite3"),
        sink=sink,
        now=now,
    )


def test_sink_unavailable_persists_waiting_public_safe_receipt(tmp_path: Path) -> None:
    sink = RecordingSink(available=False)

    receipt = trigger(tmp_path, sink)

    assert receipt["status"] == "WAITING"
    assert receipt["candidate_kind"] == REFRESH_TRIGGER_NOT_RELEASE_PROOF
    assert receipt["aggregate"]["waiting_count"] == 1
    assert sink.calls == []
    assert _states(tmp_path) == ["WAITING"]
    _assert_public_safe(receipt)


def test_waiting_resumes_when_sink_later_available_and_emits_once(tmp_path: Path) -> None:
    first_sink = RecordingSink(available=False)
    first = trigger(tmp_path, first_sink)
    second_sink = RecordingSink(available=True)

    second = trigger(tmp_path, second_sink, now=NOW + timedelta(minutes=1))

    assert first["status"] == "WAITING"
    assert second["status"] == "EMITTED"
    assert _states(tmp_path) == ["EMITTED"]
    assert len(second_sink.calls) == 1
    assert second_sink.calls[0]["candidate_kind"] == REFRESH_TRIGGER_NOT_RELEASE_PROOF


def test_replay_after_emitted_returns_deduped_with_zero_new_sink_calls(tmp_path: Path) -> None:
    sink = RecordingSink()
    first = trigger(tmp_path, sink)
    replay_sink = RecordingSink()

    replay = trigger(tmp_path, replay_sink, now=NOW + timedelta(minutes=2))

    assert first["status"] == "EMITTED"
    assert replay["status"] == "DEDUPED_ALREADY_EMITTED"
    assert replay["aggregate"]["deduped_count"] == 1
    assert replay_sink.calls == []


def test_competing_retry_claim_cannot_double_emit(tmp_path: Path) -> None:
    reentrant_receipts: list[dict[str, Any]] = []

    def competing_retry() -> None:
        reentrant_receipts.append(trigger(tmp_path, RecordingSink(), now=NOW + timedelta(seconds=1)))

    sink = RecordingSink(reentrant=competing_retry)

    receipt = trigger(tmp_path, sink)

    assert receipt["status"] == "EMITTED"
    assert reentrant_receipts[0]["status"] == "RECOVERABLE"
    assert reentrant_receipts[0]["aggregate"]["recoverable_count"] == 1
    assert len(sink.calls) == 1
    assert _states(tmp_path) == ["EMITTED"]


def test_ambiguous_sink_crash_fails_closed_on_replay(tmp_path: Path) -> None:
    first_sink = RecordingSink(ambiguous=True)
    first = trigger(tmp_path, first_sink)
    replay_sink = RecordingSink()

    replay = trigger(tmp_path, replay_sink, now=NOW + timedelta(seconds=5))

    assert first["status"] == "RECOVERABLE"
    assert first["outcomes"][0]["classification"] == "sink_acceptance_ambiguous"
    assert replay["status"] == "RECOVERABLE"
    assert replay_sink.calls == []
    assert _states(tmp_path) == ["RECOVERABLE_AMBIGUOUS"]


def test_non_idempotent_sink_fails_closed_before_side_effect(tmp_path: Path) -> None:
    sink = NonIdempotentSink()

    receipt = trigger(tmp_path, sink)

    assert receipt["status"] == "RECOVERABLE"
    assert receipt["outcomes"][0]["classification"] == "sink_idempotency_unsupported"
    assert sink.calls == []


def test_two_notices_seconds_apart_across_old_bucket_boundary_dedupe(tmp_path: Path) -> None:
    first_sink = RecordingSink()
    first = trigger(
        tmp_path,
        first_sink,
        notice(message_ref="synthetic-message-before-boundary", observed_at=NOW.replace(minute=59, second=58)),
        now=NOW.replace(minute=59, second=58),
        interval=10,
    )
    replay_sink = RecordingSink()
    second = trigger(
        tmp_path,
        replay_sink,
        notice(message_ref="synthetic-message-after-boundary", observed_at=NOW.replace(hour=13, minute=0, second=2)),
        now=NOW.replace(hour=13, minute=0, second=2),
        interval=10,
    )

    assert first["status"] == "EMITTED"
    assert second["status"] == "DEDUPED_ALREADY_EMITTED"
    assert replay_sink.calls == []
    assert _states(tmp_path) == ["EMITTED"]


def test_notice_outside_dedupe_interval_may_emit_new_intent(tmp_path: Path) -> None:
    first_sink = RecordingSink()
    trigger(tmp_path, first_sink, notice(message_ref="synthetic-message-one"), interval=10)
    second_sink = RecordingSink()

    second = trigger(
        tmp_path,
        second_sink,
        notice(message_ref="synthetic-message-two", observed_at=NOW + timedelta(seconds=11)),
        now=NOW + timedelta(seconds=11),
        interval=10,
    )

    assert second["status"] == "EMITTED"
    assert len(first_sink.calls) == 1
    assert len(second_sink.calls) == 1
    assert first_sink.calls[0]["intent_ref"] != second_sink.calls[0]["intent_ref"]
    assert sorted(_states(tmp_path)) == ["EMITTED", "EMITTED"]


def test_duplicate_links_in_one_mail_emit_one_intent(tmp_path: Path) -> None:
    sink = RecordingSink()
    body = f"{PRIVATE_URL} and duplicate {PRIVATE_URL}."

    receipt = trigger(tmp_path, sink, notice(body=body))

    assert receipt["status"] == "EMITTED"
    assert receipt["aggregate"]["candidate_count"] == 1
    assert len(sink.calls) == 1


def test_unrelated_malformed_unresolved_notice_fails_closed_without_media_record(tmp_path: Path) -> None:
    sink = RecordingSink()

    receipt = trigger(tmp_path, sink, notice(body="provider update without resolvable work link"))

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "unresolved_work"
    assert sink.calls == []
    assert not (tmp_path / "refresh.sqlite3").exists() or _states(tmp_path) == []


def test_privacy_serialization_has_no_private_marker_values(tmp_path: Path) -> None:
    sink = RecordingSink(available=False)

    receipt = trigger(tmp_path, sink)

    _assert_public_safe(receipt)


def _states(tmp_path: Path) -> list[str]:
    db = tmp_path / "refresh.sqlite3"
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return [str(row[0]) for row in conn.execute("SELECT state FROM mail_media_refresh_intents ORDER BY intent_ref")]


def _assert_public_safe(receipt: Mapping[str, Any]) -> None:
    payload = json.dumps(receipt, sort_keys=True)
    for private in (PRIVATE_PROVIDER, PRIVATE_ACCOUNT, PRIVATE_MESSAGE, PRIVATE_WORK, PRIVATE_URL):
        assert private not in payload
    assert "intent_" in payload or receipt["status"] == "BLOCKED"

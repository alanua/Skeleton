from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.mail_media_refresh import (
    CanonicalResolution,
    MailMediaRefreshProcessor,
    MailMediaRefreshStore,
    ProviderWorkEvidence,
    RefreshIntent,
    SinkResult,
    parse_provider_runtime_url,
)


OBSERVED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


@dataclass
class FixedResolver:
    mapping: dict[str, CanonicalResolution]

    def resolve(self, evidence: ProviderWorkEvidence) -> CanonicalResolution:
        return self.mapping[evidence.provider_work_ref]


class RecordingSink:
    def __init__(self, *results: SinkResult):
        self.results = list(results)
        self.calls: list[RefreshIntent] = []

    def emit(self, intent: RefreshIntent) -> SinkResult:
        self.calls.append(intent)
        if self.results:
            return self.results.pop(0)
        return SinkResult("ACCEPTED", "sink_accepted")


def evidence(provider_ref: str, *, observed_at: datetime = OBSERVED, notice_ref: str = "notice-1") -> ProviderWorkEvidence:
    return ProviderWorkEvidence(
        provider="synthetic-stream",
        provider_work_ref=provider_ref,
        observed_at=observed_at,
        notice_ref=notice_ref,
    )


def processor(
    tmp_path: Path,
    *,
    resolver: FixedResolver,
    sink: RecordingSink,
    interval: timedelta = timedelta(minutes=10),
) -> MailMediaRefreshProcessor:
    return MailMediaRefreshProcessor(
        MailMediaRefreshStore(tmp_path / "mail-media.sqlite"),
        resolver=resolver,
        sink=sink,
        dedupe_interval=interval,
    )


def test_provider_local_ref_resolves_through_injected_canonical_resolver_before_emit(tmp_path: Path) -> None:
    resolver = FixedResolver({"provider-episode-7": CanonicalResolution.resolved("home-media-work:series-001")})
    sink = RecordingSink()

    receipt = processor(tmp_path, resolver=resolver, sink=sink).process_notice(evidence("provider-episode-7"))

    assert receipt["status"] == "EMITTED"
    assert len(sink.calls) == 1
    assert sink.calls[0].canonical_work_ref == "home-media-work:series-001"
    assert sink.calls[0].trigger_reason == "REFRESH_TRIGGER_NOT_RELEASE_PROOF"


def test_unresolved_canonical_work_emits_nothing_and_creates_no_media_identity(tmp_path: Path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite")
    proc = MailMediaRefreshProcessor(
        store,
        resolver=FixedResolver({"provider-unknown": CanonicalResolution.unresolved()}),
        sink=RecordingSink(),
        dedupe_interval=timedelta(minutes=10),
    )

    receipt = proc.process_notice(evidence("provider-unknown"))

    assert receipt["status"] == "FAILED_CLOSED"
    assert store.count_intents() == 0


def test_provider_slug_differing_from_canonical_ref_proves_no_normalization_shortcut(tmp_path: Path) -> None:
    parsed = parse_provider_runtime_url("synthetic-stream", "https://stream.example/watch/provider-slug-9")
    resolver = FixedResolver({"provider-slug-9": CanonicalResolution.resolved("home-media-work:canonical-different-42")})
    sink = RecordingSink()

    processor(tmp_path, resolver=resolver, sink=sink).process_notice(
        ProviderWorkEvidence(
            provider=parsed.provider,
            provider_work_ref=parsed.provider_work_ref,
            observed_at=OBSERVED,
            notice_ref=parsed.notice_ref,
        )
    )

    assert parsed.provider_work_ref == "provider-slug-9"
    assert sink.calls[0].canonical_work_ref == "home-media-work:canonical-different-42"
    assert sink.calls[0].canonical_work_ref != f"home-media-work:{parsed.provider_work_ref}"


def test_two_notices_observed_seconds_apart_processed_days_later_dedupe(tmp_path: Path) -> None:
    resolver = FixedResolver(
        {
            "provider-a": CanonicalResolution.resolved("home-media-work:canonical-a"),
            "provider-b": CanonicalResolution.resolved("home-media-work:canonical-a"),
        }
    )
    sink = RecordingSink()
    proc = processor(tmp_path, resolver=resolver, sink=sink, interval=timedelta(minutes=5))

    first = proc.process_notice(evidence("provider-a", observed_at=OBSERVED, notice_ref="notice-1"))
    second = proc.process_notice(
        evidence("provider-b", observed_at=OBSERVED + timedelta(seconds=30), notice_ref="notice-2")
    )

    assert first["status"] == "EMITTED"
    assert second["status"] == "DEDUPED"
    assert len(sink.calls) == 1


def test_notice_outside_interval_may_emit_new_intent(tmp_path: Path) -> None:
    resolver = FixedResolver({"provider-a": CanonicalResolution.resolved("home-media-work:canonical-a")})
    sink = RecordingSink()
    proc = processor(tmp_path, resolver=resolver, sink=sink, interval=timedelta(minutes=5))

    proc.process_notice(evidence("provider-a", observed_at=OBSERVED, notice_ref="notice-1"))
    receipt = proc.process_notice(
        evidence("provider-a", observed_at=OBSERVED + timedelta(minutes=6), notice_ref="notice-2")
    )

    assert receipt["status"] == "EMITTED"
    assert len(sink.calls) == 2
    assert sink.calls[0].intent_ref != sink.calls[1].intent_ref


def test_waiting_to_emitted_exactly_once_and_replay_zero_calls(tmp_path: Path) -> None:
    resolver = FixedResolver({"provider-a": CanonicalResolution.resolved("home-media-work:canonical-a")})
    sink = RecordingSink(SinkResult("WAITING", "sink_unavailable"), SinkResult("ACCEPTED", "sink_accepted"))
    proc = processor(tmp_path, resolver=resolver, sink=sink)

    first = proc.process_notice(evidence("provider-a"))
    second = proc.resume_waiting()
    replay = proc.resume_waiting()

    assert first["status"] == "WAITING"
    assert second["status"] == "EMITTED"
    assert replay["status"] == "NOOP"
    assert len(sink.calls) == 2
    assert sink.calls[0].intent_ref == sink.calls[1].intent_ref


def test_competing_claim_cannot_double_emit(tmp_path: Path) -> None:
    resolver = FixedResolver({"provider-a": CanonicalResolution.resolved("home-media-work:canonical-a")})
    sink = RecordingSink(SinkResult("WAITING", "sink_unavailable"))
    proc = processor(tmp_path, resolver=resolver, sink=sink)

    proc.process_notice(evidence("provider-a"))
    intent_ref = sink.calls[0].intent_ref

    assert proc.claim_intent_for_test(intent_ref) is True
    assert proc.claim_intent_for_test(intent_ref) is False


def test_ambiguous_sink_path_fails_closed(tmp_path: Path) -> None:
    resolver = FixedResolver({"provider-a": CanonicalResolution.resolved("home-media-work:canonical-a")})
    sink = RecordingSink(SinkResult("AMBIGUOUS", "sink_acceptance_ambiguous"))
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite")
    proc = MailMediaRefreshProcessor(
        store,
        resolver=resolver,
        sink=sink,
        dedupe_interval=timedelta(minutes=10),
    )

    receipt = proc.process_notice(evidence("provider-a"))

    assert receipt["status"] == "FAILED_CLOSED"
    assert store.intent_status(sink.calls[0].intent_ref) == "FAILED_AMBIGUOUS"


def test_privacy_serialization_clean(tmp_path: Path) -> None:
    provider_ref = "provider-secret-slug"
    canonical_ref = "home-media-work:canonical-private-title"
    notice_ref = "notice-private-message-id"
    resolver = FixedResolver({provider_ref: CanonicalResolution.resolved(canonical_ref)})
    sink = RecordingSink()
    proc = processor(tmp_path, resolver=resolver, sink=sink)

    private_receipt = proc.process_notice(evidence(provider_ref, notice_ref=notice_ref))
    public_receipt = proc.public_receipt()
    encoded = json.dumps([private_receipt, public_receipt], sort_keys=True)

    assert provider_ref not in encoded
    assert canonical_ref not in encoded
    assert notice_ref not in encoded
    assert "opaque_intent_ref" in encoded


def test_ambiguous_canonical_resolution_recovers_fail_closed_without_intent(tmp_path: Path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite")
    proc = MailMediaRefreshProcessor(
        store,
        resolver=FixedResolver({"provider-a": CanonicalResolution.ambiguous()}),
        sink=RecordingSink(),
        dedupe_interval=timedelta(minutes=10),
    )

    receipt = proc.process_notice(evidence("provider-a"))

    assert receipt["status"] == "FAILED_CLOSED"
    assert store.count_intents() == 0


def test_claim_state_transition_precedes_sink_side_effect(tmp_path: Path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite")

    class InspectingSink(RecordingSink):
        def emit(self, intent: RefreshIntent) -> SinkResult:
            with sqlite3.connect(store.path) as con:
                status = con.execute(
                    "SELECT status FROM mail_media_refresh_intents WHERE intent_ref = ?",
                    (intent.intent_ref,),
                ).fetchone()[0]
            assert status == "CLAIMED"
            return super().emit(intent)

    proc = MailMediaRefreshProcessor(
        store,
        resolver=FixedResolver({"provider-a": CanonicalResolution.resolved("home-media-work:canonical-a")}),
        sink=InspectingSink(),
        dedupe_interval=timedelta(minutes=10),
    )

    assert proc.process_notice(evidence("provider-a"))["status"] == "EMITTED"

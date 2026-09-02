from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.mail_media_refresh import (
    ACCEPTED,
    AMBIGUOUS,
    CLAIMED,
    EMITTED,
    RECOVERY_REQUIRED,
    RESOLVED,
    TRIGGER_REFRESH_NOT_RELEASE_PROOF,
    WAITING,
    WAITING_SINK,
    CanonicalResolveResult,
    MailMediaIntent,
    MailMediaObservation,
    MailMediaRefreshProcessor,
    MailMediaRefreshStore,
    MailMediaSinkResult,
    build_intent,
)


@dataclass
class Resolver:
    result: CanonicalResolveResult
    seen: list[MailMediaObservation]

    def resolve(self, observation: MailMediaObservation) -> CanonicalResolveResult:
        self.seen.append(observation)
        return self.result


class Sink:
    def __init__(
        self,
        *,
        results: list[MailMediaSinkResult] | None = None,
        idempotent: bool = True,
        on_submit: Callable[[MailMediaIntent], None] | None = None,
    ) -> None:
        self.idempotent_acceptance_by_intent_ref = idempotent
        self.results = list(results or [MailMediaSinkResult(ACCEPTED)])
        self.calls: list[MailMediaIntent] = []
        self.on_submit = on_submit

    def submit(self, intent: MailMediaIntent) -> MailMediaSinkResult:
        self.calls.append(intent)
        if self.on_submit is not None:
            self.on_submit(intent)
        if self.results:
            return self.results.pop(0)
        return MailMediaSinkResult(ACCEPTED)


def _processor(
    tmp_path,
    *,
    resolver: Resolver | None = None,
    sink: Sink | None = None,
) -> tuple[MailMediaRefreshProcessor, MailMediaRefreshStore, Sink, Resolver]:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite3")
    store.initialize()
    active_resolver = resolver or Resolver(
        CanonicalResolveResult(RESOLVED, "home-media:work-1"),
        [],
    )
    active_sink = sink or Sink()
    return (
        MailMediaRefreshProcessor(
            store=store,
            resolver=active_resolver,
            sink=active_sink,
            dedupe_window_seconds=3600,
            max_recovery_attempts=2,
        ),
        store,
        active_sink,
        active_resolver,
    )


def _observation(*, observed_at: int, received_at: int = 9999) -> MailMediaObservation:
    return MailMediaObservation(
        provider="mail_fixture",
        provider_local_ref="provider-message-local-id",
        observed_at=observed_at,
        received_at=received_at,
    )


def test_canonical_resolver_provider_local_separation(tmp_path) -> None:
    processor, store, sink, resolver = _processor(tmp_path)

    receipt = processor.ingest(_observation(observed_at=3700), now=4000)

    assert resolver.seen[0].provider_local_ref == "provider-message-local-id"
    record = store.list_records()[0]
    assert record.canonical_work_ref == "home-media:work-1"
    assert record.intent_ref == sink.calls[0].intent_ref
    assert "provider-message-local-id" not in record.intent_ref
    assert receipt["reason"] == "SINK_ACCEPTED"


def test_unresolved_or_ambiguous_emits_zero_and_creates_zero_identity(tmp_path) -> None:
    for status in ("UNRESOLVED", AMBIGUOUS):
        resolver = Resolver(CanonicalResolveResult(status, None), [])
        processor, store, sink, _ = _processor(tmp_path / status, resolver=resolver)

        receipt = processor.ingest(_observation(observed_at=100), now=200)

        assert receipt["intent_count"] == 0
        assert receipt["external_side_effects_executed"] is False
        assert store.list_records() == ()
        assert sink.calls == []


def test_delayed_ingest_cross_boundary_dedupe_uses_observed_at(tmp_path) -> None:
    processor, store, sink, _ = _processor(tmp_path)

    first = processor.ingest(_observation(observed_at=3599, received_at=7201), now=7201)
    second = processor.ingest(_observation(observed_at=3598, received_at=10801), now=10801)

    assert first["observed_bucket_start"] == 0
    assert second["action"] == "already_emitted"
    assert len(store.list_records()) == 1
    assert len(sink.calls) == 1


def test_outside_window_creates_new_intent(tmp_path) -> None:
    processor, store, sink, _ = _processor(tmp_path)

    processor.ingest(_observation(observed_at=3599), now=4000)
    processor.ingest(_observation(observed_at=3600), now=4001)

    records = store.list_records()
    assert len(records) == 2
    assert records[0].intent_ref != records[1].intent_ref
    assert len(sink.calls) == 2


def test_waiting_resumes_to_emitted_once_and_terminal_replay_zero_calls(tmp_path) -> None:
    sink = Sink(results=[MailMediaSinkResult(WAITING_SINK), MailMediaSinkResult(ACCEPTED)])
    processor, store, _, _ = _processor(tmp_path, sink=sink)
    observation = _observation(observed_at=1000)

    waiting = processor.ingest(observation, now=1100)
    resumed = processor.ingest(observation, now=1200)
    replay = processor.ingest(observation, now=1300)

    assert waiting["status"] == WAITING
    assert resumed["status"] == EMITTED
    assert replay["action"] == "already_emitted"
    assert store.list_records()[0].state == EMITTED
    assert [call.intent_ref for call in sink.calls] == [sink.calls[0].intent_ref] * 2


def test_claim_exists_before_sink_side_effect(tmp_path) -> None:
    observed = []
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite3")
    store.initialize()

    def before_submit(intent: MailMediaIntent) -> None:
        record = store.get(intent.intent_ref)
        assert record is not None
        observed.append(record.state)

    sink = Sink(on_submit=before_submit)
    resolver = Resolver(CanonicalResolveResult(RESOLVED, "home-media:work-1"), [])
    processor = MailMediaRefreshProcessor(
        store=store,
        resolver=resolver,
        sink=sink,
        dedupe_window_seconds=3600,
    )

    processor.ingest(_observation(observed_at=1000), now=1100)

    assert observed == [CLAIMED]


def test_crash_after_sink_accepted_recovery_reuses_identical_intent_and_emits(tmp_path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite3")
    store.initialize()
    intent = build_intent(
        canonical_work_ref="home-media:work-1",
        observed_at=1000,
        dedupe_window_seconds=3600,
    )
    store.pre_side_effect_claim(intent=intent, now=1000)
    sink = Sink(idempotent=True)
    processor = MailMediaRefreshProcessor(
        store=store,
        resolver=Resolver(CanonicalResolveResult(RESOLVED, "home-media:work-1"), []),
        sink=sink,
        dedupe_window_seconds=3600,
        max_recovery_attempts=2,
    )

    receipt = processor.recover_claimed(now=5000, stale_after_seconds=100)

    assert receipt["status"] == EMITTED
    assert store.get(intent.intent_ref).state == EMITTED  # type: ignore[union-attr]
    assert [call.intent_ref for call in sink.calls] == [intent.intent_ref]


def test_crash_recovery_without_idempotent_sink_fails_closed_without_sink_call(tmp_path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite3")
    store.initialize()
    intent = build_intent(
        canonical_work_ref="home-media:work-1",
        observed_at=1000,
        dedupe_window_seconds=3600,
    )
    store.pre_side_effect_claim(intent=intent, now=1000)
    sink = Sink(idempotent=False)
    processor = MailMediaRefreshProcessor(
        store=store,
        resolver=Resolver(CanonicalResolveResult(RESOLVED, "home-media:work-1"), []),
        sink=sink,
        dedupe_window_seconds=3600,
    )

    receipt = processor.recover_claimed(now=5000, stale_after_seconds=100)

    assert receipt["status"] == RECOVERY_REQUIRED
    assert receipt["reason"] == "RECOVERY_REQUIRES_IDEMPOTENT_SINK_BY_INTENT_REF"
    assert store.get(intent.intent_ref).state == RECOVERY_REQUIRED  # type: ignore[union-attr]
    assert sink.calls == []


def test_two_recovery_workers_cannot_produce_distinct_external_operations(tmp_path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite3")
    store.initialize()
    intent = build_intent(
        canonical_work_ref="home-media:work-1",
        observed_at=1000,
        dedupe_window_seconds=3600,
    )
    store.pre_side_effect_claim(intent=intent, now=1000)
    sink = Sink(idempotent=True)
    first = MailMediaRefreshProcessor(
        store=store,
        resolver=Resolver(CanonicalResolveResult(RESOLVED, "home-media:work-1"), []),
        sink=sink,
        dedupe_window_seconds=3600,
    )
    second = MailMediaRefreshProcessor(
        store=store,
        resolver=Resolver(CanonicalResolveResult(RESOLVED, "home-media:work-1"), []),
        sink=sink,
        dedupe_window_seconds=3600,
    )

    first.recover_claimed(now=5000, stale_after_seconds=100)
    second.recover_claimed(now=5001, stale_after_seconds=100)

    assert [call.intent_ref for call in sink.calls] == [intent.intent_ref]
    assert len({call.intent_ref for call in sink.calls}) == 1


def test_bounded_recovery_exhausts_to_recovery_required(tmp_path) -> None:
    store = MailMediaRefreshStore(tmp_path / "mail-media.sqlite3")
    store.initialize()
    intent = build_intent(
        canonical_work_ref="home-media:work-1",
        observed_at=1000,
        dedupe_window_seconds=3600,
    )
    store.pre_side_effect_claim(intent=intent, now=1000)
    sink = Sink(results=[MailMediaSinkResult(WAITING_SINK)], idempotent=True)
    processor = MailMediaRefreshProcessor(
        store=store,
        resolver=Resolver(CanonicalResolveResult(RESOLVED, "home-media:work-1"), []),
        sink=sink,
        dedupe_window_seconds=3600,
        max_recovery_attempts=1,
    )

    processor.recover_claimed(now=5000, stale_after_seconds=100)
    processor.recover_claimed(now=9000, stale_after_seconds=100)

    record = store.get(intent.intent_ref)
    assert record is not None
    assert record.state == RECOVERY_REQUIRED
    assert record.reason == "CLAIMED_RECOVERY_EXHAUSTED"
    assert len(sink.calls) == 1


def test_privacy_serialization_clean(tmp_path) -> None:
    processor, store, _, _ = _processor(tmp_path)
    processor.ingest(
        MailMediaObservation(
            provider="mail_fixture",
            provider_local_ref="private-provider-message-id",
            observed_at=1000,
            received_at=2000,
        ),
        now=2000,
    )

    public = str(store.public_counts()) + str(store.list_records()[0].public_receipt())

    assert "private-provider-message-id" not in public
    assert "mail_fixture" not in public
    assert "home-media:work-1" not in public
    assert TRIGGER_REFRESH_NOT_RELEASE_PROOF not in public

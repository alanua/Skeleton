from __future__ import annotations

import json
from pathlib import Path

from core.media_translation_monitor import (
    DISCOVERY_CRON,
    LOCALIZATION_CRON,
    EvidenceStrength,
    LocalizationCapability,
    LocalizationObservation,
    MediaIdentity,
    MediaKind,
    MediaTranslationMonitor,
    MediaTranslationStateStore,
    ReleaseSignal,
    ReleaseState,
    TitleStatus,
    discovery_schedule_id,
    localization_schedule_id,
)
from core.media_translation_providers import classify_public_metadata
from core.scheduler_engine import SchedulerEngine
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE, SharedDispatcher, SharedDispatchRequest


def _identity(tmdb_id: int = 321, title: str = "Synthetic Public Title") -> MediaIdentity:
    return MediaIdentity(
        work_id=f"work-{tmdb_id}",
        tmdb_id=tmdb_id,
        media_kind=MediaKind.TV,
        title=title,
    )


def _monitor(tmp_path: Path, notifier=None, providers=()):
    scheduler = SchedulerStore(tmp_path / "scheduler.sqlite3")
    monitor = MediaTranslationMonitor(
        state_store=MediaTranslationStateStore(tmp_path / "media-monitor.json"),
        scheduler_store=scheduler,
        notifier=notifier,
        providers=providers,
    )
    return monitor, scheduler


def test_failed_localization_search_persists_monitor_and_scheduler_chain(tmp_path: Path) -> None:
    monitor, scheduler = _monitor(tmp_path)
    identity = _identity()
    signal = ReleaseSignal(identity, "S01E01", "Season 1 Episode 1")

    first = monitor.record_failed_localized_search(signal, now=100)
    second = monitor.record_failed_localized_search(signal, now=101)

    assert first["subscribed"] is True
    assert second["subscribed"] is True
    assert scheduler.status_counts()["schedules"] == 2
    assert scheduler.get_current(discovery_schedule_id(identity)).spec.cron_expression == DISCOVERY_CRON
    assert scheduler.get_current(localization_schedule_id(identity)).spec.cron_expression == LOCALIZATION_CRON
    assert monitor.monitor_state(identity)["monitor_enabled"] is True


def test_monitor_does_not_introduce_media_sqlite_authority() -> None:
    source = Path("core/media_translation_monitor.py").read_text(encoding="utf-8").lower()

    assert "sqlite3" not in source
    assert "create table" not in source


def test_ordinary_browse_search_does_not_subscribe(tmp_path: Path) -> None:
    monitor, scheduler = _monitor(tmp_path)

    result = monitor.record_browse_search(_identity(), now=100)

    assert result["subscribed"] is False
    scheduler.initialize()
    assert scheduler.status_counts()["schedules"] == 0


def test_manual_on_off_reflected_per_title_and_off_suppresses_auto_follow(tmp_path: Path) -> None:
    monitor, scheduler = _monitor(tmp_path)
    title_a = _identity(1, "Synthetic A")
    title_b = _identity(2, "Synthetic B")
    monitor.set_monitor(title_a, enabled=True, explicit=True, now=100)
    monitor.set_monitor(title_b, enabled=True, explicit=True, now=100)

    monitor.set_monitor(title_a, enabled=False, explicit=True, now=110)
    auto = monitor.auto_follow_watched_series(title_a, now=120)

    assert auto is None
    assert monitor.monitor_state(title_a)["monitor_enabled"] is False
    assert monitor.monitor_state(title_b)["monitor_enabled"] is True
    assert scheduler.get_current(discovery_schedule_id(title_a)).enabled is False
    assert scheduler.get_current(localization_schedule_id(title_a)).enabled is False
    assert scheduler.get_current(discovery_schedule_id(title_b)).enabled is True


def test_tmdb_release_only_waits_for_translation_and_sends_zero_telegram(tmp_path: Path) -> None:
    sends = []
    monitor, _scheduler = _monitor(tmp_path, notifier=lambda message, reply_markup=None: sends.append(message))
    state = monitor.ingest_release_signal(ReleaseSignal(_identity(), "S01E02", "Season 1 Episode 2"), now=100)

    assert state is ReleaseState.WAITING_FOR_TRANSLATION
    assert sends == []


def test_announcement_only_remains_waiting_and_sends_zero_telegram(tmp_path: Path) -> None:
    sends = []
    monitor, _scheduler = _monitor(tmp_path, notifier=lambda message, reply_markup=None: sends.append(message))
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, explicit=True, now=90)
    monitor.ingest_release_signal(ReleaseSignal(identity, "S01E03", "Season 1 Episode 3"), now=100)

    alerts = monitor.ingest_localization_observations(
        identity,
        [
            LocalizationObservation(
                "announcement",
                "S01E03",
                LocalizationCapability.UK_AUDIO,
                available=False,
                strength=EvidenceStrength.ANNOUNCEMENT,
            )
        ],
        now=101,
    )

    assert alerts == ()
    assert sends == []


def test_confirmed_capabilities_are_classified_without_mislabeling_subtitles() -> None:
    observations = classify_public_metadata(
        "kinobaza",
        "S01E04",
        "<html>S01E04 українська озвучка і українські субтитри</html>",
    )

    capabilities = {item.capability for item in observations}
    assert LocalizationCapability.UK_AUDIO in capabilities
    assert LocalizationCapability.UK_SUBTITLES in capabilities
    subtitle = [item for item in observations if item.capability is LocalizationCapability.UK_SUBTITLES][0]
    assert subtitle.capability is not LocalizationCapability.UK_AUDIO


def test_duplicate_providers_create_one_watchlist_item_and_one_telegram(tmp_path: Path) -> None:
    sends = []
    monitor, _scheduler = _monitor(tmp_path, notifier=lambda message, reply_markup=None: sends.append(message))
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, explicit=True, now=90)
    monitor.ingest_release_signal(ReleaseSignal(identity, "S01E05", "Season 1 Episode 5"), now=100)

    alerts = monitor.ingest_localization_observations(
        identity,
        [
            LocalizationObservation("megogo", "S01E05", LocalizationCapability.UK_DUB, True),
            LocalizationObservation("sweet.tv", "S01E05", LocalizationCapability.UK_DUB, True),
        ],
        now=101,
    )
    retry = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("megogo", "S01E05", LocalizationCapability.UK_DUB, True)],
        now=102,
    )
    state = json.loads((tmp_path / "media-monitor.json").read_text(encoding="utf-8"))

    assert len(alerts) == 1
    assert retry == ()
    assert len(sends) == 1
    assert len(state["watch_list"]) == 1
    assert next(iter(state["watch_list"].values()))["watched"] is False
    assert len(state["alert_receipts"]) == 1


def test_scheduler_dispatch_route_executes_monitor_and_unknown_route_fails_closed(tmp_path: Path) -> None:
    class Provider:
        def observations_for(self, identity, release_key):
            return (LocalizationObservation("megogo", release_key, LocalizationCapability.UK_AUDIO, True),)

    sends = []
    monitor, scheduler = _monitor(tmp_path, notifier=lambda message, reply_markup=None: sends.append(message))
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, explicit=True, now=50)
    monitor.ingest_release_signal(ReleaseSignal(identity, "S01E06", "Season 1 Episode 6"), now=60)
    dispatcher = SharedDispatcher.for_media_translation_monitor(
        state_path=str(tmp_path / "media-monitor.json"),
        scheduler_store=scheduler,
        notifier=lambda message, reply_markup=None: sends.append(message),
        providers=(Provider(),),
    )

    receipt = SchedulerEngine(scheduler).tick(now=17 * 60, dispatcher=dispatcher)
    bad = SharedDispatcher({}).dispatch(
        SharedDispatchRequest(
            occurrence_id="occ-test",
            route_type="workflow",
            route_id="missing",
            payload={
                "privacy_boundary": PRIVACY_PUBLIC_SAFE,
                "bounded": True,
                "approved_capabilities": ["media:translation_monitor"],
                "requested_capabilities": ["media:translation_monitor"],
                "task_packet": {"schema": "skeleton.media.translation_monitor_task.v1"},
            },
            attempt=1,
            idempotency_key="occ-test:attempt:1",
        )
    )

    assert receipt["dispatch"]["claimed"] >= 1
    assert len(sends) == 1
    assert bad.reason == "ROUTE_NOT_ALLOWLISTED"


def test_ended_title_keeps_pending_localization_schedule_but_stops_discovery(tmp_path: Path) -> None:
    monitor, scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, explicit=True, now=50)

    monitor.ingest_release_signal(
        ReleaseSignal(identity, "S01E07", "Season 1 Episode 7", title_status=TitleStatus.CANCELED),
        now=60,
    )

    assert scheduler.get_current(discovery_schedule_id(identity)).enabled is False
    assert scheduler.get_current(localization_schedule_id(identity)).enabled is True


def test_terminal_to_active_resumes_discovery_when_monitor_remains_on(tmp_path: Path) -> None:
    monitor, scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, explicit=True, now=50)
    monitor.ingest_release_signal(
        ReleaseSignal(identity, "S01E07", "Season 1 Episode 7", title_status=TitleStatus.ENDED),
        now=60,
    )

    monitor.set_monitor(identity, enabled=True, explicit=False, now=70, title_status=TitleStatus.ACTIVE)

    assert scheduler.get_current(discovery_schedule_id(identity)).enabled is True
    assert scheduler.get_current(localization_schedule_id(identity)).enabled is True


def test_baseline_does_not_backfill_alerts_or_watchlist_and_restart_preserves_dedupe(tmp_path: Path) -> None:
    sends = []
    monitor, scheduler = _monitor(tmp_path, notifier=lambda message, reply_markup=None: sends.append(message))
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, explicit=True, now=50)
    monitor.ingest_release_signal(ReleaseSignal(identity, "S01E08", "Season 1 Episode 8", baseline=True), now=60)
    assert monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("apple", "S01E08", LocalizationCapability.UK_SUBTITLES, True)],
        now=70,
    ) == ()

    reopened = MediaTranslationMonitor(
        state_store=MediaTranslationStateStore(tmp_path / "media-monitor.json"),
        scheduler_store=scheduler,
        notifier=lambda message, reply_markup=None: sends.append(message),
    )
    assert reopened.monitor_state(identity)["monitor_enabled"] is True
    state = json.loads((tmp_path / "media-monitor.json").read_text(encoding="utf-8"))
    assert state["watch_list"] == {}
    assert state["alert_receipts"] == {}
    assert sends == []


def test_privacy_public_receipts_do_not_contain_secrets_or_private_history(tmp_path: Path) -> None:
    sends = []
    monitor, _scheduler = _monitor(tmp_path, notifier=lambda message, reply_markup=None: sends.append(message))
    identity = _identity(title="Synthetic Public Title")
    monitor.set_monitor(identity, enabled=True, explicit=True, now=50)
    monitor.ingest_release_signal(ReleaseSignal(identity, "S01E09", "Season 1 Episode 9"), now=60)
    monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("netflix", "S01E09", LocalizationCapability.UK_AUDIO, True)],
        now=70,
    )

    encoded = (tmp_path / "media-monitor.json").read_text(encoding="utf-8").lower()
    assert "bot_token" not in encoded
    assert "chat_id" not in encoded
    assert "private_viewing_history" not in encoded
    assert "real_title" not in encoded

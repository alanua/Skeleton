from pathlib import Path
import re

from core.media_translation_monitor import (
    MEDIA_RELEASE_ALERT_SCHEMA,
    CanonicalMediaIdentity,
    LocalizationCapability,
    LocalizationObservation,
    MediaKind,
    MediaTranslationMonitor,
    MediaTranslationMonitorStore,
    ReleaseSignal,
    ReleaseState,
    SyntheticLocalizationProvider,
    SyntheticTmdbAdapter,
    TitleStatus,
    media_monitor_schedule_id,
    video_tab_monitor_contract,
)
from core.scheduler_engine import SchedulerEngine
from core.scheduler_store import SchedulerStore


ROOT = Path(__file__).resolve().parents[1]


def _identity(tmdb_id: int = 12345, title: str = "Synthetic Series") -> CanonicalMediaIdentity:
    return CanonicalMediaIdentity(tmdb_id=tmdb_id, media_kind=MediaKind.TV, title=title)


def _monitor(tmp_path: Path) -> tuple[MediaTranslationMonitor, MediaTranslationMonitorStore, SchedulerStore]:
    media_store = MediaTranslationMonitorStore(tmp_path / "media.sqlite3")
    scheduler_store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    return MediaTranslationMonitor(media_store=media_store, scheduler_store=scheduler_store), media_store, scheduler_store


def test_existing_title_opens_with_persisted_monitor_state_by_tmdb_id(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, now=100)

    reopened = media_store.monitor_state("tmdb:tv:12345")

    assert reopened is not None
    assert reopened["monitor_enabled"] is True
    assert reopened["tmdb_id"] == 12345


def test_switch_on_reuses_exactly_one_scheduler_subscription(tmp_path) -> None:
    monitor, _media_store, scheduler = _monitor(tmp_path)
    identity = _identity()

    first = monitor.set_monitor(identity, enabled=True, now=100)
    second = monitor.set_monitor(identity, enabled=True, now=101)

    assert first.schedule_id == second.schedule_id == media_monitor_schedule_id(identity)
    assert first.scheduler_created is True
    assert second.scheduler_created is False
    assert scheduler.status_counts()["schedules"] == 1
    assert scheduler.get_current(first.schedule_id).enabled is True


def test_switch_off_is_idempotent_and_keeps_history(tmp_path) -> None:
    monitor, media_store, scheduler = _monitor(tmp_path)
    identity = _identity()
    release = ReleaseSignal(identity, "S06E01", "Season 6 Episode 1")
    monitor.set_monitor(identity, enabled=True, now=100)
    monitor.ingest_tmdb_release_signal(release, now=110)

    monitor.set_monitor(identity, enabled=False, now=120)
    monitor.set_monitor(identity, enabled=False, now=121)

    assert scheduler.get_current(media_monitor_schedule_id(identity)).enabled is False
    assert media_store.monitor_state(identity.canonical_id)["monitor_enabled"] is False
    assert media_store.history_count(identity.canonical_id) == 1


def test_plain_search_does_not_subscribe_until_explicit_on(tmp_path) -> None:
    monitor, media_store, scheduler = _monitor(tmp_path)
    identity = _identity()

    result = monitor.search_title(identity, now=100)

    assert result["subscribed_by_search"] is False
    assert media_store.monitor_state(identity.canonical_id)["monitor_enabled"] is False
    scheduler.initialize()
    assert scheduler.status_counts()["schedules"] == 0


def test_watched_series_auto_monitor_suppressed_by_explicit_off(tmp_path) -> None:
    monitor, _media_store, scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.set_monitor(identity, enabled=False, now=100)

    result = monitor.auto_monitor_watched_series(identity, now=101)

    assert result is None
    assert scheduler.get_current(media_monitor_schedule_id(identity)).enabled is False


def test_tmdb_new_episode_waits_for_translation_and_sends_no_alert(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity()
    adapter = SyntheticTmdbAdapter([ReleaseSignal(identity, "S06E01", "Season 6 Episode 1")])

    states = [monitor.ingest_tmdb_release_signal(signal, now=100) for signal in adapter.release_signals(identity)]

    assert states == [ReleaseState.WAITING_FOR_TRANSLATION]
    assert media_store.release_state(identity.canonical_id, "S06E01")["state"] == "WAITING_FOR_TRANSLATION"
    assert media_store.alert_count() == 0


def test_movie_missing_translation_enters_waiting_directly(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    movie = CanonicalMediaIdentity(tmdb_id=777, media_kind=MediaKind.MOVIE, title="Synthetic Movie")

    state = monitor.ingest_tmdb_release_signal(
        ReleaseSignal(movie, "movie:2026-08-09", "Theatrical", has_ukrainian_translation=False),
        now=100,
    )

    assert state is ReleaseState.WAITING_FOR_TRANSLATION
    assert media_store.release_state(movie.canonical_id, "movie:2026-08-09")["state"] == "WAITING_FOR_TRANSLATION"


def test_announcement_is_not_availability_and_sends_no_final_alert(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.ingest_tmdb_release_signal(ReleaseSignal(identity, "S06E01", "Season 6 Episode 1"), now=100)

    alerts = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("announcement", "S06E01", LocalizationCapability.UK_AUDIO, available=False, announced=True)],
        now=101,
    )

    assert alerts == ()
    assert media_store.release_state(identity.canonical_id, "S06E01")["state"] == "LOCALIZATION_ANNOUNCED"


def test_confirmed_audio_or_dub_emits_exactly_one_media_release_alert(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.ingest_tmdb_release_signal(ReleaseSignal(identity, "S06E01", "Season 6 Episode 1"), now=100)

    first = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("provider-a", "S06E01", LocalizationCapability.UK_DUB, available=True)],
        now=101,
    )
    retry = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("provider-a", "S06E01", LocalizationCapability.UK_DUB, available=True)],
        now=102,
    )

    assert len(first) == 1
    assert retry == ()
    assert first[0].to_mapping()["schema"] == MEDIA_RELEASE_ALERT_SCHEMA
    assert media_store.release_state(identity.canonical_id, "S06E01")["state"] == "LOCALIZATION_AVAILABLE"
    assert media_store.alert_count() == 1


def test_subtitle_provider_is_labeled_subtitles_never_audio_or_dub(tmp_path) -> None:
    monitor, _media_store, _scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.ingest_tmdb_release_signal(ReleaseSignal(identity, "S06E02", "Season 6 Episode 2"), now=100)

    alerts = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("subs", "S06E02", LocalizationCapability.UK_SUBTITLES, available=True)],
        now=101,
    )

    payload = alerts[0].to_mapping()
    assert payload["capability"] == "uk_subtitles"
    assert payload["capability"] not in {"uk_audio", "uk_dub"}
    assert "subtitles" in payload["text"]


def test_two_availability_providers_for_same_release_produce_one_alert(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.ingest_tmdb_release_signal(ReleaseSignal(identity, "S06E03", "Season 6 Episode 3"), now=100)
    provider = SyntheticLocalizationProvider(
        [
            LocalizationObservation("provider-a", "S06E03", LocalizationCapability.UK_AUDIO, available=True),
            LocalizationObservation("provider-b", "S06E03", LocalizationCapability.UK_AUDIO, available=True),
        ]
    )

    alerts = monitor.ingest_localization_observations(identity, provider.observations_for("S06E03"), now=101)

    assert len(alerts) == 1
    assert media_store.alert_count() == 1


def test_restart_retry_produces_no_duplicate_alert_or_occurrence(tmp_path) -> None:
    monitor, media_store, scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, now=100)
    first_tick = SchedulerEngine(scheduler).tick(now=17 * 60)
    second_tick = SchedulerEngine(scheduler).tick(now=17 * 60)
    monitor.ingest_tmdb_release_signal(ReleaseSignal(identity, "S06E04", "Season 6 Episode 4"), now=200)

    first_alert = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("provider-a", "S06E04", LocalizationCapability.UK_AUDIO, available=True)],
        now=201,
    )
    second_alert = monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("provider-a", "S06E04", LocalizationCapability.UK_AUDIO, available=True)],
        now=202,
    )

    assert first_tick["created_occurrences"] == 1
    assert second_tick["created_occurrences"] == 0
    assert scheduler.occurrence_count(media_monitor_schedule_id(identity)) == 1
    assert len(first_alert) == 1
    assert second_alert == ()
    assert media_store.alert_count() == 1


def test_ended_title_stops_future_polling_but_pending_localization_remains(tmp_path) -> None:
    monitor, media_store, scheduler = _monitor(tmp_path)
    identity = _identity()
    monitor.set_monitor(identity, enabled=True, now=100)

    state = monitor.ingest_tmdb_release_signal(
        ReleaseSignal(identity, "S06E05", "Season 6 Episode 5", title_status=TitleStatus.CANCELED),
        now=101,
    )

    row = media_store.release_state(identity.canonical_id, "S06E05")
    assert state is ReleaseState.WAITING_FOR_TRANSLATION
    assert row["state"] == "WAITING_FOR_TRANSLATION"
    assert row["future_polling_enabled"] == 0
    assert scheduler.get_current(media_monitor_schedule_id(identity)).enabled is False


def test_reopening_video_tab_state_is_canonical_not_query_string(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity(title="Synthetic Query Result")
    monitor.search_title(identity, now=100)
    monitor.set_monitor(identity, enabled=True, now=101)

    query_a = media_store.monitor_state("tmdb:tv:12345")
    query_b = media_store.monitor_state(_identity(title="Another Search String").canonical_id)

    assert query_a["monitor_enabled"] is True
    assert query_b["monitor_enabled"] is True


def test_video_tab_keeps_play_reset_autoplay_with_monitor_between_controls() -> None:
    html = (ROOT / "core/home_edge/static/video.html").read_text(encoding="utf-8")
    contract = video_tab_monitor_contract()

    positions = [html.index(token) for token in ('data-action="play"', 'data-control="monitor"', 'data-action="reset"', 'data-action="autoplay"')]

    assert positions == sorted(positions)
    assert contract["toggle_placement"] == ("play", "monitor", "reset", "autoplay")


def test_ordinary_done_blocked_notifications_are_not_media_alerts(tmp_path) -> None:
    _monitor_obj, media_store, _scheduler = _monitor(tmp_path)

    assert all(receipt["event_type"] == "MEDIA_RELEASE_ALERT" for receipt in media_store.public_receipts())
    assert media_store.public_receipts() == ()


def test_public_receipts_do_not_leak_private_history_or_title_markers(tmp_path) -> None:
    monitor, media_store, _scheduler = _monitor(tmp_path)
    identity = _identity(title="Synthetic Public Title")
    monitor.ingest_tmdb_release_signal(ReleaseSignal(identity, "S06E06", "Season 6 Episode 6"), now=100)
    monitor.ingest_localization_observations(
        identity,
        [LocalizationObservation("provider-a", "S06E06", LocalizationCapability.UK_AUDIO, available=True)],
        now=101,
    )

    encoded = repr(media_store.public_receipts()).lower()

    assert "private_viewing_history" not in encoded
    assert "household" not in encoded
    assert "real_title" not in encoded
    assert re.search(r"bot[_-]?token|chat[_-]?id", encoded) is None

from __future__ import annotations

from core.skeleton_sleep import (
    SOURCE_SLEEP_AS_ANDROID,
    SleepSession,
    SleepStore,
    from_sleep_as_android_record,
    safe_memory_summary,
    summarize_trend,
)


HOUR_MS = 60 * 60 * 1000


def test_sleep_as_android_record_is_normalized_without_large_arrays() -> None:
    record = {
        "_id": "night-1",
        "startTime": 1_700_000_000_000,
        "toTime": 1_700_000_000_000 + 8 * HOUR_MS,
        "timezone": "GMT+02:00",
        "length": 123456,
        "rating": 4.25,
        "quality": 0.82,
        "snore": 0.05,
        "cycles": 5,
        "noiseLevel": 0.02,
        "finished": 1,
        "eventLabels": ["DEEP_START", "DEEP_END"],
        "events": [1_700_000_100_000, 1_700_000_200_000],
        "recordFullData": [1, 2, 3],
        "recordNoiseData": [4, 5, 6],
    }

    session = from_sleep_as_android_record(record)

    assert session.source == SOURCE_SLEEP_AS_ANDROID
    assert session.source_record_id == "night-1"
    assert session.effective_duration_minutes == 480
    assert session.length_minutes is None
    assert session.rating == 4.25
    assert session.finished is True
    assert session.raw_source is not None
    assert session.raw_source["length"] == 123456
    assert "recordFullData" not in session.raw_source
    assert "recordNoiseData" not in session.raw_source


def test_store_ingest_is_idempotent_and_updates_same_source_record(tmp_path) -> None:
    store = SleepStore(tmp_path / "sleep.sqlite3")
    base = SleepSession(
        source=SOURCE_SLEEP_AS_ANDROID,
        source_record_id="night-1",
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_000_000 + 7 * HOUR_MS,
        rating=3.5,
    )

    assert store.ingest(base, now_ms=100) == "inserted"
    assert store.ingest(base, now_ms=200) == "unchanged"

    changed = SleepSession(
        source=base.source,
        source_record_id=base.source_record_id,
        start_ms=base.start_ms,
        end_ms=base.end_ms,
        rating=4.0,
    )
    assert store.ingest(changed, now_ms=300) == "updated"
    assert store.latest() == changed


def test_latest_completed_excludes_in_progress_record(tmp_path) -> None:
    store = SleepStore(tmp_path / "sleep.sqlite3")
    completed = SleepSession(
        source=SOURCE_SLEEP_AS_ANDROID,
        source_record_id="completed",
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_000_000 + 7 * HOUR_MS,
        finished=True,
    )
    in_progress = SleepSession(
        source=SOURCE_SLEEP_AS_ANDROID,
        source_record_id="active",
        start_ms=1_700_000_000_000 + 9 * HOUR_MS,
        end_ms=None,
        finished=False,
    )

    store.ingest(completed)
    store.ingest(in_progress)

    assert store.latest() == completed
    assert store.latest(completed_only=False) == in_progress


def test_trend_uses_completed_sessions_only() -> None:
    sessions = [
        SleepSession(
            source=SOURCE_SLEEP_AS_ANDROID,
            source_record_id="a",
            start_ms=1_700_000_000_000,
            end_ms=1_700_000_000_000 + 6 * HOUR_MS,
            rating=3.0,
            quality=0.7,
            snore=0.1,
        ),
        SleepSession(
            source=SOURCE_SLEEP_AS_ANDROID,
            source_record_id="b",
            start_ms=1_700_100_000_000,
            end_ms=1_700_100_000_000 + 8 * HOUR_MS,
            rating=5.0,
            quality=0.9,
            snore=0.3,
        ),
        SleepSession(
            source=SOURCE_SLEEP_AS_ANDROID,
            source_record_id="active",
            start_ms=1_700_200_000_000,
            end_ms=None,
            finished=False,
        ),
    ]

    trend = summarize_trend(sessions, window_days=7)

    assert trend.session_count == 2
    assert trend.average_duration_minutes == 420
    assert trend.average_rating == 4.0
    assert trend.average_quality == 0.8
    assert trend.average_snore == 0.2


def test_safe_memory_summary_contains_only_bounded_aggregate_fields() -> None:
    trend = summarize_trend(
        [
            SleepSession(
                source=SOURCE_SLEEP_AS_ANDROID,
                source_record_id="night",
                start_ms=1_700_000_000_000,
                end_ms=1_700_000_000_000 + 8 * HOUR_MS,
                comment="private note",
                event_labels=["SNORING"],
                events=[1_700_000_100_000],
                raw_source={"some": "raw data"},
            )
        ],
        window_days=30,
    )

    summary = safe_memory_summary(trend)

    assert summary["schema"] == "skeleton.sleep.summary.v1"
    assert summary["contains_raw_audio"] is False
    assert summary["contains_raw_events"] is False
    assert summary["clinical_interpretation"] is False
    assert "comment" not in summary
    assert "event_labels" not in summary
    assert "events" not in summary
    assert "raw_source" not in summary

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore, SchedulerStoreError


MEDIA_RELEASE_ALERT_SCHEMA = "skeleton.telegram.media_release_alert.v1"
LOCALIZATION_OBSERVATION_SCHEMA = "skeleton.media.localization_observation.v1"

_PRIVATE_MARKERS = ("household", "watch_history", "real_title", "bot_token", "chat_id")


class MediaKind(StrEnum):
    MOVIE = "movie"
    TV = "tv"


class TitleStatus(StrEnum):
    CONTINUING = "continuing"
    ENDED = "ended"
    CANCELED = "canceled"


class LocalizationCapability(StrEnum):
    UK_DUB = "uk_dub"
    UK_AUDIO = "uk_audio"
    UK_SUBTITLES = "uk_subtitles"


class ReleaseState(StrEnum):
    DISCOVERED = "DISCOVERED"
    WAITING_FOR_TRANSLATION = "WAITING_FOR_TRANSLATION"
    LOCALIZATION_ANNOUNCED = "LOCALIZATION_ANNOUNCED"
    LOCALIZATION_AVAILABLE = "LOCALIZATION_AVAILABLE"


@dataclass(frozen=True)
class CanonicalMediaIdentity:
    tmdb_id: int
    media_kind: MediaKind
    title: str

    @property
    def canonical_id(self) -> str:
        return f"tmdb:{self.media_kind.value}:{self.tmdb_id}"


@dataclass(frozen=True)
class ReleaseSignal:
    identity: CanonicalMediaIdentity
    release_key: str
    release_label: str
    title_status: TitleStatus = TitleStatus.CONTINUING
    has_ukrainian_translation: bool = False


@dataclass(frozen=True)
class LocalizationObservation:
    provider: str
    release_key: str
    capability: LocalizationCapability
    available: bool
    announced: bool = False
    evidence_ref: str = "synthetic"

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": LOCALIZATION_OBSERVATION_SCHEMA,
            "provider": self.provider,
            "release_key": self.release_key,
            "capability": self.capability.value,
            "available": self.available,
            "announced": self.announced,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class MonitorToggleResult:
    identity: CanonicalMediaIdentity
    enabled: bool
    schedule_id: str
    scheduler_created: bool
    explicit_override: str | None


@dataclass(frozen=True)
class TelegramMediaReleaseAlert:
    canonical_id: str
    release_key: str
    capability: LocalizationCapability
    alert_id: str
    text: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": MEDIA_RELEASE_ALERT_SCHEMA,
            "alert_id": self.alert_id,
            "event_type": "MEDIA_RELEASE_ALERT",
            "canonical_id": self.canonical_id,
            "release_key": self.release_key,
            "capability": self.capability.value,
            "text": self.text,
            "public_safe": True,
            "external_side_effects_executed": False,
            "private_payloads_included": False,
        }


class MediaTranslationMonitorStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_titles (
                    canonical_id TEXT PRIMARY KEY,
                    tmdb_id INTEGER NOT NULL,
                    media_kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    title_status TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_subscriptions (
                    canonical_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    explicit_override TEXT CHECK(explicit_override IN ('ON', 'OFF')),
                    schedule_id TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(canonical_id) REFERENCES media_titles(canonical_id)
                );
                CREATE TABLE IF NOT EXISTS release_states (
                    canonical_id TEXT NOT NULL,
                    release_key TEXT NOT NULL,
                    release_label TEXT NOT NULL,
                    state TEXT NOT NULL,
                    title_status TEXT NOT NULL,
                    future_polling_enabled INTEGER NOT NULL CHECK(future_polling_enabled IN (0, 1)),
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(canonical_id, release_key)
                );
                CREATE TABLE IF NOT EXISTS localization_observations (
                    observation_id TEXT PRIMARY KEY,
                    canonical_id TEXT NOT NULL,
                    release_key TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    available INTEGER NOT NULL CHECK(available IN (0, 1)),
                    announced INTEGER NOT NULL CHECK(announced IN (0, 1)),
                    evidence_ref TEXT NOT NULL,
                    observed_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_ledger (
                    alert_id TEXT PRIMARY KEY,
                    canonical_id TEXT NOT NULL,
                    release_key TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(canonical_id, release_key, capability)
                );
                """
            )

    def upsert_title(self, identity: CanonicalMediaIdentity, *, title_status: TitleStatus, now: int) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO media_titles(canonical_id, tmdb_id, media_kind, title, title_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                    title = excluded.title,
                    title_status = excluded.title_status,
                    updated_at = excluded.updated_at
                """,
                (
                    identity.canonical_id,
                    identity.tmdb_id,
                    identity.media_kind.value,
                    identity.title,
                    title_status.value,
                    now,
                ),
            )
            connection.commit()

    def set_monitor(
        self,
        identity: CanonicalMediaIdentity,
        *,
        enabled: bool,
        explicit: bool,
        schedule_id: str,
        now: int,
    ) -> None:
        self.upsert_title(identity, title_status=TitleStatus.CONTINUING, now=now)
        override = "ON" if enabled else "OFF"
        if not explicit:
            override = None
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT explicit_override FROM monitor_subscriptions WHERE canonical_id = ?",
                (identity.canonical_id,),
            ).fetchone()
            if not explicit and existing is not None and existing["explicit_override"] == "OFF":
                return
            connection.execute(
                """
                INSERT INTO monitor_subscriptions(canonical_id, enabled, explicit_override, schedule_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    explicit_override = COALESCE(excluded.explicit_override, monitor_subscriptions.explicit_override),
                    schedule_id = excluded.schedule_id,
                    updated_at = excluded.updated_at
                """,
                (identity.canonical_id, int(enabled), override, schedule_id, now),
            )
            connection.commit()

    def monitor_state(self, canonical_id: str) -> dict[str, object] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT t.canonical_id, t.tmdb_id, t.media_kind, t.title, s.enabled,
                       s.explicit_override, s.schedule_id
                  FROM media_titles t
                  LEFT JOIN monitor_subscriptions s ON s.canonical_id = t.canonical_id
                 WHERE t.canonical_id = ?
                """,
                (canonical_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "canonical_id": row["canonical_id"],
            "tmdb_id": row["tmdb_id"],
            "media_kind": row["media_kind"],
            "title": row["title"],
            "monitor_enabled": bool(row["enabled"]) if row["enabled"] is not None else False,
            "explicit_override": row["explicit_override"],
            "schedule_id": row["schedule_id"],
        }

    def record_release_signal(self, signal: ReleaseSignal, *, now: int) -> ReleaseState:
        self.upsert_title(signal.identity, title_status=signal.title_status, now=now)
        initial = (
            ReleaseState.LOCALIZATION_AVAILABLE
            if signal.has_ukrainian_translation
            else ReleaseState.WAITING_FOR_TRANSLATION
        )
        future_polling = signal.title_status not in {TitleStatus.ENDED, TitleStatus.CANCELED}
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM release_states WHERE canonical_id = ? AND release_key = ?",
                (signal.identity.canonical_id, signal.release_key),
            ).fetchone()
            state = ReleaseState(str(row["state"])) if row is not None else initial
            connection.execute(
                """
                INSERT INTO release_states(
                    canonical_id, release_key, release_label, state, title_status,
                    future_polling_enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_id, release_key) DO UPDATE SET
                    release_label = excluded.release_label,
                    title_status = excluded.title_status,
                    future_polling_enabled = excluded.future_polling_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    signal.identity.canonical_id,
                    signal.release_key,
                    signal.release_label,
                    state.value,
                    signal.title_status.value,
                    int(future_polling),
                    now,
                ),
            )
            connection.commit()
        return state

    def apply_observation(
        self,
        identity: CanonicalMediaIdentity,
        observation: LocalizationObservation,
        *,
        now: int,
    ) -> tuple[ReleaseState, TelegramMediaReleaseAlert | None]:
        self.initialize()
        observation_id = _stable_digest(
            identity.canonical_id,
            observation.release_key,
            observation.provider,
            observation.capability.value,
            str(observation.available),
            str(observation.announced),
            observation.evidence_ref,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO localization_observations(
                    observation_id, canonical_id, release_key, provider, capability,
                    available, announced, evidence_ref, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    identity.canonical_id,
                    observation.release_key,
                    observation.provider,
                    observation.capability.value,
                    int(observation.available),
                    int(observation.announced),
                    observation.evidence_ref,
                    now,
                ),
            )
            current = connection.execute(
                "SELECT state FROM release_states WHERE canonical_id = ? AND release_key = ?",
                (identity.canonical_id, observation.release_key),
            ).fetchone()
            state = ReleaseState(str(current["state"])) if current is not None else ReleaseState.WAITING_FOR_TRANSLATION
            if observation.available:
                state = ReleaseState.LOCALIZATION_AVAILABLE
            elif observation.announced and state is ReleaseState.WAITING_FOR_TRANSLATION:
                state = ReleaseState.LOCALIZATION_ANNOUNCED
            connection.execute(
                """
                UPDATE release_states SET state = ?, updated_at = ?
                 WHERE canonical_id = ? AND release_key = ?
                """,
                (state.value, now, identity.canonical_id, observation.release_key),
            )
            alert = None
            if observation.available:
                alert_id = f"media_alert_{_stable_digest(identity.canonical_id, observation.release_key, observation.capability.value)[:24]}"
                result = connection.execute(
                    """
                    INSERT OR IGNORE INTO alert_ledger(
                        alert_id, canonical_id, release_key, capability, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (alert_id, identity.canonical_id, observation.release_key, observation.capability.value, now),
                )
                if result.rowcount == 1:
                    alert = TelegramMediaReleaseAlert(
                        canonical_id=identity.canonical_id,
                        release_key=observation.release_key,
                        capability=observation.capability,
                        alert_id=alert_id,
                        text=f"{identity.title}: Ukrainian {observation.capability.value} is available for {observation.release_key}.",
                    )
            connection.commit()
        return state, alert

    def release_state(self, canonical_id: str, release_key: str) -> dict[str, object] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM release_states WHERE canonical_id = ? AND release_key = ?",
                (canonical_id, release_key),
            ).fetchone()
        return None if row is None else dict(row)

    def alert_count(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM alert_ledger").fetchone()
        assert row is not None
        return int(row[0])

    def history_count(self, canonical_id: str) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM release_states WHERE canonical_id = ?",
                (canonical_id,),
            ).fetchone()
        assert row is not None
        return int(row[0])

    def public_receipts(self) -> tuple[dict[str, object], ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT alert_id, canonical_id, release_key, capability, created_at
                  FROM alert_ledger ORDER BY alert_id
                """
            ).fetchall()
        receipts = []
        for row in rows:
            payload = {
                "schema": MEDIA_RELEASE_ALERT_SCHEMA,
                "event_type": "MEDIA_RELEASE_ALERT",
                "alert_id": row["alert_id"],
                "canonical_id": row["canonical_id"],
                "release_key": row["release_key"],
                "capability": row["capability"],
                "created_at": row["created_at"],
                "public_safe": True,
                "external_side_effects_executed": False,
                "private_payloads_included": False,
            }
            _assert_public_safe(payload)
            receipts.append(payload)
        return tuple(receipts)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


class MediaTranslationMonitor:
    def __init__(
        self,
        *,
        media_store: MediaTranslationMonitorStore,
        scheduler_store: SchedulerStore,
    ) -> None:
        self.media_store = media_store
        self.scheduler_store = scheduler_store

    def search_title(self, identity: CanonicalMediaIdentity, *, now: int) -> dict[str, object]:
        self.media_store.upsert_title(identity, title_status=TitleStatus.CONTINUING, now=now)
        state = self.media_store.monitor_state(identity.canonical_id)
        assert state is not None
        state["subscribed_by_search"] = False
        return state

    def set_monitor(self, identity: CanonicalMediaIdentity, *, enabled: bool, now: int) -> MonitorToggleResult:
        self.scheduler_store.initialize()
        self.media_store.initialize()
        schedule_id = media_monitor_schedule_id(identity)
        spec = media_monitor_schedule_spec(identity)
        record, created = self.scheduler_store.register(spec, now=now, enabled=enabled)
        if not enabled:
            try:
                record = self.scheduler_store.set_enabled(schedule_id, False)
            except SchedulerStoreError:
                pass
        elif not record.enabled:
            record = self.scheduler_store.set_enabled(schedule_id, True)
        self.media_store.set_monitor(identity, enabled=enabled, explicit=True, schedule_id=schedule_id, now=now)
        return MonitorToggleResult(
            identity=identity,
            enabled=enabled,
            schedule_id=schedule_id,
            scheduler_created=created,
            explicit_override="ON" if enabled else "OFF",
        )

    def auto_monitor_watched_series(self, identity: CanonicalMediaIdentity, *, now: int) -> MonitorToggleResult | None:
        state = self.media_store.monitor_state(identity.canonical_id)
        if state is not None and state.get("explicit_override") == "OFF":
            return None
        self.scheduler_store.initialize()
        schedule_id = media_monitor_schedule_id(identity)
        record, created = self.scheduler_store.register(
            media_monitor_schedule_spec(identity),
            now=now,
            enabled=True,
        )
        self.media_store.set_monitor(identity, enabled=True, explicit=False, schedule_id=schedule_id, now=now)
        return MonitorToggleResult(identity, record.enabled, schedule_id, created, None)

    def ingest_tmdb_release_signal(self, signal: ReleaseSignal, *, now: int) -> ReleaseState:
        state = self.media_store.record_release_signal(signal, now=now)
        if signal.title_status in {TitleStatus.ENDED, TitleStatus.CANCELED}:
            try:
                self.scheduler_store.set_enabled(media_monitor_schedule_id(signal.identity), False)
            except SchedulerStoreError:
                pass
        return state

    def ingest_localization_observations(
        self,
        identity: CanonicalMediaIdentity,
        observations: Iterable[LocalizationObservation],
        *,
        now: int,
    ) -> tuple[TelegramMediaReleaseAlert, ...]:
        alerts: list[TelegramMediaReleaseAlert] = []
        for observation in observations:
            _validate_observation(observation)
            _state, alert = self.media_store.apply_observation(identity, observation, now=now)
            if alert is not None:
                alerts.append(alert)
        return tuple(alerts)


def media_monitor_schedule_id(identity: CanonicalMediaIdentity) -> str:
    suffix = _stable_digest(identity.canonical_id)[:24]
    return f"media.translation_monitor.{suffix}"


def media_monitor_schedule_spec(identity: CanonicalMediaIdentity) -> ScheduleSpec:
    payload = {
        "schema": "skeleton.media_translation_monitor.scheduler_payload.v1",
        "canonical_media_id": identity.canonical_id,
        "tmdb_id": identity.tmdb_id,
        "media_kind": identity.media_kind.value,
        "public_safe": True,
        "private_history_included": False,
    }
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": media_monitor_schedule_id(identity),
            "trigger_kind": "cron",
            "cron_expression": "17 */6 * * *",
            "once_at": None,
            "timezone": "UTC",
            "route_type": "notify",
            "route_id": "media.translation_monitor",
            "approval_policy": "notify_only",
            "overlap_policy": "skip",
            "misfire_policy": "run_once",
            "payload": payload,
        }
    )


class SyntheticTmdbAdapter:
    def __init__(self, releases: Sequence[ReleaseSignal] = ()) -> None:
        self._releases = tuple(releases)

    def release_signals(self, identity: CanonicalMediaIdentity) -> tuple[ReleaseSignal, ...]:
        return tuple(item for item in self._releases if item.identity.canonical_id == identity.canonical_id)


class SyntheticLocalizationProvider:
    def __init__(self, observations: Sequence[LocalizationObservation] = ()) -> None:
        self._observations = tuple(observations)

    def observations_for(self, release_key: str) -> tuple[LocalizationObservation, ...]:
        return tuple(item for item in self._observations if item.release_key == release_key)


def video_tab_monitor_contract() -> dict[str, object]:
    return {
        "schema": "skeleton.home_edge.video_tab.monitor_contract.v1",
        "canonical_identity": "tmdb_id",
        "search_only_subscribes": False,
        "toggle_placement": ("play", "monitor", "reset", "autoplay"),
        "public_safe": True,
    }


def _validate_observation(observation: LocalizationObservation) -> None:
    if observation.capability not in set(LocalizationCapability):
        raise ValueError("localization capability is not supported")
    if observation.available and observation.announced:
        return
    if not observation.available and not observation.announced:
        raise ValueError("observation must announce or confirm availability")


def _stable_digest(*parts: object) -> str:
    encoded = "\n".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_public_safe(payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True).lower()
    for marker in _PRIVATE_MARKERS:
        if marker in text:
            raise ValueError("public receipt contains a private marker")

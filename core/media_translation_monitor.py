from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Protocol

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE


MEDIA_MONITOR_ROUTE_TYPE = "workflow"
MEDIA_MONITOR_ROUTE_ID = "media_translation_monitor"
MEDIA_MONITOR_CAPABILITY = "media:translation_monitor"
MEDIA_RELEASE_ALERT_SCHEMA = "skeleton.media.release_alert.v1"
MEDIA_MONITOR_RECEIPT_SCHEMA = "skeleton.media.translation_monitor_receipt.v1"
MEDIA_MONITOR_STATE_SCHEMA = "skeleton.media.translation_monitor_state.v1"

DISCOVERY_CRON = "17 3 * * *"
LOCALIZATION_CRON = "17 */6 * * *"
RELEASE_EXPIRY_SECONDS = 180 * 24 * 60 * 60

_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._:-]+")


class MediaKind(StrEnum):
    MOVIE = "movie"
    TV = "tv"


class TitleStatus(StrEnum):
    ACTIVE = "active"
    CONTINUING = "continuing"
    ENDED = "ended"
    CANCELED = "canceled"


class LocalizationCapability(StrEnum):
    UK_DUB = "uk_dub"
    UK_AUDIO = "uk_audio"
    UK_SUBTITLES = "uk_subtitles"


class EvidenceStrength(StrEnum):
    HIGH = "high"
    WEAK = "weak"
    ANNOUNCEMENT = "announcement"


class ReleaseState(StrEnum):
    WAITING_FOR_TRANSLATION = "WAITING_FOR_TRANSLATION"
    LOCALIZATION_ANNOUNCED = "LOCALIZATION_ANNOUNCED"
    LOCALIZATION_AVAILABLE = "LOCALIZATION_AVAILABLE"
    NOTIFIED = "NOTIFIED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class MediaIdentity:
    work_id: str
    tmdb_id: int
    media_kind: MediaKind
    title: str

    @property
    def stable_key(self) -> str:
        return media_stable_key(self.work_id, self.media_kind, self.tmdb_id)

    def to_mapping(self) -> dict[str, object]:
        return {
            "work_id": self.work_id,
            "tmdb_id": self.tmdb_id,
            "media_kind": self.media_kind.value,
            "title": self.title,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "MediaIdentity":
        return cls(
            work_id=_safe_text(value.get("work_id"), "work_id"),
            tmdb_id=_positive_int(value.get("tmdb_id"), "tmdb_id"),
            media_kind=MediaKind(_safe_text(value.get("media_kind"), "media_kind")),
            title=_safe_text(value.get("title"), "title", max_length=200),
        )


@dataclass(frozen=True)
class ReleaseSignal:
    identity: MediaIdentity
    release_key: str
    release_label: str
    title_status: TitleStatus = TitleStatus.CONTINUING
    has_required_localization: bool = False
    baseline: bool = False


@dataclass(frozen=True)
class LocalizationObservation:
    provider: str
    release_key: str
    capability: LocalizationCapability
    available: bool
    strength: EvidenceStrength = EvidenceStrength.HIGH
    evidence_ref: str = "public-metadata"


class NotificationSender(Protocol):
    def __call__(self, message: str, reply_markup: dict[str, Any] | None = None) -> None: ...


class MediaTranslationStateStore:
    """Small JSON ledger for monitor state where no repository media authority exists."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        with self.path.open("r", encoding="utf-8") as handle:
            decoded = json.load(handle)
        if not isinstance(decoded, dict) or decoded.get("schema") != MEDIA_MONITOR_STATE_SCHEMA:
            return _empty_state()
        return decoded

    def save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(
            state,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    def update(self, mutator: Callable[[dict[str, Any]], Any]) -> Any:
        state = self.load()
        result = mutator(state)
        self.save(state)
        return result


class MediaTranslationMonitor:
    def __init__(
        self,
        *,
        state_store: MediaTranslationStateStore,
        scheduler_store: SchedulerStore,
        notifier: NotificationSender | None = None,
        providers: Sequence[object] = (),
    ) -> None:
        self.state_store = state_store
        self.scheduler_store = scheduler_store
        self.notifier = notifier
        self.providers = tuple(providers)

    def set_monitor(
        self,
        identity: MediaIdentity,
        *,
        enabled: bool,
        explicit: bool,
        now: int,
        title_status: TitleStatus = TitleStatus.CONTINUING,
    ) -> dict[str, object]:
        self.scheduler_store.initialize()

        def mutate(state: dict[str, Any]) -> dict[str, object]:
            titles = state["titles"]
            record = titles.setdefault(identity.stable_key, _title_record(identity))
            record["identity"] = identity.to_mapping()
            record["title_status"] = title_status.value
            existing_override = record.get("explicit_override")
            if not explicit and existing_override == "OFF":
                enabled_value = False
            else:
                enabled_value = bool(enabled)
                if explicit:
                    record["explicit_override"] = "ON" if enabled else "OFF"
            record["monitor_enabled"] = enabled_value
            record["updated_at"] = now
            return {
                "stable_key": identity.stable_key,
                "monitor_enabled": enabled_value,
                "explicit_override": record.get("explicit_override"),
            }

        result = self.state_store.update(mutate)
        self._sync_schedules(identity, bool(result["monitor_enabled"]), now=now, title_status=title_status)
        return result

    def monitor_state(self, identity: MediaIdentity) -> dict[str, object]:
        state = self.state_store.load()
        record = state["titles"].get(identity.stable_key)
        if record is None:
            return {"stable_key": identity.stable_key, "monitor_enabled": False}
        return {
            "stable_key": identity.stable_key,
            "monitor_enabled": bool(record.get("monitor_enabled")),
            "explicit_override": record.get("explicit_override"),
            "title_status": record.get("title_status"),
        }

    def record_failed_localized_search(
        self,
        signal: ReleaseSignal,
        *,
        now: int,
    ) -> dict[str, object]:
        monitor = self.set_monitor(signal.identity, enabled=True, explicit=False, now=now)
        state = self.ingest_release_signal(signal, now=now)
        return {
            "subscribed": monitor["monitor_enabled"],
            "release_state": state.value,
            "localization_schedule_id": localization_schedule_id(signal.identity),
        }

    def record_browse_search(self, identity: MediaIdentity, *, now: int) -> dict[str, object]:
        self.state_store.update(
            lambda state: state["titles"].setdefault(identity.stable_key, _title_record(identity))
        )
        return {"stable_key": identity.stable_key, "subscribed": False, "at": now}

    def auto_follow_watched_series(self, identity: MediaIdentity, *, now: int) -> dict[str, object] | None:
        current = self.monitor_state(identity)
        if current.get("explicit_override") == "OFF":
            self._sync_schedules(identity, False, now=now, title_status=TitleStatus.CONTINUING)
            return None
        return self.set_monitor(identity, enabled=True, explicit=False, now=now)

    def ingest_release_signal(self, signal: ReleaseSignal, *, now: int) -> ReleaseState:
        initial = (
            ReleaseState.LOCALIZATION_AVAILABLE
            if signal.has_required_localization
            else ReleaseState.WAITING_FOR_TRANSLATION
        )

        def mutate(state: dict[str, Any]) -> ReleaseState:
            title = state["titles"].setdefault(signal.identity.stable_key, _title_record(signal.identity))
            title["identity"] = signal.identity.to_mapping()
            title["title_status"] = signal.title_status.value
            title["updated_at"] = now
            releases = title.setdefault("releases", {})
            release = releases.setdefault(
                signal.release_key,
                {
                    "release_key": signal.release_key,
                    "release_label": signal.release_label,
                    "state": initial.value,
                    "created_at": now,
                    "updated_at": now,
                    "baseline": signal.baseline,
                },
            )
            release["release_label"] = signal.release_label
            release["baseline"] = bool(release.get("baseline")) or signal.baseline
            if release["state"] not in {ReleaseState.NOTIFIED.value, ReleaseState.LOCALIZATION_AVAILABLE.value}:
                release["state"] = initial.value
            release["updated_at"] = now
            return ReleaseState(str(release["state"]))

        state = self.state_store.update(mutate)
        if signal.title_status in {TitleStatus.ENDED, TitleStatus.CANCELED}:
            self._sync_schedules(signal.identity, True, now=now, title_status=signal.title_status)
        return state

    def ingest_localization_observations(
        self,
        identity: MediaIdentity,
        observations: Iterable[LocalizationObservation],
        *,
        now: int,
    ) -> tuple[dict[str, object], ...]:
        alerts: list[dict[str, object]] = []
        for observation in observations:
            if observation.strength == EvidenceStrength.ANNOUNCEMENT or not observation.available:
                self._mark_announced(identity, observation, now=now)
                continue
            if observation.strength == EvidenceStrength.WEAK and not self._has_corroboration(identity, observation):
                self._mark_announced(identity, observation, now=now)
                continue
            alert = self._mark_available(identity, observation, now=now)
            if alert is not None:
                alerts.append(alert)
        return tuple(alerts)

    def run_scheduler_task(self, task_packet: Mapping[str, object], *, now: int | None = None) -> dict[str, object]:
        current = int(time.time()) if now is None else now
        action = task_packet.get("action")
        identity = MediaIdentity.from_mapping(_mapping(task_packet.get("identity"), "identity"))
        if not bool(self.monitor_state(identity).get("monitor_enabled")):
            return _receipt("DONE", "MEDIA_MONITOR_DISABLED", checked=0)
        if action == "discover_future_releases":
            status = str(self.monitor_state(identity).get("title_status") or "")
            if status in {TitleStatus.ENDED.value, TitleStatus.CANCELED.value}:
                return _receipt("DONE", "TITLE_TERMINAL_DISCOVERY_DISABLED", checked=0)
            return _receipt("DONE", "DISCOVERY_ROUTE_READY", checked=0)
        if action == "check_pending_localization":
            checked = 0
            for provider in self.providers:
                observe = getattr(provider, "observations_for", None)
                if observe is None:
                    continue
                for release_key in self._waiting_release_keys(identity, now=current):
                    checked += 1
                    self.ingest_localization_observations(
                        identity,
                        observe(identity, release_key),
                        now=current,
                    )
            return _receipt("DONE", "LOCALIZATION_CHECK_DONE", checked=checked)
        return _receipt("BLOCKED", "UNKNOWN_MEDIA_MONITOR_ACTION", checked=0, accepted=False)

    def _mark_announced(
        self, identity: MediaIdentity, observation: LocalizationObservation, *, now: int
    ) -> None:
        def mutate(state: dict[str, Any]) -> None:
            release = _release_record(state, identity, observation.release_key, now)
            if release["state"] == ReleaseState.WAITING_FOR_TRANSLATION.value:
                release["state"] = ReleaseState.LOCALIZATION_ANNOUNCED.value
            release.setdefault("observations", []).append(_observation_record(observation, now))
            release["updated_at"] = now

        self.state_store.update(mutate)

    def _mark_available(
        self, identity: MediaIdentity, observation: LocalizationObservation, *, now: int
    ) -> dict[str, object] | None:
        alert_id = media_alert_id(identity, observation.release_key, observation.capability)

        def mutate(state: dict[str, Any]) -> dict[str, object] | None:
            title = state["titles"].setdefault(identity.stable_key, _title_record(identity))
            if title.get("explicit_override") == "OFF" or not title.get("monitor_enabled", False):
                return None
            release = _release_record(state, identity, observation.release_key, now)
            release.setdefault("observations", []).append(_observation_record(observation, now))
            release["state"] = ReleaseState.LOCALIZATION_AVAILABLE.value
            if release.get("baseline"):
                release["updated_at"] = now
                return None
            watch = state["watch_list"].setdefault(
                f"{identity.stable_key}:{observation.release_key}",
                {
                    "stable_key": identity.stable_key,
                    "release_key": observation.release_key,
                    "watched": False,
                    "created_at": now,
                },
            )
            watch["watched"] = bool(watch.get("watched", False))
            if alert_id in state["alert_receipts"]:
                return None
            alert = _alert_payload(identity, observation, alert_id)
            state["alert_receipts"][alert_id] = {"alert": alert, "created_at": now}
            release["state"] = ReleaseState.NOTIFIED.value
            release["updated_at"] = now
            return alert

        alert = self.state_store.update(mutate)
        if alert is not None:
            sender = self.notifier or _default_telegram_sender
            sender(str(alert["text"]), None)
        return alert

    def _has_corroboration(self, identity: MediaIdentity, observation: LocalizationObservation) -> bool:
        state = self.state_store.load()
        title = state["titles"].get(identity.stable_key, {})
        release = title.get("releases", {}).get(observation.release_key, {})
        providers = {
            item.get("provider")
            for item in release.get("observations", [])
            if item.get("available") is True and item.get("capability") == observation.capability.value
        }
        return bool(providers - {observation.provider})

    def _waiting_release_keys(self, identity: MediaIdentity, *, now: int) -> tuple[str, ...]:
        state = self.state_store.load()
        title = state["titles"].get(identity.stable_key, {})
        releases = title.get("releases", {})
        keys = []
        for key, release in releases.items():
            if release.get("state") in {
                ReleaseState.WAITING_FOR_TRANSLATION.value,
                ReleaseState.LOCALIZATION_ANNOUNCED.value,
            }:
                expires_at = release.get("expires_at")
                if not isinstance(expires_at, int) or now <= expires_at:
                    keys.append(str(key))
        return tuple(sorted(keys))

    def _sync_schedules(
        self,
        identity: MediaIdentity,
        enabled: bool,
        *,
        now: int,
        title_status: TitleStatus,
    ) -> None:
        discovery_enabled = enabled and title_status not in {TitleStatus.ENDED, TitleStatus.CANCELED}
        for spec, active in (
            (_schedule(identity, "discover_future_releases", DISCOVERY_CRON), discovery_enabled),
            (_schedule(identity, "check_pending_localization", LOCALIZATION_CRON), enabled),
        ):
            record, _ = self.scheduler_store.register(spec, now=now, enabled=active)
            if record.enabled != active:
                self.scheduler_store.set_enabled(record.spec.schedule_id, active)


def media_stable_key(work_id: str, media_kind: MediaKind, tmdb_id: int) -> str:
    return f"{_token(work_id)}:tmdb:{media_kind.value}:{_positive_int(tmdb_id, 'tmdb_id')}"


def discovery_schedule_id(identity: MediaIdentity) -> str:
    return _schedule_id(identity, "discovery")


def localization_schedule_id(identity: MediaIdentity) -> str:
    return _schedule_id(identity, "localization")


def media_alert_id(identity: MediaIdentity, release_key: str, capability: LocalizationCapability) -> str:
    digest = hashlib.sha256(
        f"{identity.stable_key}\n{release_key}\n{capability.value}".encode("utf-8")
    ).hexdigest()
    return f"media_alert_{digest[:32]}"


def media_monitor_dispatcher(*, state_path: str | Path, scheduler_store: SchedulerStore, notifier: NotificationSender | None = None, providers: Sequence[object] = ()):
    from core.shared_dispatch import DispatchRoute, SharedDispatcher

    monitor = MediaTranslationMonitor(
        state_store=MediaTranslationStateStore(state_path),
        scheduler_store=scheduler_store,
        notifier=notifier,
        providers=providers,
    )

    def handler(request):
        return monitor.run_scheduler_task(_mapping(request.payload.get("task_packet"), "task_packet"))

    return SharedDispatcher(
        {
            (MEDIA_MONITOR_ROUTE_TYPE, MEDIA_MONITOR_ROUTE_ID): DispatchRoute(
                route_type=MEDIA_MONITOR_ROUTE_TYPE,
                route_id=MEDIA_MONITOR_ROUTE_ID,
                required_capabilities=frozenset({MEDIA_MONITOR_CAPABILITY}),
                handler=handler,
            )
        }
    )


def _schedule(identity: MediaIdentity, action: str, cron: str) -> ScheduleSpec:
    kind = "discovery" if action == "discover_future_releases" else "localization"
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": _schedule_id(identity, kind),
            "trigger_kind": "cron",
            "cron_expression": cron,
            "once_at": None,
            "timezone": "UTC",
            "route_type": MEDIA_MONITOR_ROUTE_TYPE,
            "route_id": MEDIA_MONITOR_ROUTE_ID,
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": {
                "privacy_boundary": PRIVACY_PUBLIC_SAFE,
                "bounded": True,
                "approved_capabilities": [MEDIA_MONITOR_CAPABILITY],
                "requested_capabilities": [MEDIA_MONITOR_CAPABILITY],
                "task_packet": {
                    "schema": "skeleton.media.translation_monitor_task.v1",
                    "action": action,
                    "identity": identity.to_mapping(),
                    "public_safe": True,
                    "external_side_effects_allowed": action == "check_pending_localization",
                },
            },
        }
    )


def _schedule_id(identity: MediaIdentity, kind: str) -> str:
    digest = hashlib.sha256(identity.stable_key.encode("utf-8")).hexdigest()
    return f"media.monitor.{kind}.{digest[:24]}"


def _empty_state() -> dict[str, Any]:
    return {
        "schema": MEDIA_MONITOR_STATE_SCHEMA,
        "titles": {},
        "watch_list": {},
        "alert_receipts": {},
    }


def _title_record(identity: MediaIdentity) -> dict[str, Any]:
    return {
        "identity": identity.to_mapping(),
        "monitor_enabled": False,
        "explicit_override": None,
        "title_status": TitleStatus.CONTINUING.value,
        "releases": {},
    }


def _release_record(
    state: dict[str, Any], identity: MediaIdentity, release_key: str, now: int
) -> dict[str, Any]:
    title = state["titles"].setdefault(identity.stable_key, _title_record(identity))
    releases = title.setdefault("releases", {})
    return releases.setdefault(
        release_key,
        {
            "release_key": release_key,
            "release_label": release_key,
            "state": ReleaseState.WAITING_FOR_TRANSLATION.value,
            "created_at": now,
            "updated_at": now,
            "baseline": False,
            "observations": [],
        },
    )


def _observation_record(observation: LocalizationObservation, now: int) -> dict[str, object]:
    return {
        "provider": observation.provider,
        "release_key": observation.release_key,
        "capability": observation.capability.value,
        "available": observation.available,
        "strength": observation.strength.value,
        "evidence_ref": observation.evidence_ref,
        "observed_at": now,
    }


def _alert_payload(
    identity: MediaIdentity, observation: LocalizationObservation, alert_id: str
) -> dict[str, object]:
    capability = {
        LocalizationCapability.UK_DUB: "Ukrainian dub",
        LocalizationCapability.UK_AUDIO: "Ukrainian audio",
        LocalizationCapability.UK_SUBTITLES: "Ukrainian subtitles",
    }[observation.capability]
    return {
        "schema": MEDIA_RELEASE_ALERT_SCHEMA,
        "event_type": "MEDIA_RELEASE_ALERT",
        "subscription_type": "USER_SUBSCRIPTION",
        "alert_id": alert_id,
        "stable_key": identity.stable_key,
        "tmdb_id": identity.tmdb_id,
        "release_key": observation.release_key,
        "capability": observation.capability.value,
        "text": f"{identity.title}: {observation.release_key} now has {capability}. Added to watch list.",
        "public_safe": True,
        "private_payloads_included": False,
    }


def _receipt(status: str, reason: str, *, checked: int, accepted: bool = True) -> dict[str, object]:
    return {
        "schema": MEDIA_MONITOR_RECEIPT_SCHEMA,
        "status": status,
        "accepted": accepted,
        "decision": "ACCEPT" if accepted else "REJECT",
        "reason": reason,
        "checked_releases": checked,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _default_telegram_sender(message: str, reply_markup: dict[str, Any] | None = None) -> None:
    from scripts.runner_poll_github_tasks import send_telegram_notification

    send_telegram_notification(message, reply_markup)


def _safe_text(value: object, field: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"{field} must be a bounded string")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _token(value: str) -> str:
    token = _SAFE_TOKEN_RE.sub("-", value.strip())[:80].strip("-")
    if not token:
        raise ValueError("work_id must contain a safe token")
    return token

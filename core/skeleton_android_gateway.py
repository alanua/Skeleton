from __future__ import annotations

import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol


ANDROID_OBSERVATION_SCHEMA = "skeleton.android.observation.v1"
ANDROID_SNAPSHOT_SCHEMA = "skeleton.android.snapshot.v1"
ANDROID_GATEWAY_CONTRACT_VERSION = "1.1.0"
ANDROID_GATEWAY_PRIVACY_BOUNDARY = "local_first_bounded_android_observations"

_MAX_DEPTH = 6
_MAX_ITEMS = 128
_MAX_STRING_LENGTH = 512
_MAX_KEY_LENGTH = 96
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f:]{1,4}\b")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")

_SENSITIVE_TOKENS = frozenset(
    {
        "2fa",
        "address",
        "audio",
        "auth",
        "bank",
        "banking",
        "body",
        "bssid",
        "call",
        "calls",
        "clipboard",
        "contact",
        "contacts",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "file",
        "files",
        "finance",
        "financial",
        "intent",
        "intents",
        "ip",
        "lat",
        "latitude",
        "location",
        "logcat",
        "lon",
        "longitude",
        "mac",
        "message",
        "messages",
        "messenger",
        "microphone",
        "mutation",
        "mutations",
        "notification",
        "notifications",
        "password",
        "passwords",
        "photo",
        "photos",
        "sms",
        "ssid",
        "text",
        "token",
        "tokens",
    }
)

_SENSITIVE_FRAGMENTS = frozenset(
    {
        "appcontent",
        "authtoken",
        "bssid",
        "clipboard",
        "credential",
        "messagebody",
        "notificationbody",
        "password",
        "rawlogcat",
        "smsbody",
    }
)

_DEFAULT_SKELETON_STATE_ROOT = Path("~/.local/state/skeleton")
_MAX_PID = 4_194_304
_RDC_AUTH_REQUIRED_FILE = "rdc-auth-required"
_RDC_PID_FILE = "redmi-rdc-agent.pid"
_SUPERVISOR_PID_FILE = "redmi-runtime-supervisor.pid"
_SAMSUNG_ADB_WATCHDOG_STATE_FILE = "samsung-adb-watchdog.json"
_SAMSUNG_ADB_SERVER_HOST = "127.0.0.1"
_SAMSUNG_ADB_SERVER_PORT = 5037
_SAMSUNG_USB_RECOVERY_STEPS = ("reconnect_offline", "reconnect_device", "usb_reset")
_SAMSUNG_USB_MARKERS = ("samsung", "sm_", "sm-", "gt-", "gta")
_FORBIDDEN_ADB_TOKENS = frozenset(
    {
        "reboot",
        "install",
        "uninstall",
        "push",
        "pull",
        "shell",
        "tcpip",
        "kill-server",
        "start-server",
    }
)


class AndroidGatewayError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class AndroidObservationKind(StrEnum):
    BATTERY = "battery"
    STORAGE = "storage"
    RUNTIME = "runtime"
    NETWORK = "network"
    SENSOR_CAPABILITIES = "sensor_capabilities"
    STEP_ACTIVITY = "step_activity"
    SUPERVISOR = "supervisor"
    REMOTE_DESKTOP_STATE = "remote_desktop_state"


class RemoteDesktopState(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"


class AndroidObservationQuality(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AdbServerLauncher(Protocol):
    def __call__(self, command: Sequence[str], env: Mapping[str, str]) -> Any:
        ...


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    details: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_usb_transport(self) -> bool:
        return "usb" in self.details and ":" not in self.serial

    @property
    def is_network_transport(self) -> bool:
        return ":" in self.serial or self.details.get("transport") in {"tcp", "network"}

    @property
    def is_samsung_usb(self) -> bool:
        if not self.is_usb_transport:
            return False
        haystack = " ".join([self.serial, *self.details.values()]).lower()
        return any(marker in haystack for marker in _SAMSUNG_USB_MARKERS)


@dataclass(frozen=True)
class SamsungAdbWatchdogReceipt:
    status: str
    reason: str
    server_owner: str
    device_state: str
    actions: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": "skeleton.android.samsung_adb_watchdog.v1",
            "status": self.status,
            "reason": self.reason,
            "server_owner": self.server_owner,
            "device_state": self.device_state,
            "actions": list(self.actions),
            "forbidden_actions_excluded": [
                "reboot",
                "factory_reset",
                "data_clear",
                "deploy",
                "apk_install",
                "media_mode_change",
                "network_adb",
            ],
        }


@dataclass
class _WatchdogState:
    attempts: list[float] = field(default_factory=list)
    managed_server_started: bool = False


@dataclass
class SamsungAdbWatchdog:
    """Bounded repository-backed Samsung USB ADB recovery path.

    The watchdog manages exactly one local adb server on localhost:5037. It
    refuses unmanaged owners and stale network transports instead of killing
    processes or changing the tablet's mode.
    """

    adb_path: str = "adb"
    state_root: str | Path | None = None
    clock: Callable[[], float] = time.time
    run_command: Callable[[Sequence[str], float, Mapping[str, str]], subprocess.CompletedProcess[str]] | None = None
    server_launcher: AdbServerLauncher | None = None
    port_owner: Callable[[str, int], str | None] | None = None
    max_recovery_attempts: int = 3
    recovery_window_seconds: float = 300.0

    def check_and_recover(self) -> SamsungAdbWatchdogReceipt:
        state = self._load_state()
        owner = self._server_owner(state)
        if owner == "unmanaged":
            return SamsungAdbWatchdogReceipt(
                status="BLOCKED",
                reason="UNMANAGED_ADB_SERVER_ON_5037",
                server_owner=owner,
                device_state="unknown",
            )
        actions: list[str] = []
        if owner != "managed":
            self._launch_managed_server()
            state.managed_server_started = True
            actions.append("managed_server_started")
            owner = "managed"

        devices = self._list_devices()
        if any(device.is_network_transport for device in devices):
            self._save_state(state)
            return SamsungAdbWatchdogReceipt(
                status="BLOCKED",
                reason="STALE_NETWORK_ADB_FAIL_CLOSED",
                server_owner=owner,
                device_state="network_transport_present",
                actions=tuple(actions),
            )

        samsung = [device for device in devices if device.is_samsung_usb]
        online = [device for device in samsung if device.state == "device"]
        if online:
            self._save_state(_WatchdogState(managed_server_started=state.managed_server_started))
            return SamsungAdbWatchdogReceipt(
                status="DONE",
                reason="SAMSUNG_USB_ADB_HEALTHY",
                server_owner=owner,
                device_state="usb_online",
                actions=tuple(actions),
            )

        if not self._recovery_budget_available(state):
            self._save_state(state)
            return SamsungAdbWatchdogReceipt(
                status="BLOCKED",
                reason="RECOVERY_BUDGET_EXHAUSTED",
                server_owner=owner,
                device_state="usb_unavailable",
                actions=tuple(actions),
            )

        actions.extend(self._recover(samsung[0].serial if samsung else None))
        state.attempts.append(self.clock())
        self._save_state(state)
        return SamsungAdbWatchdogReceipt(
            status="RECOVERING",
            reason="SAMSUNG_USB_RECOVERY_ESCALATED",
            server_owner=owner,
            device_state=samsung[0].state if samsung else "missing",
            actions=tuple(actions),
        )

    def _recover(self, serial: str | None) -> list[str]:
        actions: list[str] = []
        for step in _SAMSUNG_USB_RECOVERY_STEPS:
            command = self._recovery_command(step, serial)
            if command is None:
                continue
            self._safe_adb(command, timeout=10.0)
            actions.append(step)
        return actions

    def _recovery_command(self, step: str, serial: str | None) -> list[str] | None:
        if step == "reconnect_offline":
            return ["reconnect", "offline"]
        if step == "reconnect_device":
            return ["reconnect", "device"]
        if step == "usb_reset" and serial:
            return ["-s", serial, "usb"]
        return None

    def _launch_managed_server(self) -> None:
        command = [
            self.adb_path,
            "-L",
            f"tcp:{_SAMSUNG_ADB_SERVER_HOST}:{_SAMSUNG_ADB_SERVER_PORT}",
            "nodaemon",
            "server",
        ]
        env = self._adb_env()
        if self.server_launcher is not None:
            self.server_launcher(command, env)
            return
        subprocess.Popen(command, env=dict(env), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _list_devices(self) -> list[AdbDevice]:
        result = self._safe_adb(["devices", "-l"], timeout=5.0)
        if result.returncode != 0:
            return []
        return parse_adb_devices(result.stdout or "")

    def _safe_adb(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        _validate_adb_args(args)
        runner = self.run_command or _default_adb_command
        return runner([self.adb_path, "-P", str(_SAMSUNG_ADB_SERVER_PORT), *args], timeout, self._adb_env())

    def _adb_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["ADB_SERVER_SOCKET"] = f"tcp:{_SAMSUNG_ADB_SERVER_HOST}:{_SAMSUNG_ADB_SERVER_PORT}"
        env["ADB_MDNS_AUTO_CONNECT"] = "0"
        return env

    def _server_owner(self, state: _WatchdogState) -> str:
        if self.port_owner is not None:
            owner = self.port_owner(_SAMSUNG_ADB_SERVER_HOST, _SAMSUNG_ADB_SERVER_PORT)
            return owner if owner in {"managed", "unmanaged", "none"} else "unmanaged"
        if _localhost_port_open(_SAMSUNG_ADB_SERVER_HOST, _SAMSUNG_ADB_SERVER_PORT):
            return "managed" if state.managed_server_started else "unmanaged"
        return "none"

    def _recovery_budget_available(self, state: _WatchdogState) -> bool:
        now = self.clock()
        state.attempts = [attempt for attempt in state.attempts if now - attempt <= self.recovery_window_seconds]
        return len(state.attempts) < self.max_recovery_attempts

    def _state_path(self) -> Path:
        root = Path(self.state_root).expanduser() if self.state_root is not None else _DEFAULT_SKELETON_STATE_ROOT.expanduser()
        return root / _SAMSUNG_ADB_WATCHDOG_STATE_FILE

    def _load_state(self) -> _WatchdogState:
        path = self._state_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _WatchdogState()
        attempts = raw.get("attempts")
        return _WatchdogState(
            attempts=[float(item) for item in attempts if isinstance(item, int | float)] if isinstance(attempts, list) else [],
            managed_server_started=bool(raw.get("managed_server_started")),
        )

    def _save_state(self, state: _WatchdogState) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            canonical_json(
                {
                    "attempts": [round(attempt, 3) for attempt in state.attempts[-self.max_recovery_attempts :]],
                    "managed_server_started": state.managed_server_started,
                }
            )
            + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class AndroidObservation:
    source: str
    node_id: str
    observed_at: str
    kind: AndroidObservationKind | str
    quality: AndroidObservationQuality | str
    confidence: float
    payload: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    observation_id: str | None = None

    def __post_init__(self) -> None:
        normalized = normalize_observation_mapping(self.to_mapping(include_id=False))
        expected_id = observation_id_for(normalized)
        if self.observation_id is not None and self.observation_id != expected_id:
            raise AndroidGatewayError("INVALID_OBSERVATION_ID", "observation_id does not match canonical content")
        object.__setattr__(self, "source", normalized["source"])
        object.__setattr__(self, "node_id", normalized["node_id"])
        object.__setattr__(self, "observed_at", normalized["observed_at"])
        object.__setattr__(self, "kind", AndroidObservationKind(normalized["kind"]))
        object.__setattr__(self, "quality", AndroidObservationQuality(normalized["quality"]))
        object.__setattr__(self, "confidence", normalized["confidence"])
        object.__setattr__(self, "payload", normalized["payload"])
        object.__setattr__(self, "provenance", normalized["provenance"])
        object.__setattr__(self, "observation_id", expected_id)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> AndroidObservation:
        normalized = normalize_observation_mapping(raw)
        expected_id = observation_id_for(normalized)
        if raw.get("observation_id") is not None and raw["observation_id"] != expected_id:
            raise AndroidGatewayError("INVALID_OBSERVATION_ID", "observation_id does not match canonical content")
        return cls(
            source=normalized["source"],
            node_id=normalized["node_id"],
            observed_at=normalized["observed_at"],
            kind=normalized["kind"],
            quality=normalized["quality"],
            confidence=normalized["confidence"],
            payload=normalized["payload"],
            provenance=normalized["provenance"],
            observation_id=expected_id,
        )

    def to_mapping(self, *, include_id: bool = True) -> dict[str, Any]:
        result = {
            "schema": ANDROID_OBSERVATION_SCHEMA,
            "source": self.source,
            "node_id": self.node_id,
            "observed_at": self.observed_at,
            "kind": str(self.kind),
            "quality": str(self.quality),
            "confidence": self.confidence,
            "payload": dict(self.payload),
            "provenance": dict(self.provenance),
        }
        if include_id and self.observation_id is not None:
            result["observation_id"] = self.observation_id
        return result


class AndroidObservationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AndroidObservationStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def ingest(self, observation: AndroidObservation | Mapping[str, Any]) -> AndroidObservation:
        normalized = normalize_observation(observation)
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO android_observations (
                    observation_id, schema, source, node_id, observed_at, kind,
                    quality, confidence, payload_json, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized.observation_id,
                    ANDROID_OBSERVATION_SCHEMA,
                    normalized.source,
                    normalized.node_id,
                    normalized.observed_at,
                    normalized.kind.value,
                    normalized.quality.value,
                    normalized.confidence,
                    canonical_json(normalized.payload),
                    canonical_json(normalized.provenance),
                ),
            )
        return normalized

    def latest(self, kind: AndroidObservationKind | str | None = None) -> AndroidObservation | None:
        if kind is None:
            row = self._conn.execute(
                "SELECT * FROM android_observations ORDER BY observed_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT * FROM android_observations
                WHERE kind = ?
                ORDER BY observed_at DESC, rowid DESC
                LIMIT 1
                """,
                (AndroidObservationKind(kind).value,),
            ).fetchone()
        return _row_to_observation(row) if row is not None else None

    def by_kind(self, kind: AndroidObservationKind | str, *, limit: int = 50) -> list[AndroidObservation]:
        rows = self._conn.execute(
            """
            SELECT * FROM android_observations
            WHERE kind = ?
            ORDER BY observed_at DESC, rowid DESC
            LIMIT ?
            """,
            (AndroidObservationKind(kind).value, _bounded_limit(limit)),
        ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def recent(self, *, limit: int = 50) -> list[AndroidObservation]:
        rows = self._conn.execute(
            "SELECT * FROM android_observations ORDER BY observed_at DESC, rowid DESC LIMIT ?",
            (_bounded_limit(limit),),
        ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS android_observations (
                    observation_id TEXT PRIMARY KEY,
                    schema TEXT NOT NULL,
                    source TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_android_observations_kind_time
                ON android_observations(kind, observed_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_android_observations_time
                ON android_observations(observed_at DESC)
                """
            )


@dataclass(frozen=True)
class AndroidLocalCollector:
    node_id: str = "local-android"
    source: str = "termux.local"
    clock: Callable[[], datetime | str] = lambda: datetime.now(UTC)
    run_command: Callable[[Sequence[str], float], subprocess.CompletedProcess[str]] | None = None
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    uptime_reader: Callable[[], float | None] | None = None
    process_reader: Callable[[], Sequence[Mapping[str, Any]]] | None = None
    pid_alive: Callable[[int], bool] | None = None
    state_root: str | Path | None = None
    state_file_exists: Callable[[Path], bool] | None = None
    state_file_text: Callable[[Path], str | None] | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def collect(self) -> list[AndroidObservation]:
        return [
            self.collect_battery(),
            self.collect_storage(),
            self.collect_runtime(),
            self.collect_network(),
            self.collect_sensor_capabilities(),
            self.collect_step_activity(),
            self.collect_supervisor(),
            self.collect_remote_desktop_state(),
        ]

    def snapshot(self) -> dict[str, Any]:
        return snapshot_from_observations(self.collect(), generated_at=self._now_iso())

    def collect_battery(self) -> AndroidObservation:
        evidence = {"collector": "termux-battery-status", "timeout_seconds": 2.0}
        result = self._run(["termux-battery-status"], timeout=2.0)
        if result is None or result.returncode != 0:
            return self._observation(
                AndroidObservationKind.BATTERY,
                AndroidObservationQuality.UNAVAILABLE,
                0.2,
                {"level_percent": None, "status": "unknown", "charging": None, "temperature_c": None},
                evidence | {"available": False},
            )
        try:
            raw = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            raw = {}
        status = _safe_status(raw.get("status"))
        payload = {
            "level_percent": _bounded_number(raw.get("percentage"), minimum=0, maximum=100, as_int=True),
            "status": status,
            "charging": status in {"charging", "full"} if status != "unknown" else None,
            "temperature_c": _bounded_number(raw.get("temperature"), minimum=-20, maximum=90),
        }
        quality = AndroidObservationQuality.OK if payload["level_percent"] is not None else AndroidObservationQuality.DEGRADED
        return self._observation(AndroidObservationKind.BATTERY, quality, 0.9, payload, evidence | {"available": True})

    def collect_storage(self) -> AndroidObservation:
        evidence = {"collector": "local_filesystem", "path": "home"}
        try:
            usage = self.disk_usage(str(Path.home()))
        except OSError:
            return self._observation(
                AndroidObservationKind.STORAGE,
                AndroidObservationQuality.UNAVAILABLE,
                0.2,
                {"total_bytes": None, "used_bytes": None, "free_bytes": None, "used_percent": None},
                evidence | {"available": False},
            )
        used = int(usage.total) - int(usage.free)
        payload = {
            "total_bytes": int(usage.total),
            "used_bytes": used,
            "free_bytes": int(usage.free),
            "used_percent": round((used / int(usage.total)) * 100, 2) if int(usage.total) else None,
        }
        return self._observation(AndroidObservationKind.STORAGE, AndroidObservationQuality.OK, 0.95, payload, evidence | {"available": True})

    def collect_runtime(self) -> AndroidObservation:
        uptime = self._read_uptime()
        payload = {
            "uptime_seconds": round(uptime, 3) if uptime is not None else None,
            "python_process_pid_present": True,
            "health": "ok" if uptime is not None else "unknown",
        }
        quality = AndroidObservationQuality.OK if uptime is not None else AndroidObservationQuality.UNKNOWN
        return self._observation(
            AndroidObservationKind.RUNTIME,
            quality,
            0.8 if uptime is not None else 0.3,
            payload,
            {"collector": "local_runtime", "pid_evidence": "self"},
        )

    def collect_network(self) -> AndroidObservation:
        evidence = {"collector": "coarse_network_state", "sensitive_fields_discarded": True, "timeout_seconds": 2.0}
        result = self._run(["termux-wifi-connectioninfo"], timeout=2.0)
        if result is None or result.returncode != 0:
            return self._observation(
                AndroidObservationKind.NETWORK,
                AndroidObservationQuality.UNKNOWN,
                0.3,
                {"network_available": None, "transport": "unknown"},
                evidence | {"termux_network_available": False},
            )
        try:
            connected = bool(json.loads(result.stdout or "{}"))
        except json.JSONDecodeError:
            connected = bool((result.stdout or "").strip())
        return self._observation(
            AndroidObservationKind.NETWORK,
            AndroidObservationQuality.OK,
            0.6,
            {"network_available": connected, "transport": "wifi" if connected else "unknown"},
            evidence | {"termux_network_available": True},
        )


    def collect_sensor_capabilities(self) -> AndroidObservation:
        evidence = {
            "collector": "termux-sensor-list",
            "raw_names_discarded": True,
            "timeout_seconds": 3.0,
        }
        result = self._run(["termux-sensor", "-l"], timeout=3.0)
        if result is None or result.returncode != 0:
            return self._observation(
                AndroidObservationKind.SENSOR_CAPABILITIES,
                AndroidObservationQuality.UNAVAILABLE,
                0.2,
                {"capability_count": None, "sensor_types": []},
                evidence | {"available": False},
            )
        try:
            raw = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            raw = {}
        names = raw.get("sensors") if isinstance(raw, Mapping) else None
        capabilities: set[str] = set()
        if isinstance(names, Sequence) and not isinstance(names, bytes | bytearray | str):
            for name in names[:_MAX_ITEMS]:
                capability = _sensor_capability_type(name)
                if capability is not None:
                    capabilities.add(capability)
        normalized = sorted(capabilities)
        quality = AndroidObservationQuality.OK if normalized else AndroidObservationQuality.DEGRADED
        return self._observation(
            AndroidObservationKind.SENSOR_CAPABILITIES,
            quality,
            0.9 if normalized else 0.4,
            {"capability_count": len(normalized), "sensor_types": normalized},
            evidence | {"available": True},
        )

    def collect_step_activity(self) -> AndroidObservation:
        evidence = {
            "collector": "termux-sensor-step-counter",
            "semantic": "counter_since_boot",
            "timeout_seconds": 4.0,
        }
        result = self._run(["termux-sensor", "-s", "STEP_COUNTER", "-n", "1"], timeout=4.0)
        if result is None or result.returncode != 0:
            return self._observation(
                AndroidObservationKind.STEP_ACTIVITY,
                AndroidObservationQuality.UNAVAILABLE,
                0.2,
                {"counter_since_boot": None},
                evidence | {"available": False},
            )
        try:
            raw = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            raw = {}
        record = raw.get("STEP_COUNTER") if isinstance(raw, Mapping) else None
        values = record.get("values") if isinstance(record, Mapping) else None
        counter: int | None = None
        if isinstance(values, Sequence) and not isinstance(values, bytes | bytearray | str) and values:
            counter = _bounded_number(values[0], minimum=0, maximum=1_000_000_000, as_int=True)
        quality = AndroidObservationQuality.OK if counter is not None else AndroidObservationQuality.DEGRADED
        return self._observation(
            AndroidObservationKind.STEP_ACTIVITY,
            quality,
            0.9 if counter is not None else 0.4,
            {"counter_since_boot": counter},
            evidence | {"available": True},
        )

    def collect_supervisor(self) -> AndroidObservation:
        state = "unknown"
        quality = AndroidObservationQuality.UNKNOWN
        confidence = 0.3
        provenance: dict[str, Any] = {
            "collector": "local_supervisor_state",
            "registered_state_evidence": "unknown",
        }
        pid_evidence = self._registered_pid_evidence(_SUPERVISOR_PID_FILE)
        if pid_evidence == "live":
            state = "running"
            quality = AndroidObservationQuality.OK
            confidence = 0.9
            provenance["registered_state_evidence"] = "live"
        elif pid_evidence in {"missing", "stale", "invalid"}:
            state = "stopped"
            quality = AndroidObservationQuality.OK
            confidence = 0.75
            provenance["registered_state_evidence"] = pid_evidence
        else:
            processes = list(self._read_processes())
            names = {_safe_process_name(process.get("name")) for process in processes}
            names.discard(None)
            if "redmi-runtime-supervisor" in names:
                state = "running"
                quality = AndroidObservationQuality.DEGRADED
                confidence = 0.45
                provenance |= {
                    "registered_state_evidence": "unevaluable",
                    "bounded_process_evidence": True,
                    "process_identity": "redmi-runtime-supervisor",
                }
        return self._observation(
            AndroidObservationKind.SUPERVISOR,
            quality,
            confidence,
            {"state": state},
            provenance,
        )

    def collect_remote_desktop_state(self) -> AndroidObservation:
        auth_hold = self._state_file_exists(_RDC_AUTH_REQUIRED_FILE)
        if auth_hold is True or _env_truthy(self.environ.get("SKELETON_RDC_AUTH_REQUIRED")):
            state = RemoteDesktopState.AUTH_REQUIRED
            confidence = 0.9
            evidence = "auth_required"
        elif auth_hold is None:
            state = RemoteDesktopState.UNKNOWN
            confidence = 0.3
            evidence = "unevaluable"
        else:
            pid_evidence = self._registered_pid_evidence(_RDC_PID_FILE)
            evidence = pid_evidence
            if pid_evidence == "live":
                state = RemoteDesktopState.ONLINE
                confidence = 0.9
            elif pid_evidence in {"missing", "stale", "invalid"}:
                state = RemoteDesktopState.OFFLINE
                confidence = 0.75
            else:
                state = RemoteDesktopState.UNKNOWN
                confidence = 0.3
        return self._observation(
            AndroidObservationKind.REMOTE_DESKTOP_STATE,
            AndroidObservationQuality.OK if state != RemoteDesktopState.UNKNOWN else AndroidObservationQuality.UNKNOWN,
            confidence,
            {"state": state.value},
            {
                "collector": "local_rdc_state",
                "hold_checked": True,
                "registered_state_evidence": evidence,
            },
        )

    def _observation(
        self,
        kind: AndroidObservationKind,
        quality: AndroidObservationQuality,
        confidence: float,
        payload: Mapping[str, Any],
        provenance: Mapping[str, Any],
    ) -> AndroidObservation:
        return AndroidObservation(
            source=self.source,
            node_id=self.node_id,
            observed_at=self._now_iso(),
            kind=kind,
            quality=quality,
            confidence=confidence,
            payload=payload,
            provenance=provenance,
        )

    def _now_iso(self) -> str:
        return _coerce_observed_at(self.clock())

    def _run(self, command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str] | None:
        runner = self.run_command or _default_run_command
        try:
            return runner(command, timeout)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return None

    def _read_uptime(self) -> float | None:
        if self.uptime_reader is not None:
            value = self.uptime_reader()
            return float(value) if value is not None and math.isfinite(float(value)) and float(value) >= 0 else None
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as handle:
                return float(handle.read().split()[0])
        except (OSError, ValueError, IndexError):
            value = time.monotonic()
            return value if math.isfinite(value) and value >= 0 else None

    def _read_processes(self) -> Sequence[Mapping[str, Any]]:
        if self.process_reader is not None:
            return list(self.process_reader())[:128]
        return _default_process_reader()

    def _state_root(self) -> Path:
        root = self.state_root if self.state_root is not None else _DEFAULT_SKELETON_STATE_ROOT
        return Path(root).expanduser()

    def _state_path(self, filename: str) -> Path:
        return self._state_root() / filename

    def _state_file_exists(self, filename: str) -> bool | None:
        path = self._state_path(filename)
        try:
            if self.state_file_exists is not None:
                return bool(self.state_file_exists(path))
            return path.exists()
        except OSError:
            return None

    def _read_state_text(self, filename: str) -> str | None:
        path = self._state_path(filename)
        try:
            if self.state_file_text is not None:
                return self.state_file_text(path)
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _registered_pid_evidence(self, filename: str) -> str:
        exists = self._state_file_exists(filename)
        if exists is None:
            return "unevaluable"
        if exists is False:
            return "missing"
        raw = self._read_state_text(filename)
        if raw is None:
            return "unevaluable"
        pid = _parse_registered_pid(raw)
        if pid is None:
            return "invalid"
        return "live" if self._pid_alive(pid) else "stale"

    def _pid_alive(self, pid: int) -> bool:
        if self.pid_alive is not None:
            try:
                return bool(self.pid_alive(pid))
            except OSError:
                return False
        return _default_pid_alive(pid)


def normalize_observation(observation: AndroidObservation | Mapping[str, Any]) -> AndroidObservation:
    if isinstance(observation, AndroidObservation):
        return observation
    return AndroidObservation.from_mapping(observation)


def normalize_observation_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema",
        "observation_id",
        "source",
        "node_id",
        "observed_at",
        "kind",
        "quality",
        "confidence",
        "payload",
        "provenance",
    }
    if set(raw) - allowed:
        raise AndroidGatewayError("UNSUPPORTED_OBSERVATION_FIELD", "observation contains unsupported fields")
    if raw.get("schema") != ANDROID_OBSERVATION_SCHEMA:
        raise AndroidGatewayError("INVALID_SCHEMA", "observation schema is invalid")
    try:
        kind = AndroidObservationKind(raw.get("kind"))
        quality = AndroidObservationQuality(raw.get("quality"))
    except ValueError as exc:
        raise AndroidGatewayError("UNSUPPORTED_OBSERVATION_KIND", "observation kind or quality is unsupported") from exc
    payload = raw.get("payload")
    provenance = raw.get("provenance")
    if not isinstance(payload, Mapping) or not isinstance(provenance, Mapping):
        raise AndroidGatewayError("INVALID_OBSERVATION_JSON", "payload and provenance must be JSON objects")
    return {
        "schema": ANDROID_OBSERVATION_SCHEMA,
        "source": _safe_required_string(raw.get("source"), "source"),
        "node_id": _safe_required_string(raw.get("node_id"), "node_id"),
        "observed_at": _coerce_observed_at(raw.get("observed_at")),
        "kind": kind.value,
        "quality": quality.value,
        "confidence": _normalize_confidence(raw.get("confidence")),
        "payload": validate_public_json(payload, label="payload"),
        "provenance": validate_public_json(provenance, label="provenance"),
    }


def observation_id_for(normalized_without_id: Mapping[str, Any]) -> str:
    canonical = {key: normalized_without_id[key] for key in sorted(normalized_without_id) if key != "observation_id"}
    return f"android-observation-{sha256(canonical_json(canonical).encode('utf-8')).hexdigest()}"


def snapshot_from_observations(
    observations: Sequence[AndroidObservation | Mapping[str, Any]], *, generated_at: str | datetime | None = None
) -> dict[str, Any]:
    normalized = [normalize_observation(observation).to_mapping() for observation in observations]
    kind_counts: dict[str, int] = {}
    for observation in normalized:
        kind_counts[observation["kind"]] = kind_counts.get(observation["kind"], 0) + 1
    return {
        "schema": ANDROID_SNAPSHOT_SCHEMA,
        "contract_version": ANDROID_GATEWAY_CONTRACT_VERSION,
        "generated_at": _coerce_observed_at(generated_at or datetime.now(UTC)),
        "privacy_boundary": ANDROID_GATEWAY_PRIVACY_BOUNDARY,
        "local_first": True,
        "observation_count": len(normalized),
        "kinds": dict(sorted(kind_counts.items())),
        "observations": sorted(normalized, key=lambda item: (item["kind"], item["observation_id"])),
    }


def validate_public_json(value: Any, *, label: str = "value", depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        raise AndroidGatewayError("JSON_TOO_DEEP", f"{label} is too deeply nested")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AndroidGatewayError("NON_FINITE_JSON_NUMBER", f"{label} contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise AndroidGatewayError("JSON_STRING_TOO_LONG", f"{label} contains an overlong string")
        if _contains_sensitive_identifier(value):
            raise AndroidGatewayError("SENSITIVE_JSON_REJECTED", f"{label} contains sensitive identifier content")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_ITEMS:
            raise AndroidGatewayError("JSON_OBJECT_TOO_LARGE", f"{label} contains too many keys")
        normalized: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str) or not key or len(key) > _MAX_KEY_LENGTH:
                raise AndroidGatewayError("INVALID_JSON_KEY", f"{label} contains an invalid key")
            if _is_sensitive_key(key):
                raise AndroidGatewayError("SENSITIVE_JSON_REJECTED", f"{label} contains sensitive keys")
            normalized[key] = validate_public_json(item, label=label, depth=depth + 1)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        if len(value) > _MAX_ITEMS:
            raise AndroidGatewayError("JSON_ARRAY_TOO_LARGE", f"{label} contains too many items")
        return [validate_public_json(item, label=label, depth=depth + 1) for item in value]
    raise AndroidGatewayError("UNSUPPORTED_JSON_VALUE", f"{label} contains unsupported JSON values")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _row_to_observation(row: sqlite3.Row) -> AndroidObservation:
    return AndroidObservation(
        observation_id=row["observation_id"],
        source=row["source"],
        node_id=row["node_id"],
        observed_at=row["observed_at"],
        kind=row["kind"],
        quality=row["quality"],
        confidence=float(row["confidence"]),
        payload=json.loads(row["payload_json"]),
        provenance=json.loads(row["provenance_json"]),
    )


def _bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return min(limit, 500)


def _safe_required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise AndroidGatewayError(f"INVALID_{field_name.upper()}", f"{field_name} must be a safe bounded string")
    if _contains_sensitive_identifier(value):
        raise AndroidGatewayError(f"SENSITIVE_{field_name.upper()}", f"{field_name} contains sensitive identifier content")
    return value


def _normalize_confidence(value: Any) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise AndroidGatewayError("INVALID_CONFIDENCE", "confidence must be a finite number")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise AndroidGatewayError("INVALID_CONFIDENCE", "confidence must be in [0, 1]")
    return confidence


def _coerce_observed_at(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or len(value) > _MAX_STRING_LENGTH:
        raise AndroidGatewayError("INVALID_OBSERVED_AT", "observed_at must be a bounded string")
    if _contains_sensitive_identifier(value):
        raise AndroidGatewayError("INVALID_OBSERVED_AT", "observed_at contains sensitive content")
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS):
        return True
    return any(token in _SENSITIVE_TOKENS for token in _key_tokens(key))


def _key_tokens(key: str) -> set[str]:
    split_camel = _CAMEL_RE.sub("_", key).lower()
    return {token for token in re.split(r"[^a-z0-9]+", split_camel) if token}


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _CAMEL_RE.sub("_", key).lower())


def _contains_sensitive_identifier(value: str) -> bool:
    if _MAC_RE.search(value) or _IPV6_RE.search(value):
        return True
    match = _IPV4_RE.search(value)
    if match:
        parts = match.group(0).split(".")
        if all(0 <= int(part) <= 255 for part in parts):
            return True
    return False



def _sensor_capability_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not name or len(name) > 128:
        return None
    if "step_counter" in name:
        return "step_counter"
    if "step_detector" in name:
        return "step_detector"
    if "significant_motion" in name:
        return "significant_motion"
    if "geomagnetic_rotation_vector" in name:
        return "geomagnetic_rotation_vector"
    if "game_rotation_vector" in name:
        return "game_rotation_vector"
    if "rotation_vector" in name:
        return "rotation_vector"
    if "linearaccel" in name or "linear_accel" in name:
        return "linear_acceleration"
    if "accelerometer" in name or "uncali_acc" in name:
        return "accelerometer"
    if "magnetometer" in name or "uncali_mag" in name:
        return "magnetometer"
    if "gyroscope" in name:
        return "gyroscope"
    if "proximity" in name or "prox" in name:
        return "proximity"
    if "gravity" in name:
        return "gravity"
    if "tilt" in name:
        return "tilt"
    if "pickup" in name:
        return "pickup"
    if "device_orientation" in name:
        return "device_orientation"
    if "orientation" in name:
        return "orientation"
    if "light" in name:
        return "light"
    if "touch" in name:
        return "touch"
    if "sar" in name:
        return "sar"
    return None


def _default_run_command(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), check=False, capture_output=True, text=True, timeout=timeout)


def _default_adb_command(
    command: Sequence[str], timeout: float, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), check=False, capture_output=True, text=True, timeout=timeout, env=dict(env))


def parse_adb_devices(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        text = line.strip()
        if not text or text.startswith("List of devices attached") or text.startswith("*"):
            continue
        fields = text.split()
        if len(fields) < 2:
            continue
        serial = fields[0]
        state = fields[1].lower()
        if not _safe_adb_serial(serial) or state not in {"device", "offline", "unauthorized", "recovery", "sideload"}:
            continue
        details: dict[str, str] = {}
        for token in fields[2:]:
            if ":" not in token:
                continue
            key, value = token.split(":", 1)
            if _SAFE_ID_RE.fullmatch(key) is not None and _SAFE_ID_RE.fullmatch(value.replace("/", "_")) is not None:
                details[key] = value
        devices.append(AdbDevice(serial=serial, state=state, details=details))
    return devices


def _validate_adb_args(args: Sequence[str]) -> None:
    if not args:
        raise AndroidGatewayError("INVALID_ADB_COMMAND", "adb command must not be empty")
    normalized = {str(arg).strip().lower() for arg in args}
    if normalized & _FORBIDDEN_ADB_TOKENS:
        raise AndroidGatewayError("FORBIDDEN_ADB_COMMAND", "adb command is outside the Samsung watchdog contract")
    joined = " ".join(normalized)
    if "setfunctions" in joined or "factory" in joined or "wipe" in joined:
        raise AndroidGatewayError("FORBIDDEN_ADB_COMMAND", "adb command is outside the Samsung watchdog contract")
    allowed = (
        tuple(args) == ("devices", "-l")
        or tuple(args) == ("reconnect", "offline")
        or tuple(args) == ("reconnect", "device")
        or (len(args) == 3 and args[0] == "-s" and _safe_adb_serial(str(args[1])) and args[2] == "usb")
    )
    if not allowed:
        raise AndroidGatewayError("UNSUPPORTED_ADB_COMMAND", "adb command is not part of the bounded watchdog")


def _safe_adb_serial(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value) is not None


def _localhost_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _default_process_reader() -> list[Mapping[str, Any]]:
    proc = Path("/proc")
    processes: list[Mapping[str, Any]] = []
    if not proc.exists():
        return processes
    try:
        children = list(proc.iterdir())
    except OSError:
        return processes
    for child in children:
        if not child.name.isdigit() or len(processes) >= 128:
            continue
        try:
            name = (child / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        processes.append({"name": name})
    return processes


def _default_pid_alive(pid: int) -> bool:
    if _parse_registered_pid(str(pid)) is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _safe_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower().replace("_", "-")
    return normalized if normalized in {"charging", "discharging", "full", "not-charging", "unknown"} else "unknown"


def _bounded_number(value: Any, *, minimum: float, maximum: float, as_int: bool = False) -> int | float | None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(float(value)):
        return None
    number = float(value)
    if number < minimum or number > maximum:
        return None
    return int(number) if as_int else round(number, 3)


def _safe_process_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = Path(value).name.strip().lower()
    return name if re.fullmatch(r"[a-z0-9_.+-]{1,64}", name) else None


def _parse_registered_pid(value: str) -> int | None:
    text = value.strip()
    if re.fullmatch(r"[0-9]{1,10}", text) is None:
        return None
    pid = int(text)
    return pid if 1 <= pid <= _MAX_PID else None


def _env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on", "hold", "required"}

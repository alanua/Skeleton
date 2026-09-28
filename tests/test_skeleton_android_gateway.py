from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.skeleton_android_gateway import (
    ANDROID_OBSERVATION_SCHEMA,
    ANDROID_SNAPSHOT_SCHEMA,
    AndroidGatewayError,
    AndroidLocalCollector,
    AndroidObservation,
    AndroidObservationKind,
    AndroidObservationStore,
    AndroidObservationQuality,
    RemoteDesktopState,
    snapshot_from_observations,
)

OBSERVED_AT = "2026-09-28T00:00:00Z"


def _observation(**overrides: object) -> AndroidObservation:
    values = {
        "source": "termux.local",
        "node_id": "android-node",
        "observed_at": OBSERVED_AT,
        "kind": AndroidObservationKind.BATTERY,
        "quality": AndroidObservationQuality.OK,
        "confidence": 0.9,
        "payload": {"charging": True, "level_percent": 87, "status": "charging", "temperature_c": 31.2},
        "provenance": {"collector": "termux-battery-status", "available": True},
    }
    values.update(overrides)
    return AndroidObservation(**values)


def test_exact_observation_and_snapshot_schemas() -> None:
    observation = _observation()
    snapshot = snapshot_from_observations([observation], generated_at=OBSERVED_AT)

    assert observation.to_mapping()["schema"] == "skeleton.android.observation.v1"
    assert ANDROID_OBSERVATION_SCHEMA == "skeleton.android.observation.v1"
    assert snapshot["schema"] == "skeleton.android.snapshot.v1"
    assert ANDROID_SNAPSHOT_SCHEMA == "skeleton.android.snapshot.v1"
    assert snapshot["local_first"] is True
    assert snapshot["observation_count"] == 1
    assert snapshot["kinds"] == {"battery": 1}


@pytest.mark.parametrize("kind", ["screen.rendered", "app.started", "provider.event", "camera", ""])
def test_strict_kind_rejection(kind: str) -> None:
    with pytest.raises(AndroidGatewayError) as exc:
        _observation(kind=kind)

    assert exc.value.reason_code == "UNSUPPORTED_OBSERVATION_KIND"


def test_deterministic_ids_from_canonical_normalized_content() -> None:
    first = _observation(payload={"status": "charging", "temperature_c": 31.2, "level_percent": 87, "charging": True})
    second = _observation(payload={"charging": True, "level_percent": 87, "status": "charging", "temperature_c": 31.2})
    changed = _observation(payload={"charging": False, "level_percent": 86, "status": "discharging", "temperature_c": 31.2})

    assert first.observation_id == second.observation_id
    assert first.observation_id.startswith("android-observation-")
    assert first.observation_id != changed.observation_id


def test_observation_id_mismatch_fails_closed() -> None:
    mapping = _observation().to_mapping()
    mapping["observation_id"] = "android-observation-wrong"

    with pytest.raises(AndroidGatewayError) as exc:
        AndroidObservation.from_mapping(mapping)

    assert exc.value.reason_code == "INVALID_OBSERVATION_ID"


def test_sqlite_store_is_idempotent_and_queries_latest_by_kind_recent(tmp_path: Path) -> None:
    store_path = tmp_path / "android.sqlite3"
    battery = _observation(observed_at="2026-09-28T00:00:00Z")
    newer_battery = _observation(
        observed_at="2026-09-28T00:01:00Z",
        payload={"charging": False, "level_percent": 80, "status": "discharging", "temperature_c": 30.0},
    )
    runtime = _observation(
        observed_at="2026-09-28T00:02:00Z",
        kind=AndroidObservationKind.RUNTIME,
        payload={"health": "ok", "python_process_pid_present": True, "uptime_seconds": 120.0},
        provenance={"collector": "local_runtime", "pid_evidence": "self"},
    )

    with AndroidObservationStore(store_path) as store:
        store.ingest(battery)
        store.ingest(battery)
        store.ingest(newer_battery)
        store.ingest(runtime)

        assert store.latest().kind == AndroidObservationKind.RUNTIME
        assert store.latest(AndroidObservationKind.BATTERY).observation_id == newer_battery.observation_id
        assert [item.observation_id for item in store.by_kind("battery", limit=10)] == [
            newer_battery.observation_id,
            battery.observation_id,
        ]
        assert [item.kind for item in store.recent(limit=2)] == [
            AndroidObservationKind.RUNTIME,
            AndroidObservationKind.BATTERY,
        ]

    with AndroidObservationStore(store_path) as reopened:
        assert len(reopened.recent(limit=10)) == 3
        assert reopened.by_kind("battery", limit=10)[0].payload["level_percent"] == 80


def test_collector_normalizes_battery_storage_runtime_network_supervisor_and_rdc() -> None:
    def run_command(command: list[str] | tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        if command[0] == "termux-battery-status":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"percentage": 77, "status": "CHARGING", "temperature": 32.4}),
                stderr="",
            )
        if command[0] == "termux-wifi-connectioninfo":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "ssid": "PrivateWifi",
                        "bssid": "00:11:22:33:44:55",
                        "ip": "192.168.1.50",
                        "link_speed_mbps": 433,
                    }
                ),
                stderr="",
            )
        raise FileNotFoundError(command[0])

    usage = shutil.disk_usage("/")
    collector = AndroidLocalCollector(
        node_id="android-node",
        clock=lambda: OBSERVED_AT,
        run_command=run_command,
        disk_usage=lambda path: usage,
        uptime_reader=lambda: 123.456,
        process_reader=lambda: [{"name": "skeleton-supervisor"}, {"name": "xrdp"}],
    )

    by_kind = {observation.kind: observation for observation in collector.collect()}

    assert by_kind[AndroidObservationKind.BATTERY].payload == {
        "charging": True,
        "level_percent": 77,
        "status": "charging",
        "temperature_c": 32.4,
    }
    assert by_kind[AndroidObservationKind.STORAGE].payload["total_bytes"] == usage.total
    assert by_kind[AndroidObservationKind.RUNTIME].payload["uptime_seconds"] == 123.456
    assert by_kind[AndroidObservationKind.NETWORK].payload == {"network_available": True, "transport": "wifi"}
    assert by_kind[AndroidObservationKind.SUPERVISOR].payload["state"] == "running"
    assert by_kind[AndroidObservationKind.REMOTE_DESKTOP_STATE].payload["state"] == RemoteDesktopState.ONLINE.value

    encoded = json.dumps([observation.to_mapping() for observation in by_kind.values()], sort_keys=True)
    assert "PrivateWifi" not in encoded
    assert "00:11:22:33:44:55" not in encoded
    assert "192.168.1.50" not in encoded
    assert "ssid" not in encoded.lower()
    assert "bssid" not in encoded.lower()
    assert '"ip"' not in encoded.lower()
    assert '"mac"' not in encoded.lower()


def test_remote_desktop_auth_required_hold_uses_enum() -> None:
    collector = AndroidLocalCollector(
        node_id="android-node",
        clock=lambda: OBSERVED_AT,
        run_command=lambda command, timeout: (_ for _ in ()).throw(FileNotFoundError(command[0])),
        process_reader=lambda: [{"name": "xrdp"}],
        environ={"SKELETON_RDC_AUTH_REQUIRED": "true"},
    )

    observation = collector.collect_remote_desktop_state()

    assert observation.kind == AndroidObservationKind.REMOTE_DESKTOP_STATE
    assert observation.payload == {"state": RemoteDesktopState.AUTH_REQUIRED.value}


def test_missing_termux_commands_degrade_without_crashing() -> None:
    collector = AndroidLocalCollector(
        node_id="android-node",
        clock=lambda: OBSERVED_AT,
        run_command=lambda command, timeout: (_ for _ in ()).throw(FileNotFoundError(command[0])),
        process_reader=lambda: [],
        uptime_reader=lambda: None,
    )

    battery = collector.collect_battery()
    network = collector.collect_network()
    runtime = collector.collect_runtime()

    assert battery.quality == AndroidObservationQuality.UNAVAILABLE
    assert battery.payload["status"] == "unknown"
    assert network.quality == AndroidObservationQuality.UNKNOWN
    assert network.payload == {"network_available": None, "transport": "unknown"}
    assert runtime.quality == AndroidObservationQuality.UNKNOWN


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"ssid": "Cafe"},
        {"network": {"bssid": "00:11:22:33:44:55"}},
        {"endpoint": "192.168.1.2"},
        {"deviceMac": "00:11:22:33:44:55"},
        {"location": {"lat": 52.5, "lon": 13.4}},
        {"clipboard": "private"},
        {"smsBody": "hello"},
        {"call_history": 1},
        {"contacts": ["A"]},
        {"notificationBody": "secret"},
        {"credentials": {"token": "abc"}},
        {"two_factor_2fa": "123456"},
        {"banking": "balance"},
        {"messengerContent": "hi"},
        {"microphone": "on"},
        {"photos": ["image"]},
        {"rawLogcat": "line"},
        {"files": ["/sdcard/private"]},
        {"intents": ["ACTION_VIEW"]},
        {"mutation": "toggle"},
        {"appContent": {"visibleText": "private"}},
    ],
)
def test_recursive_privacy_rejection_for_payload_and_provenance(bad_payload: dict[str, object]) -> None:
    with pytest.raises(AndroidGatewayError) as payload_exc:
        _observation(payload={"safe": {"nested": bad_payload}})
    with pytest.raises(AndroidGatewayError) as provenance_exc:
        _observation(provenance={"safe": {"nested": bad_payload}})

    assert payload_exc.value.reason_code == "SENSITIVE_JSON_REJECTED"
    assert provenance_exc.value.reason_code == "SENSITIVE_JSON_REJECTED"


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"nan": math.nan},
        {"object": object()},
        {"deep": [[[[[[[1]]]]]]]},
        {"items": list(range(129))},
        {1: "not a string key"},
    ],
)
def test_malformed_payload_fails_closed(bad_payload: dict[object, object]) -> None:
    with pytest.raises(AndroidGatewayError):
        _observation(payload=bad_payload)


def test_snapshot_cli_writes_output_and_emits_snapshot(tmp_path: Path) -> None:
    output_path = tmp_path / "snapshot.json"

    result = subprocess.run(
        [sys.executable, "scripts/android_gateway_snapshot.py", "--output", str(output_path), "--node-id", "cli-node"],
        check=True,
        capture_output=True,
        text=True,
    )

    stdout_snapshot = json.loads(result.stdout)
    file_snapshot = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_snapshot == file_snapshot
    assert stdout_snapshot["schema"] == "skeleton.android.snapshot.v1"
    assert stdout_snapshot["observation_count"] == 6
    assert set(stdout_snapshot["kinds"]) == {
        "battery",
        "network",
        "remote_desktop_state",
        "runtime",
        "storage",
        "supervisor",
    }

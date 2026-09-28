from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.skeleton_android_gateway import (
    ANDROID_GATEWAY_EVENT_SCHEMA,
    AndroidGatewayError,
    AndroidTelemetryEvent,
    SkeletonAndroidGateway,
    snapshot_from_events,
)


def test_accepts_provider_neutral_public_safe_metadata() -> None:
    gateway = SkeletonAndroidGateway(clock=lambda: "2026-09-28T00:00:00Z")

    receipt = gateway.ingest(
        AndroidTelemetryEvent(
            event_id="evt-1",
            provider="provider.synthetic",
            event_type="screen.rendered",
            payload={"screen": "settings", "duration_ms": 17, "cold_start": False},
        )
    )
    snapshot = gateway.snapshot()

    assert receipt["decision"] == "accepted"
    assert snapshot["runtime_mutation"] is False
    assert snapshot["privacy_boundary"] == "public_safe_metadata_only"
    assert snapshot["providers"] == {"provider.synthetic": 1}
    assert snapshot["event_types"] == {"screen.rendered": 1}
    assert snapshot["events"][0]["payload"] == {
        "cold_start": False,
        "duration_ms": 17,
        "screen": "settings",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"notification_text": "private message"},
        {"android_id": "abc"},
        {"raw_logcat": ["line"]},
        {"nested": {"email": "person@example.com"}},
        {"location": {"lat": 52.5, "lon": 13.4}},
    ],
)
def test_rejects_sensitive_device_or_app_content(payload: dict[str, object]) -> None:
    gateway = SkeletonAndroidGateway()

    receipt = gateway.try_ingest(
        {
            "schema": ANDROID_GATEWAY_EVENT_SCHEMA,
            "event_id": "evt-sensitive",
            "provider": "provider.synthetic",
            "event_type": "screen.rendered",
            "payload": payload,
        }
    )

    assert receipt["decision"] == "rejected"
    assert receipt["reason"] == "SENSITIVE_PAYLOAD_REJECTED"
    assert gateway.snapshot()["event_count"] == 0
    assert gateway.snapshot()["rejection_count"] == 1


def test_invalid_schema_fails_closed() -> None:
    gateway = SkeletonAndroidGateway()

    with pytest.raises(AndroidGatewayError) as exc:
        gateway.ingest(
            {
                "schema": "vendor.android.event",
                "event_id": "evt-1",
                "provider": "provider.synthetic",
                "event_type": "screen.rendered",
                "payload": {},
            }
        )

    assert exc.value.reason_code == "INVALID_SCHEMA"


def test_snapshot_from_events_keeps_counts_and_rejections() -> None:
    snapshot = snapshot_from_events(
        [
            {
                "schema": ANDROID_GATEWAY_EVENT_SCHEMA,
                "event_id": "evt-1",
                "provider": "provider.a",
                "event_type": "app.started",
                "payload": {"duration_ms": 3},
            },
            {
                "schema": ANDROID_GATEWAY_EVENT_SCHEMA,
                "event_id": "evt-2",
                "provider": "provider.a",
                "event_type": "app.started",
                "payload": {"message_body": "private"},
            },
        ]
    )

    assert snapshot["event_count"] == 1
    assert snapshot["rejection_count"] == 1
    assert snapshot["providers"] == {"provider.a": 1}


def test_snapshot_cli_outputs_public_safe_json(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "schema": ANDROID_GATEWAY_EVENT_SCHEMA,
                "event_id": "evt-cli",
                "provider": "provider.cli",
                "event_type": "app.started",
                "payload": {"duration_ms": 9},
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/android_gateway_snapshot.py", "--event", str(event_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    snapshot = json.loads(result.stdout)
    assert snapshot["schema"] == "skeleton.android_gateway.snapshot.v1"
    assert snapshot["event_count"] == 1
    assert snapshot["runtime_mutation"] is False

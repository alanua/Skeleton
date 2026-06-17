from __future__ import annotations

import json
from pathlib import Path

from core.hermes_private_memory import (
    HERMES_PRIVATE_MEMORY_REPORT_SCHEMA,
    orient_hermes_private_memory,
    record_hermes_private_memory_note,
    write_hermes_private_memory_heartbeat,
)
from core.private_memory import PRIVATE_MEMORY_CONFIG_SCHEMA


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "synthetic_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": PRIVATE_MEMORY_CONFIG_SCHEMA,
                "database": {"path": str(tmp_path / "memory.sqlite")},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def assert_public_safe(report: dict[str, object], tmp_path: Path) -> None:
    serialized = json.dumps(report, sort_keys=True)
    assert report["schema"] == HERMES_PRIVATE_MEMORY_REPORT_SCHEMA
    assert str(tmp_path) not in serialized
    assert "synthetic_config.json" not in serialized
    assert "memory.sqlite" not in serialized
    assert "private_memory_heartbeat" not in serialized
    assert "private_memory_task_state_heartbeat" not in serialized
    assert "SELECT" not in serialized
    assert "CREATE TABLE" not in serialized
    assert "path" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert "secret" not in serialized.lower()
    assert "payload" not in serialized.lower()


def test_orient_is_read_first_and_does_not_create_database(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = orient_hermes_private_memory(config_path=config_path)

    assert report["status"] == "BLOCKED"
    assert report["operation"] == "orient"
    assert report["connector_status"] == "BLOCKED"
    assert report["db_configured"] is False
    assert report["db_openable"] is False
    assert report["writable_when_requested"] is False
    assert report["bridge_write_enabled"] is False
    assert not (tmp_path / "memory.sqlite").exists()
    assert_public_safe(report, tmp_path)


def test_heartbeat_write_is_blocked_without_explicit_gate(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = write_hermes_private_memory_heartbeat(
        "synthetic-hermes-heartbeat-001",
        config_path=config_path,
    )

    assert report["status"] == "BLOCKED"
    assert report["operation"] == "heartbeat"
    assert report["writable_when_requested"] is False
    assert report["bridge_write_enabled"] is False
    assert report["error_class"] == "HermesPrivateMemoryWriteGateError"
    assert report["next_operator_action"] == "operator_enable_hermes_private_memory_write"
    assert not (tmp_path / "memory.sqlite").exists()
    assert_public_safe(report, tmp_path)


def test_heartbeat_write_uses_existing_connector_when_gated(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = write_hermes_private_memory_heartbeat(
        "synthetic-hermes-heartbeat-001",
        write_enabled=True,
        config_path=config_path,
    )

    assert report["status"] == "DONE"
    assert report["operation"] == "heartbeat"
    assert report["connector_status"] == "DONE"
    assert report["db_configured"] is True
    assert report["db_openable"] is True
    assert report["integrity_ok"] is True
    assert report["schema_present"] is True
    assert report["writable_when_requested"] is True
    assert report["heartbeat_ok"] is True
    assert report["bridge_write_enabled"] is True
    assert report["next_operator_action"] == "none"
    assert_public_safe(report, tmp_path)


def test_note_write_uses_connector_task_state_heartbeat_when_gated(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = record_hermes_private_memory_note(
        "synthetic-hermes-note-001",
        "ready",
        write_enabled=True,
        config_path=config_path,
    )

    assert report["status"] == "DONE"
    assert report["operation"] == "note"
    assert report["connector_status"] == "DONE"
    assert report["writable_when_requested"] is True
    assert report["heartbeat_ok"] is True
    assert report["bridge_write_enabled"] is True
    assert_public_safe(report, tmp_path)


def test_note_write_rejects_private_looking_state(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = record_hermes_private_memory_note(
        "synthetic-hermes-note-002",
        "token from local registry",
        write_enabled=True,
        config_path=config_path,
    )

    assert report["status"] == "BLOCKED"
    assert report["operation"] == "note"
    assert report["error_class"] == "ValueError"
    assert report["bridge_write_enabled"] is True
    assert not (tmp_path / "memory.sqlite").exists()
    assert_public_safe(report, tmp_path)


def test_missing_config_returns_sanitized_public_report(tmp_path: Path) -> None:
    report = orient_hermes_private_memory(env={})

    assert report["status"] == "BLOCKED"
    assert report["operation"] == "orient"
    assert report["error_class"] == "PrivateMemoryConfigError"
    assert report["next_operator_action"] == "configure_private_memory"
    assert_public_safe(report, tmp_path)

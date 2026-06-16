from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.private_memory import (
    PRIVATE_MEMORY_BOOTSTRAP_REGISTRY_SCHEMAS,
    PRIVATE_MEMORY_CONFIG_ENV,
    PRIVATE_MEMORY_CONFIG_SCHEMA,
    healthcheck_private_memory,
    read_public_heartbeat,
    record_task_state_heartbeat,
    write_public_heartbeat,
)


def write_config(tmp_path: Path, db_path: Path | str | None = None) -> Path:
    config_path = tmp_path / "synthetic_config.json"
    if db_path is None:
        db_path = tmp_path / "memory.sqlite"
    config_path.write_text(
        json.dumps(
            {
                "schema": PRIVATE_MEMORY_CONFIG_SCHEMA,
                "database": {"path": str(db_path)},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def write_bootstrap_registry(
    tmp_path: Path,
    db_path: Path | str | None = None,
    *,
    schema: str | None = None,
) -> Path:
    registry_path = tmp_path / "registry.local.json"
    if db_path is None:
        db_path = "memory.sqlite"
    registry_path.write_text(
        json.dumps(
            {
                "schema": schema or sorted(PRIVATE_MEMORY_BOOTSTRAP_REGISTRY_SCHEMAS)[0],
                "root_path": str(tmp_path),
                "services": {
                    "private_memory": {
                        "sqlite": {
                            "path": str(db_path),
                        },
                    },
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def assert_public_safe(report: dict[str, object], tmp_path: Path) -> None:
    serialized = json.dumps(report, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "synthetic_config.json" not in serialized
    assert "registry.local.json" not in serialized
    assert "memory.sqlite" not in serialized
    assert "private_memory_heartbeat" not in serialized
    assert "private_memory_task_state_heartbeat" not in serialized
    assert "SELECT" not in serialized
    assert "CREATE TABLE" not in serialized
    assert "path" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert "secret" not in serialized.lower()


def test_missing_config_fails_closed_without_path_leakage(tmp_path: Path) -> None:
    report = healthcheck_private_memory(env={})

    assert report["status"] == "BLOCKED"
    assert report["db_configured"] is False
    assert report["db_openable"] is False
    assert report["integrity_ok"] is False
    assert report["error_class"] == "PrivateMemoryConfigError"
    assert_public_safe(report, tmp_path)


def test_read_only_healthcheck_does_not_create_database(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    config_path = write_config(tmp_path, db_path)

    report = healthcheck_private_memory(config_path)

    assert report["status"] == "BLOCKED"
    assert report["db_configured"] is False
    assert report["db_openable"] is False
    assert report["writable_when_requested"] is False
    assert not db_path.exists()
    assert_public_safe(report, tmp_path)


def test_env_config_path_supports_read_only_healthcheck(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    sqlite3.connect(db_path).close()
    config_path = write_config(tmp_path, db_path)

    report = healthcheck_private_memory(env={PRIVATE_MEMORY_CONFIG_ENV: str(config_path)})

    assert report["status"] == "BLOCKED"
    assert report["db_configured"] is True
    assert report["db_openable"] is True
    assert report["integrity_ok"] is True
    assert report["schema_present"] is False
    assert report["table_count"] == 0
    assert report["writable_when_requested"] is False
    assert_public_safe(report, tmp_path)


def test_write_heartbeat_initializes_schema_and_reads_public_status(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    write_report = write_public_heartbeat(
        "synthetic-heartbeat-001",
        source="synthetic-test",
        config_path=config_path,
    )
    read_report = read_public_heartbeat(
        config_path=config_path,
        heartbeat_id="synthetic-heartbeat-001",
    )

    assert write_report["status"] == "DONE"
    assert write_report["db_configured"] is True
    assert write_report["db_openable"] is True
    assert write_report["integrity_ok"] is True
    assert write_report["schema_present"] is True
    assert write_report["table_count"] == 2
    assert write_report["writable_when_requested"] is True
    assert write_report["heartbeat_ok"] is True
    assert read_report["status"] == "DONE"
    assert read_report["writable_when_requested"] is False
    assert read_report["heartbeat_ok"] is True
    assert_public_safe(write_report, tmp_path)
    assert_public_safe(read_report, tmp_path)


def test_bootstrap_registry_read_only_healthcheck_uses_adapter(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE private_memory_heartbeat (heartbeat_id TEXT PRIMARY KEY, source TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE private_memory_task_state_heartbeat (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
    registry_path = write_bootstrap_registry(tmp_path, db_path="memory.sqlite")

    report = healthcheck_private_memory(registry_path)

    assert report["status"] == "DONE"
    assert report["db_configured"] is True
    assert report["db_openable"] is True
    assert report["integrity_ok"] is True
    assert report["schema_present"] is True
    assert report["writable_when_requested"] is False
    assert_public_safe(report, tmp_path)


def test_bootstrap_registry_write_and_read_heartbeat_uses_adapter(tmp_path: Path) -> None:
    registry_path = write_bootstrap_registry(tmp_path)

    write_report = write_public_heartbeat(
        "synthetic-bootstrap-heartbeat-001",
        source="synthetic-test",
        config_path=registry_path,
    )
    read_report = read_public_heartbeat(
        heartbeat_id="synthetic-bootstrap-heartbeat-001",
        config_path=registry_path,
    )

    assert write_report["status"] == "DONE"
    assert write_report["schema_present"] is True
    assert write_report["writable_when_requested"] is True
    assert write_report["heartbeat_ok"] is True
    assert read_report["status"] == "DONE"
    assert read_report["writable_when_requested"] is False
    assert read_report["heartbeat_ok"] is True
    assert_public_safe(write_report, tmp_path)
    assert_public_safe(read_report, tmp_path)


def test_schema_detection_blocks_wrong_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated_public_fixture (id TEXT PRIMARY KEY)")
    config_path = write_config(tmp_path, db_path)

    report = healthcheck_private_memory(config_path)

    assert report["status"] == "BLOCKED"
    assert report["db_configured"] is True
    assert report["db_openable"] is True
    assert report["integrity_ok"] is True
    assert report["schema_present"] is False
    assert report["table_count"] == 1
    assert report["error_class"] == "PrivateMemorySchemaError"
    assert report["next_operator_action"] == "initialize_private_memory_schema"
    assert_public_safe(report, tmp_path)


def test_task_state_heartbeat_uses_synthetic_ids_only(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = record_task_state_heartbeat(
        "synthetic-task-001",
        "ready",
        source="synthetic-test",
        config_path=config_path,
    )

    assert report["status"] == "DONE"
    assert report["schema_present"] is True
    assert report["writable_when_requested"] is True
    assert report["heartbeat_ok"] is True
    assert_public_safe(report, tmp_path)


def test_invalid_bootstrap_registry_fails_closed_without_registry_leakage(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.local.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": "skeleton.bootstrap.local_registry.v0",
                "root_path": str(tmp_path),
                "services": {"private_memory": {"sqlite": {}}},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    report = healthcheck_private_memory(registry_path)

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemoryConfigError"
    assert_public_safe(report, tmp_path)


def test_privacy_redaction_fails_closed_for_unsafe_input(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    report = write_public_heartbeat(
        "synthetic-heartbeat-002",
        source="token from private registry",
        config_path=config_path,
    )

    assert report["status"] == "BLOCKED"
    assert report["heartbeat_ok"] is False
    assert report["error_class"] == "ValueError"
    assert_public_safe(report, tmp_path)

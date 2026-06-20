from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time
from pathlib import Path

from core.runner_executor_registry import RegisteredCommandExecutor, RunnerExecutorRegistry
from core.universal_runner_task import (
    AtomicTaskStateStore,
    SCHEMA_ID,
    UniversalTaskResult,
    detect_protected_resources,
    execute_universal_task,
    gate_task,
    github_status_for_universal_state,
    migrate_legacy_task,
    normalize_task,
    run_with_timeout,
    sanitize_public_text,
)


def _task(**updates: object):
    raw = {
        "schema": SCHEMA_ID,
        "task_id": "task-1",
        "task_key": "task-key-1",
        "mode": "local_module_task",
        "risk": "YELLOW",
        "payload": {"command_id": "safe_command"},
        "timeout_seconds": 10,
    }
    raw.update(updates)
    return normalize_task(raw)


def test_red_risk_without_approval_is_blocked() -> None:
    decision = gate_task(_task(risk="RED", operator_approved=False))

    assert decision.status == "NEEDS_OPERATOR"
    assert "red_risk_requires_operator_approval" in decision.public["reasons"]


def test_protected_resource_without_approval_is_blocked() -> None:
    task = _task(resources=["secrets/prod.env"])

    decision = gate_task(task)

    assert decision.status == "NEEDS_OPERATOR"
    assert "protected_resource_requires_operator_approval" in decision.public["reasons"]
    assert detect_protected_resources(task) == {"private_or_traversal_path"}


def test_low_and_yellow_risk_are_explicitly_allowed() -> None:
    assert gate_task(_task(risk="LOW")).status == "RUNNING"
    assert gate_task(_task(risk="YELLOW")).status == "RUNNING"


def test_timeout_returns_before_slow_handler_completes() -> None:
    finished = threading.Event()

    def slow() -> UniversalTaskResult:
        time.sleep(1)
        finished.set()
        return UniversalTaskResult(status="COMPLETED", public={})

    started = time.monotonic()
    result = run_with_timeout(slow, 0.05)
    elapsed = time.monotonic() - started

    assert result.status == "CANCELLED"
    assert elapsed < 0.5
    assert not finished.is_set()


def test_concurrent_different_task_keys_cannot_corrupt_state(tmp_path: Path) -> None:
    store = AtomicTaskStateStore(tmp_path / "state.json")
    registry = RunnerExecutorRegistry(
        local_commands=RegisteredCommandExecutor(
            {"safe_command": lambda payload: {"status": "COMPLETED", "ok": True}}
        )
    )
    results: list[str] = []

    def worker(index: int) -> None:
        task = _task(task_id=f"task-{index}", task_key=f"key-{index}")
        results.append(execute_universal_task(task, registry, store).status)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state = store.load()
    assert sorted(state["tasks"]) == [f"key-{index}" for index in range(8)]
    assert set(results) == {"COMPLETED"}


def test_stale_lease_recovers_safely(tmp_path: Path) -> None:
    store = AtomicTaskStateStore(tmp_path / "state.json", stale_lease_seconds=1)
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    store.path.write_text(
        (
            '{"schema":"skeleton.runner_task.v1","tasks":{"task-key-1":'
            '{"status":"RUNNING","lease":{"owner":"old","heartbeat_at":"'
            + old
            + '"}}}}\n'
        ),
        encoding="utf-8",
    )

    result = store.acquire(_task(), "new-owner")

    assert result.status == "RUNNING"
    assert store.get("task-key-1")["lease"]["owner"] == "new-owner"


def test_status_and_cancel_missing_record_fail_closed(tmp_path: Path) -> None:
    store = AtomicTaskStateStore(tmp_path / "state.json")

    assert store.status("missing").status == "FAILED"
    assert store.status("missing").public["reason"] == "missing_record"
    assert store.cancel("missing").status == "FAILED"
    assert store.cancel("missing").public["reason"] == "missing_record"


def test_universal_state_to_github_mapping_preserves_semantics() -> None:
    expected = {
        "CHECKPOINTED": "pending",
        "NEEDS_OPERATOR": "action_required",
        "RUNNING": "pending",
        "CANCELLED": "cancelled",
        "FAILED": "failure",
        "COMPLETED": "success",
    }

    assert {
        status: github_status_for_universal_state(status)["state"]
        for status in expected
    } == expected


def test_sanitizes_windows_paths_ips_urls_drive_and_credentials() -> None:
    report = (
        "C:\\Users\\alice\\secret.txt /home/alice/private.txt 10.1.2.3 "
        "https://example.test/x https://drive.google.com/file/d/abc "
        "API_TOKEN=super-secret private_value=customer"
    )

    sanitized = sanitize_public_text(report)

    assert "C:\\Users" not in sanitized
    assert "/home/alice" not in sanitized
    assert "10.1.2.3" not in sanitized
    assert "example.test" not in sanitized
    assert "drive.google.com" not in sanitized
    assert "super-secret" not in sanitized
    assert "customer" not in sanitized


def test_legacy_compatibility_migrates_schema_and_modes() -> None:
    migrated = migrate_legacy_task(
        {
            "schema": "skeleton.universal_runner_task.v1",
            "task_id": "legacy-1",
            "task_key": "legacy-key",
            "mode": "local_command",
            "risk": "low",
            "params": {"command_id": "safe_command"},
        }
    )

    assert migrated.schema_id == SCHEMA_ID
    assert migrated.legacy_schema_id == "skeleton.universal_runner_task.v1"
    assert migrated.mode == "local_module_task"
    assert migrated.risk == "LOW"

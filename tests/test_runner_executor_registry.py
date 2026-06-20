from __future__ import annotations

import time

from core.runner_executor_registry import (
    ExecutorResult,
    FunctionExecutorAdapter,
    HermesPrivateTaskMockAdapter,
    RunnerExecutorRegistry,
    UniversalTaskExecutor,
    default_runner_executor_registry,
)
from core.universal_runner_task import SCHEMA_ID, UniversalRunnerTask, UniversalTaskStateStore


def _task(**updates: object) -> UniversalRunnerTask:
    payload: dict[str, object] = {
        "schema": SCHEMA_ID,
        "task_id": "task-1",
        "idempotency_key": "idem-1",
        "action": "START",
        "executor_type": "read_only_probe",
        "capability": "read_only",
        "risk_class": "low",
        "target": {"resource": "docs/UNIVERSAL_RUNNER_TASKS.md"},
        "repo": "alanua/Skeleton",
        "branch": "runner/universal",
        "task": "Probe public docs only.",
        "allowed_files_or_resources": ["docs/UNIVERSAL_RUNNER_TASKS.md"],
        "forbidden_actions": ["merge", "deploy"],
        "validation": {},
        "expected_output": "aggregate status",
        "privacy_boundary": "public-safe aggregate status only",
        "timeout_seconds": 30,
        "approval_requirement": "none",
        "private_payload_ref": None,
    }
    payload.update(updates)
    return UniversalRunnerTask.from_mapping(payload)


def _executor(tmp_path, registry: RunnerExecutorRegistry) -> UniversalTaskExecutor:
    return UniversalTaskExecutor(
        registry,
        UniversalTaskStateStore(tmp_path / "state.json"),
    )


def test_unknown_executor_is_rejected(tmp_path) -> None:
    executor = _executor(tmp_path, default_runner_executor_registry())
    task = _task(executor_type="missing_executor")

    record = executor.execute(task)

    assert record.state == "BLOCKED"
    assert "unknown executor_type" in record.public_report


def test_unknown_capability_is_rejected(tmp_path) -> None:
    executor = _executor(tmp_path, default_runner_executor_registry())
    task = _task(capability="write_anything")

    record = executor.execute(task)

    assert record.state == "BLOCKED"
    assert "unknown capability" in record.public_report


def test_duplicate_idempotency_key_executes_only_once(tmp_path) -> None:
    calls = {"count": 0}

    def handler(task, previous):
        del task, previous
        calls["count"] += 1
        return ExecutorResult("COMPLETED", "DONE", "DONE: executed once.")

    registry = RunnerExecutorRegistry()
    registry.register(
        FunctionExecutorAdapter(
            "read_only_probe",
            frozenset({"read_only"}),
            frozenset({"START"}),
            handler,
        )
    )
    executor = _executor(tmp_path, registry)

    first = executor.execute(_task())
    second = executor.execute(_task())

    assert first.state == "COMPLETED"
    assert second.public_report == first.public_report
    assert calls["count"] == 1


def test_concurrent_duplicate_task_is_locked(tmp_path) -> None:
    store = UniversalTaskStateStore(tmp_path / "state.json")
    lease = store.acquire_lease("idem-1")
    assert lease is not None
    try:
        executor = UniversalTaskExecutor(default_runner_executor_registry(), store)

        record = executor.execute(_task())
    finally:
        lease.release()

    assert record.state == "BLOCKED"
    assert "concurrent_duplicate_task_locked" in record.public_report


def test_timeout_is_recorded(tmp_path) -> None:
    def handler(task, previous):
        del task, previous
        time.sleep(1.05)
        return ExecutorResult("RUNNING", "RUNNING", "RUNNING: still active.")

    registry = RunnerExecutorRegistry()
    registry.register(
        FunctionExecutorAdapter(
            "read_only_probe",
            frozenset({"read_only"}),
            frozenset({"START"}),
            handler,
        )
    )
    executor = _executor(tmp_path, registry)

    record = executor.execute(_task(timeout_seconds=1))

    assert record.state == "FAILED"
    assert "timed out" in record.public_report


def test_cancel_records_cancelled_state(tmp_path) -> None:
    executor = _executor(tmp_path, default_runner_executor_registry())

    record = executor.execute(_task(action="CANCEL"))

    assert record.state == "CANCELLED"


def test_continue_resumes_checkpoint(tmp_path) -> None:
    executor = _executor(
        tmp_path,
        default_runner_executor_registry(hermes_private_refs={"hermes://mock/task-1"}),
    )
    start = executor.execute(
        _task(
            executor_type="hermes_private_task",
            capability="private_task",
            private_payload_ref="hermes://mock/task-1",
        )
    )
    continued = executor.execute(
        _task(
            action="CONTINUE",
            executor_type="hermes_private_task",
            capability="private_task",
            private_payload_ref="hermes://mock/task-1",
        )
    )

    assert start.state == "CHECKPOINTED"
    assert continued.state == "COMPLETED"


def test_mocked_hermes_actions_do_not_expand_private_ref(tmp_path) -> None:
    registry = RunnerExecutorRegistry()
    registry.register(HermesPrivateTaskMockAdapter({"hermes://mock/task-1"}))
    executor = _executor(tmp_path, registry)

    for action in ("START", "STATUS", "CONTINUE", "CANCEL"):
        record = executor.execute(
            _task(
                action=action,
                executor_type="hermes_private_task",
                capability="private_task",
                private_payload_ref="hermes://mock/task-1",
            )
        )
        assert "hermes://mock/task-1" not in record.public_report
        assert "private_payload_ref=opaque" in record.public_report

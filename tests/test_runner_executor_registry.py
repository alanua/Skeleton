from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.runner_executor import (
    CallableRunnerExecutor,
    RunnerExecutorError,
)
from core.runner_executor_registry import RunnerExecutorRegistry
from core.runner_task import RUNNER_TASK_SCHEMA, TASK_KINDS, RunnerTask


def runner_task(
    task_kind: str = "code_edit",
    *,
    capabilities: tuple[str, ...] = (
        "repository_read",
        "repository_write_allowlisted",
        "test_execution",
    ),
) -> RunnerTask:
    return RunnerTask.from_mapping(
        {
            "schema": RUNNER_TASK_SCHEMA,
            "repo": "alanua/Skeleton",
            "branch": "runner/issue-1513",
            "base_sha": "e119b8d16b4bb61d5aac76097a1f8e045c623669",
            "task_kind": task_kind,
            "payload": {"issue_number": 1513},
            "requested_capabilities": list(capabilities),
            "allowed_files": ["core/runner_executor.py"],
            "forbidden_actions": ["no runtime changes"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 900,
            "expected_output": ["draft PR"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": "operator-chat-2026-07-05-slice2-1496",
            "idempotency_key": "skeleton-runner-executor-registry-slice-2-v1",
        }
    )


def assert_reason(reason_code: str, operation: Any) -> None:
    with pytest.raises(RunnerExecutorError) as excinfo:
        operation()
    assert excinfo.value.reason_code == reason_code


def test_callable_adapter_and_registry_preserve_result_identity() -> None:
    expected = object()
    seen: list[RunnerTask] = []

    def handler(task: RunnerTask) -> object:
        seen.append(task)
        return expected

    executor = CallableRunnerExecutor(
        task_kind="code_edit",
        required_capabilities=("test_execution", "repository_read"),
        handler=handler,
    )
    registry = RunnerExecutorRegistry([executor])
    task = runner_task()

    assert executor.required_capabilities == ("repository_read", "test_execution")
    assert registry.lookup("code_edit") is executor
    assert registry.dispatch(task) is expected
    assert seen == [task]


def test_handler_exception_propagates_unchanged() -> None:
    class ExistingRunnerError(RuntimeError):
        pass

    marker = ExistingRunnerError("existing behavior")

    def handler(task: RunnerTask) -> object:
        raise marker

    registry = RunnerExecutorRegistry(
        [CallableRunnerExecutor(task_kind="diagnostic", handler=handler)]
    )

    with pytest.raises(ExistingRunnerError) as excinfo:
        registry.dispatch(runner_task("diagnostic"))
    assert excinfo.value is marker


def test_registry_route_contract_matches_runner_task_kinds() -> None:
    registry = RunnerExecutorRegistry()

    assert registry.supported_task_kinds == tuple(sorted(TASK_KINDS))
    assert registry.registered_task_kinds == ()
    assert registry.unregistered_task_kinds == tuple(sorted(TASK_KINDS))


def test_registered_routes_are_reported_deterministically() -> None:
    registry = RunnerExecutorRegistry(
        [
            CallableRunnerExecutor(task_kind="publish", handler=lambda task: None),
            CallableRunnerExecutor(task_kind="code_edit", handler=lambda task: None),
            CallableRunnerExecutor(task_kind="diagnostic", handler=lambda task: None),
        ]
    )

    assert registry.registered_task_kinds == (
        "code_edit",
        "diagnostic",
        "publish",
    )
    assert registry.unregistered_task_kinds == tuple(
        sorted(TASK_KINDS - {"code_edit", "diagnostic", "publish"})
    )


def test_duplicate_registration_fails_closed() -> None:
    registry = RunnerExecutorRegistry()
    registry.register(
        CallableRunnerExecutor(task_kind="code_edit", handler=lambda task: None)
    )

    assert_reason(
        "DUPLICATE_EXECUTOR_REGISTRATION",
        lambda: registry.register(
            CallableRunnerExecutor(task_kind="code_edit", handler=lambda task: None)
        ),
    )


def test_unknown_and_unregistered_routes_fail_closed() -> None:
    registry = RunnerExecutorRegistry()

    assert_reason("UNKNOWN_EXECUTOR_TASK_KIND", lambda: registry.lookup("vendor_model"))
    assert_reason(
        "EXECUTOR_NOT_REGISTERED",
        lambda: registry.dispatch(runner_task("repository_maintenance")),
    )


def test_missing_required_capability_fails_before_handler() -> None:
    called = False

    def handler(task: RunnerTask) -> object:
        nonlocal called
        called = True
        return None

    registry = RunnerExecutorRegistry(
        [
            CallableRunnerExecutor(
                task_kind="code_edit",
                required_capabilities=("test_execution",),
                handler=handler,
            )
        ]
    )

    assert_reason(
        "MISSING_EXECUTOR_CAPABILITY",
        lambda: registry.dispatch(
            runner_task(capabilities=("repository_read",))
        ),
    )
    assert called is False


def test_callable_adapter_rejects_task_kind_mismatch() -> None:
    executor = CallableRunnerExecutor(
        task_kind="diagnostic",
        handler=lambda task: None,
    )

    assert_reason(
        "EXECUTOR_TASK_KIND_MISMATCH",
        lambda: executor.execute(runner_task("code_edit")),
    )


@pytest.mark.parametrize(
    ("kwargs", "reason_code"),
    [
        (
            {"task_kind": "vendor_model", "handler": lambda task: None},
            "UNKNOWN_EXECUTOR_TASK_KIND",
        ),
        (
            {"task_kind": "code_edit", "handler": None},
            "INVALID_EXECUTOR_HANDLER",
        ),
        (
            {
                "task_kind": "code_edit",
                "handler": lambda task: None,
                "required_capabilities": ("repository_read", "repository_read"),
            },
            "DUPLICATE_EXECUTOR_CAPABILITY",
        ),
        (
            {
                "task_kind": "code_edit",
                "handler": lambda task: None,
                "required_capabilities": ("unbounded_shell",),
            },
            "UNKNOWN_EXECUTOR_CAPABILITY",
        ),
    ],
)
def test_callable_adapter_contract_fails_closed(
    kwargs: dict[str, object],
    reason_code: str,
) -> None:
    assert_reason(reason_code, lambda: CallableRunnerExecutor(**kwargs))


def test_registry_rejects_non_executor_objects() -> None:
    assert_reason(
        "INVALID_EXECUTOR",
        lambda: RunnerExecutorRegistry([object()]),
    )
    assert_reason(
        "INVALID_EXECUTOR_COLLECTION",
        lambda: RunnerExecutorRegistry("code_edit"),
    )


@dataclass
class MutableExecutor:
    task_kind: str = "code_edit"
    required_capabilities: tuple[str, ...] = ()

    def execute(self, task: RunnerTask) -> object:
        return task.payload


def test_structural_executor_protocol_is_supported() -> None:
    executor = MutableExecutor()
    registry = RunnerExecutorRegistry([executor])
    task = runner_task()

    assert registry.dispatch(task) == {"issue_number": 1513}


def test_registered_executor_contract_cannot_mutate_silently() -> None:
    executor = MutableExecutor()
    registry = RunnerExecutorRegistry([executor])
    executor.required_capabilities = ("test_execution",)

    assert_reason(
        "EXECUTOR_CONTRACT_CHANGED",
        lambda: registry.lookup("code_edit"),
    )


def test_dispatch_requires_runner_task() -> None:
    registry = RunnerExecutorRegistry(
        [CallableRunnerExecutor(task_kind="code_edit", handler=lambda task: None)]
    )

    assert_reason("INVALID_DISPATCH_TASK", lambda: registry.dispatch({}))

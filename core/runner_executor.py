from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from core.runner_task import REQUESTED_CAPABILITIES, TASK_KINDS, RunnerTask


EXECUTOR_CONTRACT_VERSION: Final = "skeleton.runner_executor.v1"


class RunnerExecutorError(ValueError):
    """Raised when an executor or dispatch request violates the Runner contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@runtime_checkable
class RunnerExecutor(Protocol):
    """Semantic executor contract used by the universal Runner registry."""

    task_kind: str
    required_capabilities: tuple[str, ...]

    def execute(self, task: RunnerTask) -> Any:
        """Execute one validated Runner task without changing its semantics."""
        ...


@dataclass(frozen=True)
class CallableRunnerExecutor:
    """Adapter for wrapping an existing Runner callable as a typed executor."""

    task_kind: str
    handler: Callable[[RunnerTask], Any]
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        task_kind = validate_task_kind(self.task_kind)
        capabilities = normalize_required_capabilities(self.required_capabilities)
        if not callable(self.handler):
            raise RunnerExecutorError(
                "INVALID_EXECUTOR_HANDLER",
                "executor handler must be callable",
            )
        object.__setattr__(self, "task_kind", task_kind)
        object.__setattr__(self, "required_capabilities", capabilities)

    def execute(self, task: RunnerTask) -> Any:
        validate_executor_task(self.task_kind, task)
        return self.handler(task)


def validate_task_kind(value: object) -> str:
    if not isinstance(value, str) or value not in TASK_KINDS:
        raise RunnerExecutorError(
            "UNKNOWN_EXECUTOR_TASK_KIND",
            "executor task kind is not a registered semantic route",
        )
    return value


def normalize_required_capabilities(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
        raise RunnerExecutorError(
            "INVALID_EXECUTOR_CAPABILITIES",
            "required capabilities must be an iterable of capability names",
        )

    capabilities = list(value)
    if any(not isinstance(item, str) for item in capabilities):
        raise RunnerExecutorError(
            "INVALID_EXECUTOR_CAPABILITY",
            "executor capability names must be strings",
        )
    if len(set(capabilities)) != len(capabilities):
        raise RunnerExecutorError(
            "DUPLICATE_EXECUTOR_CAPABILITY",
            "executor required capabilities contain duplicates",
        )

    unknown = sorted(set(capabilities) - REQUESTED_CAPABILITIES)
    if unknown:
        raise RunnerExecutorError(
            "UNKNOWN_EXECUTOR_CAPABILITY",
            f"executor capability is not allowlisted: {unknown[0]}",
        )
    return tuple(sorted(capabilities))


def validate_executor_task(task_kind: str, task: object) -> RunnerTask:
    if not isinstance(task, RunnerTask):
        raise RunnerExecutorError(
            "INVALID_EXECUTOR_TASK",
            "executor input must be a RunnerTask",
        )
    if task.task_kind != task_kind:
        raise RunnerExecutorError(
            "EXECUTOR_TASK_KIND_MISMATCH",
            "executor task kind does not match the Runner task",
        )
    return task

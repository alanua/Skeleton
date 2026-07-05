from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core.runner_executor import (
    RunnerExecutor,
    RunnerExecutorError,
    normalize_required_capabilities,
    validate_task_kind,
)
from core.runner_task import TASK_KINDS, RunnerTask


@dataclass(frozen=True)
class _ExecutorRegistration:
    task_kind: str
    required_capabilities: tuple[str, ...]
    executor: RunnerExecutor


class RunnerExecutorRegistry:
    """Deterministic registry for semantic Runner executor routes."""

    def __init__(self, executors: Iterable[RunnerExecutor] = ()) -> None:
        if isinstance(executors, (str, bytes, bytearray)):
            raise RunnerExecutorError(
                "INVALID_EXECUTOR_COLLECTION",
                "executors must be an iterable of executor objects",
            )
        self._registrations: dict[str, _ExecutorRegistration] = {}
        for executor in executors:
            self.register(executor)

    @property
    def supported_task_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(TASK_KINDS))

    @property
    def registered_task_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    @property
    def unregistered_task_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(TASK_KINDS - self._registrations.keys()))

    def register(self, executor: object) -> RunnerExecutor:
        registration = _registration_from_executor(executor)
        if registration.task_kind in self._registrations:
            raise RunnerExecutorError(
                "DUPLICATE_EXECUTOR_REGISTRATION",
                f"executor already registered for {registration.task_kind}",
            )
        self._registrations[registration.task_kind] = registration
        return registration.executor

    def lookup(self, task_kind: object) -> RunnerExecutor:
        normalized_task_kind = validate_task_kind(task_kind)
        try:
            registration = self._registrations[normalized_task_kind]
        except KeyError as exc:
            raise RunnerExecutorError(
                "EXECUTOR_NOT_REGISTERED",
                f"no executor is registered for {normalized_task_kind}",
            ) from exc
        return _validate_registration(registration).executor

    def dispatch(self, task: object) -> Any:
        if not isinstance(task, RunnerTask):
            raise RunnerExecutorError(
                "INVALID_DISPATCH_TASK",
                "registry dispatch requires a RunnerTask",
            )

        normalized_task_kind = validate_task_kind(task.task_kind)
        try:
            registration = self._registrations[normalized_task_kind]
        except KeyError as exc:
            raise RunnerExecutorError(
                "EXECUTOR_NOT_REGISTERED",
                f"no executor is registered for {normalized_task_kind}",
            ) from exc

        registration = _validate_registration(registration)
        requested_capabilities = frozenset(task.requested_capabilities)
        missing = tuple(
            sorted(set(registration.required_capabilities) - requested_capabilities)
        )
        if missing:
            raise RunnerExecutorError(
                "MISSING_EXECUTOR_CAPABILITY",
                "Runner task is missing required executor capabilities: "
                + ", ".join(missing),
            )

        return registration.executor.execute(task)


def _registration_from_executor(executor: object) -> _ExecutorRegistration:
    try:
        task_kind = getattr(executor, "task_kind")
        required_capabilities = getattr(executor, "required_capabilities")
        execute = getattr(executor, "execute")
    except AttributeError as exc:
        raise RunnerExecutorError(
            "INVALID_EXECUTOR",
            "executor does not implement the RunnerExecutor protocol",
        ) from exc

    normalized_task_kind = validate_task_kind(task_kind)
    normalized_capabilities = normalize_required_capabilities(required_capabilities)
    if not callable(execute):
        raise RunnerExecutorError(
            "INVALID_EXECUTOR",
            "executor execute attribute must be callable",
        )

    return _ExecutorRegistration(
        task_kind=normalized_task_kind,
        required_capabilities=normalized_capabilities,
        executor=executor,
    )


def _validate_registration(
    registration: _ExecutorRegistration,
) -> _ExecutorRegistration:
    current = _registration_from_executor(registration.executor)
    if (
        current.task_kind != registration.task_kind
        or current.required_capabilities != registration.required_capabilities
    ):
        raise RunnerExecutorError(
            "EXECUTOR_CONTRACT_CHANGED",
            "registered executor contract changed after registration",
        )
    return registration

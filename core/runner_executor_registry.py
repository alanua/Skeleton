from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Protocol

from .universal_runner_task import (
    TERMINAL_STATES,
    TaskExecutionRecord,
    UniversalRunnerTask,
    UniversalTaskError,
    UniversalTaskStateStore,
    make_record,
)


@dataclass(frozen=True)
class ExecutorResult:
    state: str
    public_status: str
    public_report: str
    checkpoint: dict[str, object] | None = None


class RunnerExecutorAdapter(Protocol):
    executor_type: str
    capabilities: frozenset[str]
    actions: frozenset[str]

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        ...


@dataclass(frozen=True)
class FunctionExecutorAdapter:
    executor_type: str
    capabilities: frozenset[str]
    actions: frozenset[str]
    handler: Callable[[UniversalRunnerTask, TaskExecutionRecord | None], ExecutorResult]

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        return self.handler(task, previous)


class RunnerExecutorRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, RunnerExecutorAdapter] = {}

    def register(self, adapter: RunnerExecutorAdapter) -> None:
        if adapter.executor_type in self._adapters:
            raise ValueError(f"executor {adapter.executor_type!r} is already registered")
        self._adapters[adapter.executor_type] = adapter

    def adapter_for(self, task: UniversalRunnerTask) -> RunnerExecutorAdapter:
        adapter = self._adapters.get(task.executor_type)
        if adapter is None:
            raise UniversalTaskError(f"unknown executor_type: {task.executor_type}")
        if task.capability not in adapter.capabilities:
            raise UniversalTaskError(f"unknown capability for executor: {task.capability}")
        if task.action not in adapter.actions:
            raise UniversalTaskError(f"unknown action for executor: {task.action}")
        return adapter

    @property
    def executor_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


class UniversalTaskExecutor:
    def __init__(
        self,
        registry: RunnerExecutorRegistry,
        store: UniversalTaskStateStore | None = None,
    ) -> None:
        self.registry = registry
        self.store = store or UniversalTaskStateStore()

    def execute(self, task: UniversalRunnerTask) -> TaskExecutionRecord:
        previous = self.store.get(task.idempotency_key)

        if task.action == "START" and previous and previous.state in TERMINAL_STATES:
            return previous
        if task.action == "CONTINUE" and previous and previous.state == "COMPLETED":
            return previous
        if task.action == "STATUS" and previous is not None:
            status_task = _task_with_action(task, "STATUS")
            try:
                result = self.registry.adapter_for(status_task).execute(
                    status_task, previous
                )
            except UniversalTaskError as exc:
                result = ExecutorResult(
                    "BLOCKED",
                    "BLOCKED",
                    f"BLOCKED: {exc}",
                    previous.checkpoint,
                )
            record = _record_from_result(status_task, result, previous)
            self.store.save(record)
            return record

        lease = self.store.acquire_lease(task.idempotency_key)
        if lease is None:
            record = make_record(
                task,
                state="BLOCKED",
                public_status="BLOCKED",
                public_report=(
                    "BLOCKED: universal runner task lease is already held.\n"
                    "reason=concurrent_duplicate_task_locked"
                ),
                checkpoint=previous.checkpoint if previous else None,
                started_at=previous.started_at if previous else None,
            )
            self.store.save(record)
            return record

        try:
            previous = self.store.get(task.idempotency_key)
            if task.action == "START" and previous and previous.state in TERMINAL_STATES:
                return previous
            if task.action == "START" and previous and previous.state not in TERMINAL_STATES:
                record = make_record(
                    task,
                    state="BLOCKED",
                    public_status="BLOCKED",
                    public_report=(
                        "BLOCKED: duplicate START cannot restart existing work.\n"
                        "reason=duplicate_idempotency_key"
                    ),
                    checkpoint=previous.checkpoint,
                    started_at=previous.started_at,
                )
                self.store.save(record)
                return record

            started = make_record(
                task,
                state="PREFLIGHT",
                public_status="PREFLIGHT",
                public_report="Universal runner task preflight accepted.",
                checkpoint=previous.checkpoint if previous else None,
                started_at=previous.started_at if previous else None,
            )
            self.store.save(started)
            deadline = time.monotonic() + task.timeout_seconds
            result: ExecutorResult | None = None
            try:
                adapter = self.registry.adapter_for(task)
            except UniversalTaskError as exc:
                result = ExecutorResult(
                    "BLOCKED",
                    "BLOCKED",
                    f"BLOCKED: {exc}",
                    started.checkpoint,
                )
            if result is not None:
                pass
            elif time.monotonic() > deadline:
                result = ExecutorResult(
                    state="FAILED",
                    public_status="FAILED",
                    public_report="FAILED: universal runner task timed out before execution.",
                    checkpoint=started.checkpoint,
                )
            else:
                result = adapter.execute(task, previous or started)
            if time.monotonic() > deadline and result.state not in TERMINAL_STATES:
                result = ExecutorResult(
                    state="FAILED",
                    public_status="FAILED",
                    public_report="FAILED: universal runner task timed out.",
                    checkpoint=result.checkpoint,
                )
            record = _record_from_result(task, result, previous or started)
            self.store.save(record)
            return record
        finally:
            lease.release()


def _record_from_result(
    task: UniversalRunnerTask,
    result: ExecutorResult,
    previous: TaskExecutionRecord | None,
) -> TaskExecutionRecord:
    return make_record(
        task,
        state=result.state,
        public_status=result.public_status,
        public_report=result.public_report,
        checkpoint=result.checkpoint,
        started_at=previous.started_at if previous else None,
    )


def _task_with_action(task: UniversalRunnerTask, action: str) -> UniversalRunnerTask:
    return UniversalRunnerTask(
        schema=task.schema,
        task_id=task.task_id,
        idempotency_key=task.idempotency_key,
        action=action,
        executor_type=task.executor_type,
        capability=task.capability,
        risk_class=task.risk_class,
        target=task.target,
        repo=task.repo,
        branch=task.branch,
        task=task.task,
        allowed_files_or_resources=task.allowed_files_or_resources,
        forbidden_actions=task.forbidden_actions,
        validation=task.validation,
        expected_output=task.expected_output,
        privacy_boundary=task.privacy_boundary,
        timeout_seconds=task.timeout_seconds,
        approval_requirement=task.approval_requirement,
        private_payload_ref=task.private_payload_ref,
        approval_evidence=task.approval_evidence,
    )


class HermesPrivateTaskMockAdapter:
    executor_type = "hermes_private_task"
    capabilities = frozenset({"private_task"})
    actions = frozenset({"START", "STATUS", "CONTINUE", "CANCEL"})

    def __init__(self, private_registry_refs: set[str] | frozenset[str] | None = None) -> None:
        self.private_registry_refs = frozenset(private_registry_refs or {"hermes://mock/task-1"})

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        if not task.private_payload_ref or task.private_payload_ref not in self.private_registry_refs:
            return ExecutorResult(
                state="BLOCKED",
                public_status="BLOCKED",
                public_report=(
                    "BLOCKED: Hermes private task ref is unavailable on Runner host.\n"
                    "private_payload_ref=opaque\n"
                    "reason=private_payload_ref_unresolved"
                ),
                checkpoint=previous.checkpoint if previous else None,
            )
        if task.action == "START":
            return ExecutorResult(
                state="CHECKPOINTED",
                public_status="CHECKPOINTED",
                public_report=(
                    "CHECKPOINTED: mocked Hermes private task START accepted.\n"
                    "private_payload_ref=opaque\n"
                    "live_execution=false"
                ),
                checkpoint={"step": "mocked_hermes_start", "private_payload_ref": "opaque"},
            )
        if task.action == "STATUS":
            state = previous.state if previous else "CHECKPOINTED"
            return ExecutorResult(
                state=state,
                public_status=state,
                public_report=(
                    f"{state}: mocked Hermes private task STATUS.\n"
                    "private_payload_ref=opaque\n"
                    "live_execution=false"
                ),
                checkpoint=previous.checkpoint if previous else None,
            )
        if task.action == "CONTINUE":
            return ExecutorResult(
                state="COMPLETED",
                public_status="DONE",
                public_report=(
                    "DONE: mocked Hermes private task CONTINUE completed.\n"
                    "private_payload_ref=opaque\n"
                    "live_execution=false"
                ),
                checkpoint={"step": "mocked_hermes_complete", "private_payload_ref": "opaque"},
            )
        return ExecutorResult(
            state="CANCELLED",
            public_status="CANCELLED",
            public_report=(
                "CANCELLED: mocked Hermes private task CANCEL recorded.\n"
                "private_payload_ref=opaque\n"
                "live_execution=false"
            ),
            checkpoint=previous.checkpoint if previous else None,
        )


class ReadOnlyProbeAdapter:
    executor_type = "read_only_probe"
    capabilities = frozenset({"read_only"})
    actions = frozenset({"START", "STATUS", "CANCEL"})

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        if task.action == "CANCEL":
            return ExecutorResult("CANCELLED", "CANCELLED", "CANCELLED: read-only probe cancelled.")
        if task.action == "STATUS" and previous is not None:
            return ExecutorResult(previous.state, previous.public_status, previous.public_report, previous.checkpoint)
        return ExecutorResult(
            "COMPLETED",
            "DONE",
            "DONE: read-only probe completed without private expansion.",
            {"probe": "completed"},
        )


class LocalModuleTaskAdapter:
    executor_type = "local_module_task"
    capabilities = frozenset({"registered_command"})
    actions = frozenset({"START", "STATUS", "CANCEL"})

    def __init__(self, commands: dict[str, Callable[[UniversalRunnerTask], ExecutorResult]] | None = None) -> None:
        self.commands = dict(commands or {})

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        if task.action == "CANCEL":
            return ExecutorResult("CANCELLED", "CANCELLED", "CANCELLED: local module task cancelled.")
        if task.action == "STATUS" and previous is not None:
            return ExecutorResult(previous.state, previous.public_status, previous.public_report, previous.checkpoint)
        command_id = task.target.get("command_id")
        if not isinstance(command_id, str) or command_id not in self.commands:
            return ExecutorResult(
                "BLOCKED",
                "BLOCKED",
                "BLOCKED: local module command is not registered server-side.",
            )
        return self.commands[command_id](task)


class RuntimeMaintenanceTaskAdapter:
    executor_type = "runtime_maintenance_task"
    capabilities = frozenset({"runtime_maintenance"})
    actions = frozenset({"START", "STATUS", "CANCEL"})

    def __init__(self, dispatcher: Callable[[str, str, str], str], workdir: str) -> None:
        self.dispatcher = dispatcher
        self.workdir = workdir

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        if task.action == "CANCEL":
            return ExecutorResult("CANCELLED", "CANCELLED", "CANCELLED: runtime maintenance task cancelled.")
        if task.action == "STATUS" and previous is not None:
            return ExecutorResult(previous.state, previous.public_status, previous.public_report, previous.checkpoint)
        task_id = task.target.get("maintenance_task_id")
        if not isinstance(task_id, str):
            return ExecutorResult("BLOCKED", "BLOCKED", "BLOCKED: maintenance_task_id missing.")
        report = self.dispatcher(task_id, self.workdir, task.task)
        done = report.startswith("DONE:")
        return ExecutorResult(
            "COMPLETED" if done else "BLOCKED",
            "DONE" if done else "BLOCKED",
            report,
            {"maintenance_task_id": task_id},
        )


class CodexBranchTaskAdapter:
    executor_type = "codex_branch_task"
    capabilities = frozenset({"code_change"})
    actions = frozenset({"START", "STATUS", "CANCEL"})

    def __init__(
        self,
        starter: Callable[[UniversalRunnerTask], ExecutorResult] | None = None,
    ) -> None:
        self.starter = starter

    def execute(
        self, task: UniversalRunnerTask, previous: TaskExecutionRecord | None = None
    ) -> ExecutorResult:
        if task.action == "CANCEL":
            return ExecutorResult("CANCELLED", "CANCELLED", "CANCELLED: Codex branch task cancelled before launch.")
        if task.action == "STATUS" and previous is not None:
            return ExecutorResult(previous.state, previous.public_status, previous.public_report, previous.checkpoint)
        if self.starter is None:
            return ExecutorResult(
                "NEEDS_OPERATOR",
                "NEEDS_OPERATOR",
                (
                    "NEEDS_OPERATOR: codex_branch_task is registered, but live branch "
                    "execution remains behind the existing Runner issue-worktree gate."
                ),
            )
        return self.starter(task)


def default_runner_executor_registry(
    *,
    maintenance_dispatcher: Callable[[str, str, str], str] | None = None,
    maintenance_workdir: str = ".",
    hermes_private_refs: set[str] | frozenset[str] | None = None,
) -> RunnerExecutorRegistry:
    registry = RunnerExecutorRegistry()
    registry.register(CodexBranchTaskAdapter())
    registry.register(HermesPrivateTaskMockAdapter(hermes_private_refs))
    registry.register(LocalModuleTaskAdapter())
    if maintenance_dispatcher is not None:
        registry.register(RuntimeMaintenanceTaskAdapter(maintenance_dispatcher, maintenance_workdir))
    else:
        registry.register(
            FunctionExecutorAdapter(
                executor_type="runtime_maintenance_task",
                capabilities=frozenset({"runtime_maintenance"}),
                actions=frozenset({"START", "STATUS", "CANCEL"}),
                handler=lambda task, previous: ExecutorResult(
                    "NEEDS_OPERATOR",
                    "NEEDS_OPERATOR",
                    "NEEDS_OPERATOR: runtime maintenance adapter requires Runner host dispatcher.",
                    previous.checkpoint if previous else None,
                ),
            )
        )
    registry.register(ReadOnlyProbeAdapter())
    return registry

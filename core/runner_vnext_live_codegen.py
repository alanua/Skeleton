from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping, Protocol

from core.runner_gate import RunnerGate
from core.runner_task import RunnerTask, RunnerTaskValidationError
from core.runner_vnext_contracts import OperationIR, PolicyInput, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_execution import GreenExecutionError, GreenExecutionResult
from core.runner_vnext_execution_gate import GreenExecutionGrant


REPOSITORY_CODEGEN_ADAPTER_ID = "adapter:repository-codegen"


class RepositoryCodegenCompileError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RepositoryCodegenContext:
    source_task_ref: str
    runner_task: RunnerTask
    runner_task_json_sha256: str
    task_id: str
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    universal_task: UniversalTask
    operation: OperationIR
    policy_input: PolicyInput


@dataclass(frozen=True)
class RepositoryCodegenBackendResult:
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    fence_token: int
    touched_resource_refs: tuple[str, ...]
    outcome: str
    reason_code: str
    before_state_ref: str
    after_state_ref: str
    validation_status: str
    rollback_requested: bool = False


class RepositoryCodegenBackend(Protocol):
    def execute(
        self,
        task: RunnerTask,
        context: RepositoryCodegenContext,
        grant: GreenExecutionGrant,
    ) -> RepositoryCodegenBackendResult: ...


class RepositoryCodegenExecutorAdapter:
    def __init__(self, *, context: RepositoryCodegenContext, backend: RepositoryCodegenBackend) -> None:
        self._context = context
        self._backend = backend

    def execute(self, grant: GreenExecutionGrant) -> GreenExecutionResult:
        _validate_grant_binding(self._context, grant)
        backend_result = self._backend.execute(self._context.runner_task, self._context, grant)
        _validate_backend_result(self._context, grant, backend_result)
        return GreenExecutionResult(
            operation_id=backend_result.operation_id,
            idempotency_key=backend_result.idempotency_key,
            adapter_id=backend_result.adapter_id,
            target_state_ref=backend_result.target_state_ref,
            fence_token=backend_result.fence_token,
            resources=backend_result.touched_resource_refs,
            effects=grant.planner_envelope.effects,
            outcome=backend_result.outcome,
            reason_code=backend_result.reason_code,
            before_state_ref=backend_result.before_state_ref,
            after_state_ref=backend_result.after_state_ref,
            validation_status=backend_result.validation_status,
            rollback_requested=backend_result.rollback_requested,
        )


def compile_repository_codegen_context(
    task: RunnerTask | Mapping[str, object],
    *,
    source_task_ref: str,
    adapter_id: str = REPOSITORY_CODEGEN_ADAPTER_ID,
) -> RepositoryCodegenContext:
    runner_task = _runner_task(task)
    _public_ref(source_task_ref, "REPOSITORY_CODEGEN_SOURCE_REF_INVALID")
    if runner_task.task_kind != "code_edit":
        raise RepositoryCodegenCompileError("REPOSITORY_CODEGEN_TASK_KIND_UNSUPPORTED")
    if runner_task.privacy_boundary != "PUBLIC_SAFE_REPOSITORY_ONLY":
        raise RepositoryCodegenCompileError("REPOSITORY_CODEGEN_PRIVACY_BOUNDARY_UNSUPPORTED")
    if not {"repository_read", "repository_write_allowlisted", "test_execution"}.issubset(runner_task.requested_capabilities):
        raise RepositoryCodegenCompileError("REPOSITORY_CODEGEN_CAPABILITIES_REQUIRED")
    protected = tuple(path for path in runner_task.allowed_files if RunnerGate().is_protected_path(path))
    if protected:
        raise RepositoryCodegenCompileError("REPOSITORY_CODEGEN_PROTECTED_RESOURCE")

    runner_task_hash = _runner_task_hash(runner_task)
    target_state_ref = f"git:{runner_task.base_sha}"
    resources = tuple(f"repo:{path}" for path in runner_task.allowed_files)
    effects = ("workspace_write",)
    task_id = f"task:repository-codegen:{runner_task_hash}"
    operation_id = f"op:repository-codegen:{runner_task_hash}"
    idempotency_key = f"idem:repository-codegen:{runner_task_hash}"
    universal_task = UniversalTask(
        task_id=task_id,
        intent=f"repository-codegen:{source_task_ref}",
        domain="github",
        target_resources=resources,
        required_capabilities=runner_task.requested_capabilities,
        privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE,
        expected_effects=effects,
        validation=tuple(_command_ref(command) for command in runner_task.validation_commands),
        rollback=("grant_bound_no_fabricated_success",),
        idempotency_key=idempotency_key,
        operator_boundary_evidence=(
            source_task_ref,
            f"runner-task-sha256:{runner_task_hash}",
            target_state_ref,
        ),
    )
    operation = OperationIR(
        operation_id=operation_id,
        kind="workspace_write",
        resources=resources,
        effects=effects,
        idempotency_key=idempotency_key,
    )
    policy_input = PolicyInput(
        operation=operation,
        reversibility=universal_task.reversibility,
        privacy=universal_task.privacy,
        target_state_ref=target_state_ref,
        operator_boundary_evidence=universal_task.operator_boundary_evidence,
    )
    return RepositoryCodegenContext(
        source_task_ref=source_task_ref,
        runner_task=runner_task,
        runner_task_json_sha256=runner_task_hash,
        task_id=task_id,
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        adapter_id=adapter_id,
        target_state_ref=target_state_ref,
        resources=resources,
        effects=effects,
        universal_task=universal_task,
        operation=operation,
        policy_input=policy_input,
    )


def _validate_grant_binding(context: RepositoryCodegenContext, grant: GreenExecutionGrant) -> None:
    if context.runner_task_json_sha256 != _runner_task_hash(context.runner_task):
        raise GreenExecutionError("REPOSITORY_CODEGEN_RUNNER_TASK_HASH_MISMATCH")
    expected = (
        context.task_id,
        context.operation_id,
        context.idempotency_key,
        context.adapter_id,
        context.target_state_ref,
        context.resources,
        context.effects,
    )
    actual = (
        grant.task_id,
        grant.operation_id,
        grant.idempotency_key,
        grant.adapter_id,
        grant.target_state_ref,
        grant.planner_envelope.resources,
        grant.planner_envelope.effects,
    )
    if actual != expected:
        raise GreenExecutionError("REPOSITORY_CODEGEN_GRANT_BINDING_MISMATCH")
    if not grant.execution_authorized or grant.effect_execution_started:
        raise GreenExecutionError("REPOSITORY_CODEGEN_FRESH_AUTHORIZED_GRANT_REQUIRED")


def _validate_backend_result(
    context: RepositoryCodegenContext,
    grant: GreenExecutionGrant,
    result: RepositoryCodegenBackendResult,
) -> None:
    expected = (
        context.operation_id,
        context.idempotency_key,
        context.adapter_id,
        context.target_state_ref,
        grant.fence_token,
    )
    actual = (
        result.operation_id,
        result.idempotency_key,
        result.adapter_id,
        result.target_state_ref,
        result.fence_token,
    )
    if actual != expected:
        raise GreenExecutionError("REPOSITORY_CODEGEN_BACKEND_BINDING_MISMATCH")
    if len(set(result.touched_resource_refs)) != len(result.touched_resource_refs):
        raise GreenExecutionError("REPOSITORY_CODEGEN_TOUCHED_RESOURCES_INVALID")
    authorized = frozenset(context.resources)
    for ref in (*result.touched_resource_refs, result.before_state_ref, result.after_state_ref):
        if not _is_public_ref(ref):
            raise GreenExecutionError("REPOSITORY_CODEGEN_PUBLIC_REF_REQUIRED")
    if any(ref not in authorized for ref in result.touched_resource_refs):
        raise GreenExecutionError("REPOSITORY_CODEGEN_TOUCHED_RESOURCES_OUT_OF_SCOPE")
    if result.outcome == "SUCCEEDED" and result.before_state_ref != result.after_state_ref and not result.touched_resource_refs:
        raise GreenExecutionError("REPOSITORY_CODEGEN_TOUCHED_RESOURCES_REQUIRED")


def _runner_task(value: RunnerTask | Mapping[str, object]) -> RunnerTask:
    if isinstance(value, RunnerTask):
        return RunnerTask.from_json(value.to_json())
    try:
        return RunnerTask.from_mapping(value)
    except RunnerTaskValidationError as exc:
        raise RepositoryCodegenCompileError(exc.reason_code) from exc


def _runner_task_hash(task: RunnerTask) -> str:
    return hashlib.sha256(task.to_json().encode("utf-8")).hexdigest()


def _command_ref(command: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest()
    return f"validation-sha256:{digest}"


def _public_ref(value: str, reason_code: str) -> None:
    if not _is_public_ref(value):
        raise RepositoryCodegenCompileError(reason_code)


def _is_public_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value and not any(ch.isspace() for ch in value)

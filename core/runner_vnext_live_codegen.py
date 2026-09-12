from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from core.runner_task import RunnerTask
from core.runner_vnext_contracts import OperationIR, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_execution import GreenExecutionError, GreenExecutionResult
from core.runner_vnext_execution_gate import GreenExecutionGrant, planner_envelope_hash


REPOSITORY_CODEGEN_ADAPTER_ID = "adapter:repository-codegen"
REPOSITORY_CODEGEN_EFFECT = "workspace_write"
REPOSITORY_CODEGEN_CAPABILITY = "repository_write_allowlisted"


class RepositoryCodegenBoundaryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RepositoryCodegenExecutionContext:
    source_task_ref: str
    task_envelope_hash: str
    task_id: str
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    authorized_resource_refs: tuple[str, ...]
    required_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryCodegenPlan:
    runner_task: RunnerTask
    context: RepositoryCodegenExecutionContext
    universal_task: UniversalTask
    operation: OperationIR


@dataclass(frozen=True)
class RepositoryCodegenBackendResult:
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    fence_token: int
    touched_resource_refs: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    validation_status: str
    outcome: str = "SUCCEEDED"
    reason_code: str = "EXECUTION_PASS"
    rollback_requested: bool = False


class RepositoryCodegenBackend(Protocol):
    def execute(
        self,
        *,
        runner_task: RunnerTask,
        context: RepositoryCodegenExecutionContext,
        fence_token: int,
    ) -> RepositoryCodegenBackendResult: ...


class RepositoryCodegenExecutorAdapter:
    """Grant-bound adapter for repository codegen. It starts no subprocess provider."""

    def __init__(
        self,
        *,
        backend: RepositoryCodegenBackend,
        runner_task: RunnerTask,
        context: RepositoryCodegenExecutionContext,
    ) -> None:
        self._backend = backend
        self._runner_task = runner_task
        self._context = context

    def execute(self, grant: GreenExecutionGrant) -> GreenExecutionResult:
        _validate_grant_binding(self._runner_task, self._context, grant)
        backend_result = self._backend.execute(
            runner_task=self._runner_task,
            context=self._context,
            fence_token=grant.fence_token,
        )
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


def compile_repository_codegen_plan(
    *,
    runner_task: RunnerTask,
    source_task_ref: str,
    adapter_id: str = REPOSITORY_CODEGEN_ADAPTER_ID,
) -> RepositoryCodegenPlan:
    _validate_runner_task_for_repository_codegen(runner_task)
    if not _public_ref(source_task_ref):
        raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_SOURCE_REF_INVALID")

    task_envelope_json = runner_task.to_json()
    task_envelope_hash = hashlib.sha256(task_envelope_json.encode("utf-8")).hexdigest()
    source_hash = hashlib.sha256(source_task_ref.encode("utf-8")).hexdigest()
    resources = tuple(f"repo:{path}" for path in runner_task.allowed_files)
    target_state_ref = f"git:{runner_task.base_sha}"
    binding_payload = {
        "adapter_id": adapter_id,
        "allowed_resource_refs": list(resources),
        "base_target_state_ref": target_state_ref,
        "original_idempotency_key": runner_task.idempotency_key,
        "source_task_ref": source_task_ref,
        "source_task_ref_hash": source_hash,
        "task_envelope_hash": task_envelope_hash,
    }
    binding_hash = hashlib.sha256(
        json.dumps(binding_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    context = RepositoryCodegenExecutionContext(
        source_task_ref=source_task_ref,
        task_envelope_hash=task_envelope_hash,
        task_id=f"task:repository-codegen:{source_hash[:16]}:{task_envelope_hash[:16]}",
        operation_id=f"op:repository-codegen:{binding_hash[:32]}",
        idempotency_key=f"idem:repository-codegen:{binding_hash}",
        adapter_id=adapter_id,
        target_state_ref=target_state_ref,
        authorized_resource_refs=resources,
        required_capabilities=runner_task.requested_capabilities,
    )
    universal_task = UniversalTask(
        task_id=context.task_id,
        intent="repository_codegen",
        domain="github",
        target_resources=context.authorized_resource_refs,
        required_capabilities=context.required_capabilities,
        privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE,
        expected_effects=(REPOSITORY_CODEGEN_EFFECT,),
        validation=_validation_refs(runner_task),
        rollback=("git:worktree-revert",),
        idempotency_key=context.idempotency_key,
        operator_boundary_evidence=(
            f"source-task:{source_hash}",
            f"runner-task-envelope:{task_envelope_hash}",
            context.target_state_ref,
        ),
    )
    operation = OperationIR(
        context.operation_id,
        REPOSITORY_CODEGEN_EFFECT,
        context.authorized_resource_refs,
        (REPOSITORY_CODEGEN_EFFECT,),
        context.idempotency_key,
    )
    return RepositoryCodegenPlan(
        runner_task=runner_task,
        context=context,
        universal_task=universal_task,
        operation=operation,
    )


def _validate_runner_task_for_repository_codegen(runner_task: RunnerTask) -> None:
    if runner_task.task_kind != "code_edit":
        raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_CODE_EDIT_REQUIRED")
    if runner_task.privacy_boundary != "PUBLIC_SAFE_REPOSITORY_ONLY":
        raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_PUBLIC_SAFE_REQUIRED")
    if not runner_task.allowed_files:
        raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_ALLOWED_FILES_REQUIRED")
    required = {REPOSITORY_CODEGEN_CAPABILITY, "repository_read", "test_execution"}
    if not required.issubset(set(runner_task.requested_capabilities)):
        raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_CAPABILITY_SCOPE_INVALID")
    seen: set[str] = set()
    for path in runner_task.allowed_files:
        if path in seen:
            raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_RESOURCE_SCOPE_INVALID")
        seen.add(path)
        if _protected_file(path):
            raise RepositoryCodegenBoundaryError("REPOSITORY_CODEGEN_PROTECTED_RESOURCE")


def _validate_grant_binding(
    runner_task: RunnerTask,
    context: RepositoryCodegenExecutionContext,
    grant: GreenExecutionGrant,
) -> None:
    if context.task_envelope_hash != hashlib.sha256(runner_task.to_json().encode("utf-8")).hexdigest():
        raise GreenExecutionError("REPOSITORY_CODEGEN_TASK_HASH_MISMATCH")
    if context.task_id != grant.task_id:
        raise GreenExecutionError("REPOSITORY_CODEGEN_GRANT_TASK_MISMATCH")
    expected = (
        context.operation_id,
        context.idempotency_key,
        context.adapter_id,
        context.target_state_ref,
        context.authorized_resource_refs,
        (REPOSITORY_CODEGEN_EFFECT,),
        tuple(context.required_capabilities),
    )
    actual = (
        grant.operation_id,
        grant.idempotency_key,
        grant.adapter_id,
        grant.target_state_ref,
        grant.planner_envelope.resources,
        grant.planner_envelope.effects,
        grant.planner_envelope.required_capabilities,
    )
    if actual != expected:
        raise GreenExecutionError("REPOSITORY_CODEGEN_GRANT_BINDING_MISMATCH")
    if grant.target_state_ref != f"git:{runner_task.base_sha}":
        raise GreenExecutionError("REPOSITORY_CODEGEN_TARGET_STATE_MISMATCH")
    if grant.planner_envelope_hash != planner_envelope_hash(grant.planner_envelope):
        raise GreenExecutionError("REPOSITORY_CODEGEN_PLANNER_HASH_MISMATCH")


def _validate_backend_result(
    context: RepositoryCodegenExecutionContext,
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
    _validate_touched_resources(result.touched_resource_refs, context.authorized_resource_refs)


def _validate_touched_resources(
    touched_resource_refs: tuple[str, ...],
    authorized_resource_refs: tuple[str, ...],
) -> None:
    if len(set(touched_resource_refs)) != len(touched_resource_refs):
        raise GreenExecutionError("REPOSITORY_CODEGEN_TOUCHED_RESOURCE_DUPLICATE")
    authorized = set(authorized_resource_refs)
    if any(ref not in authorized for ref in touched_resource_refs):
        raise GreenExecutionError("REPOSITORY_CODEGEN_TOUCHED_RESOURCE_OUT_OF_SCOPE")
    for ref in touched_resource_refs:
        if not _public_ref(ref):
            raise GreenExecutionError("REPOSITORY_CODEGEN_TOUCHED_RESOURCE_INVALID")


def _validation_refs(runner_task: RunnerTask) -> tuple[str, ...]:
    payload = runner_task.to_mapping()["validation_commands"]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return (f"validation:{digest}",)


def _protected_file(path: str) -> bool:
    return (
        path == "scripts/runner_poll_github_tasks.py"
        or path.startswith(".github/workflows/")
        or path.startswith("governance/")
        or path.startswith("project-registry/")
        or path.startswith("secrets/")
        or path.startswith("protected/")
    )


def _public_ref(value: str) -> bool:
    if not value or "\\" in value or ".." in value or any(ch.isspace() for ch in value):
        return False
    namespace, sep, tail = value.partition(":")
    return bool(sep and namespace and tail) and not tail.startswith(("/", "~"))

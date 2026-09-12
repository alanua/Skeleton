from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from core.runner_gate import RunnerGate
from core.runner_task import RunnerTask
from core.runner_vnext_contracts import OperationIR, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_execution import GreenAdapterExecutor, GreenExecutionError, GreenExecutionResult
from core.runner_vnext_execution_gate import GreenExecutionGrant


class LiveCodegenBindingError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SourceAdapterBinding:
    runner_task_hash: str
    source_task_ref: str
    adapter_id: str
    binding_hash: str
    task_id: str
    operation_id: str
    idempotency_key: str
    target_state_ref: str


@dataclass(frozen=True)
class LiveCodegenPlan:
    binding: SourceAdapterBinding
    task: UniversalTask
    operation: OperationIR


@dataclass(frozen=True)
class LiveCodegenBackendRequest:
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    runner_task_hash: str
    source_task_ref: str
    binding_hash: str


@dataclass(frozen=True)
class LiveCodegenBackendResult:
    touched_resource_refs: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    validation_status: str
    outcome: str = "SUCCEEDED"
    reason_code: str = "EXECUTION_PASS"
    rollback_requested: bool = False


class LiveCodegenBackend(Protocol):
    def execute(self, request: LiveCodegenBackendRequest) -> LiveCodegenBackendResult: ...


class LiveCodegenAdapter(GreenAdapterExecutor):
    """Typed vNext codegen adapter boundary; it validates scope and delegates no shell authority."""

    def __init__(self, *, plan: LiveCodegenPlan, backend: LiveCodegenBackend) -> None:
        self._plan = plan
        self._backend = backend

    def execute(self, grant: GreenExecutionGrant) -> GreenExecutionResult:
        validate_grant_matches_plan(grant, self._plan)
        request = LiveCodegenBackendRequest(
            operation_id=grant.operation_id,
            idempotency_key=grant.idempotency_key,
            adapter_id=grant.adapter_id,
            target_state_ref=grant.target_state_ref,
            resources=grant.planner_envelope.resources,
            effects=grant.planner_envelope.effects,
            required_capabilities=grant.planner_envelope.required_capabilities,
            runner_task_hash=self._plan.binding.runner_task_hash,
            source_task_ref=self._plan.binding.source_task_ref,
            binding_hash=self._plan.binding.binding_hash,
        )
        backend_result = self._backend.execute(request)
        return GreenExecutionResult(
            operation_id=grant.operation_id,
            idempotency_key=grant.idempotency_key,
            adapter_id=grant.adapter_id,
            target_state_ref=grant.target_state_ref,
            fence_token=grant.fence_token,
            resources=backend_result.touched_resource_refs,
            effects=grant.planner_envelope.effects,
            outcome=backend_result.outcome,
            reason_code=backend_result.reason_code,
            before_state_ref=backend_result.before_state_ref,
            after_state_ref=backend_result.after_state_ref,
            validation_status=backend_result.validation_status,
            rollback_requested=backend_result.rollback_requested,
        )


def compile_live_codegen_plan(
    runner_task: RunnerTask,
    *,
    source_task_ref: str,
    adapter_id: str,
) -> LiveCodegenPlan:
    _public_ref(source_task_ref, reason_code="LIVE_CODEGEN_SOURCE_REF_INVALID")
    _public_ref(adapter_id, reason_code="LIVE_CODEGEN_ADAPTER_ID_INVALID")
    if runner_task.task_kind not in {"code_edit", "code_generation"}:
        raise LiveCodegenBindingError("LIVE_CODEGEN_TASK_KIND_UNSUPPORTED")
    gate = RunnerGate()
    protected = tuple(path for path in runner_task.allowed_files if gate.is_protected_path(path))
    if protected:
        raise LiveCodegenBindingError("LIVE_CODEGEN_PROTECTED_TARGET")
    resources = tuple(f"repo:{path}" for path in runner_task.allowed_files)
    if not resources:
        raise LiveCodegenBindingError("LIVE_CODEGEN_RESOURCES_REQUIRED")

    binding = source_adapter_binding(runner_task, source_task_ref=source_task_ref, adapter_id=adapter_id)
    task = UniversalTask(
        task_id=binding.task_id,
        intent=f"runner_task:{runner_task.task_kind}",
        domain="github",
        target_resources=resources,
        required_capabilities=runner_task.requested_capabilities,
        privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE,
        expected_effects=("workspace_write",),
        validation=tuple(" ".join(command) for command in runner_task.validation_commands),
        rollback=("no_real_provider_invocation_in_live_codegen_slice",),
        idempotency_key=binding.idempotency_key,
        operator_boundary_evidence=(
            f"runner_task_sha256:{binding.runner_task_hash}",
            f"source_task_ref:{source_task_ref}",
            f"adapter_id:{adapter_id}",
        ),
    )
    operation = OperationIR(
        operation_id=binding.operation_id,
        kind="workspace_write",
        resources=resources,
        effects=("workspace_write",),
        idempotency_key=binding.idempotency_key,
    )
    return LiveCodegenPlan(binding=binding, task=task, operation=operation)


def source_adapter_binding(
    runner_task: RunnerTask,
    *,
    source_task_ref: str,
    adapter_id: str,
) -> SourceAdapterBinding:
    task_json = runner_task.to_json()
    runner_task_hash = hashlib.sha256(task_json.encode("utf-8")).hexdigest()
    payload = {
        "adapter_id": adapter_id,
        "runner_task_sha256": runner_task_hash,
        "source_task_ref": source_task_ref,
    }
    binding_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return SourceAdapterBinding(
        runner_task_hash=runner_task_hash,
        source_task_ref=source_task_ref,
        adapter_id=adapter_id,
        binding_hash=binding_hash,
        task_id=f"task:codegen-{binding_hash}",
        operation_id=f"op:codegen-{binding_hash}",
        idempotency_key=f"idem:codegen-{binding_hash}",
        target_state_ref=f"git:{runner_task.base_sha}",
    )


def validate_grant_matches_plan(grant: GreenExecutionGrant, plan: LiveCodegenPlan) -> None:
    expected = (
        plan.task.task_id,
        plan.operation.operation_id,
        plan.task.idempotency_key,
        plan.binding.adapter_id,
        plan.binding.target_state_ref,
        plan.operation.resources,
        plan.operation.effects,
        plan.task.required_capabilities,
    )
    actual = (
        grant.task_id,
        grant.operation_id,
        grant.idempotency_key,
        grant.adapter_id,
        grant.target_state_ref,
        grant.planner_envelope.resources,
        grant.planner_envelope.effects,
        grant.planner_envelope.required_capabilities,
    )
    if actual != expected:
        raise GreenExecutionError("LIVE_CODEGEN_GRANT_SCOPE_MISMATCH")
    if grant.planner_envelope.operation_id != grant.operation_id:
        raise GreenExecutionError("LIVE_CODEGEN_GRANT_SCOPE_MISMATCH")
    if grant.planner_envelope.idempotency_key != grant.idempotency_key:
        raise GreenExecutionError("LIVE_CODEGEN_GRANT_SCOPE_MISMATCH")
    if grant.planner_envelope.adapter_id != grant.adapter_id:
        raise GreenExecutionError("LIVE_CODEGEN_GRANT_SCOPE_MISMATCH")
    if grant.planner_envelope.target_state_ref != grant.target_state_ref:
        raise GreenExecutionError("LIVE_CODEGEN_GRANT_SCOPE_MISMATCH")


def validate_actual_touched_resources(
    touched_resource_refs: tuple[str, ...],
    *,
    authorized_resources: tuple[str, ...],
) -> tuple[str, ...]:
    if not touched_resource_refs:
        raise GreenExecutionError("LIVE_CODEGEN_TOUCHED_RESOURCES_REQUIRED")
    if len(set(touched_resource_refs)) != len(touched_resource_refs):
        raise GreenExecutionError("LIVE_CODEGEN_TOUCHED_RESOURCES_DUPLICATE")
    authorized = set(authorized_resources)
    for ref in touched_resource_refs:
        if not _repo_resource_ref(ref):
            raise GreenExecutionError("LIVE_CODEGEN_TOUCHED_RESOURCE_MALFORMED")
        if ref not in authorized:
            raise GreenExecutionError("LIVE_CODEGEN_TOUCHED_RESOURCE_OUT_OF_SCOPE")
    return touched_resource_refs


def _repo_resource_ref(value: str) -> bool:
    if not value.startswith("repo:"):
        return False
    tail = value.removeprefix("repo:")
    return bool(tail) and not tail.startswith(("/", "~")) and "\\" not in tail and ".." not in tail and not any(ch.isspace() for ch in value)


def _public_ref(value: str, *, reason_code: str) -> None:
    if not value or value.startswith(("/", "~")) or "\\" in value or ".." in value or ":" not in value or any(ch.isspace() for ch in value):
        raise LiveCodegenBindingError(reason_code)

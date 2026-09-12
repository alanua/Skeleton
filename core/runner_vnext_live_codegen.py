from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol

from core.runner_gate import RunnerGate
from core.runner_task import RunnerTask
from core.runner_vnext_authority import (
    ORDINARY_REPOSITORY_PRIVACY,
    ROUTE_CODE_GENERATION,
    BoundRunnerOperation,
    bind_runner_operation,
)
from core.runner_vnext_contracts import EffectClass
from core.runner_vnext_execution import (
    GreenAdapterExecutor,
    GreenExecutionError,
    GreenExecutionResult,
    validate_actual_touched_resources,
)
from core.runner_vnext_execution_gate import GreenExecutionGrant, planner_envelope_hash


class RunnerVNextLiveCodegenError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class MechanicalCodegenBackend(Protocol):
    def run_codegen(
        self,
        *,
        runner_task: RunnerTask,
        source_task_ref: str,
        grant: GreenExecutionGrant,
    ) -> GreenExecutionResult: ...


@dataclass(frozen=True)
class LiveCodegenPlan:
    bound: BoundRunnerOperation

    @property
    def runner_task_hash(self) -> str:
        assert self.bound.runner_task_hash is not None
        return self.bound.runner_task_hash

    @property
    def source_task_ref(self) -> str:
        return self.bound.source_task_ref

    @property
    def source_binding_hash(self) -> str:
        return self.bound.source_binding_hash


@dataclass(frozen=True)
class CodegenBackendReceipt:
    runner_task_hash: str
    source_task_ref: str
    source_binding_hash: str
    grant_operation_id: str
    grant_idempotency_key: str
    adapter_id: str
    result: GreenExecutionResult
    publication_attempted: bool = False


class LiveCodegenAdapter(GreenAdapterExecutor):
    """Typed codegen boundary. It carries no shell/argv/provider-selection authority."""

    def __init__(
        self,
        *,
        runner_task: RunnerTask,
        source_task_ref: str,
        backend: MechanicalCodegenBackend,
    ) -> None:
        self._runner_task = runner_task
        self._source_task_ref = source_task_ref
        self._backend = backend
        self._plan = compile_live_codegen_plan(
            runner_task,
            source_task_ref=source_task_ref,
        )

    @property
    def plan(self) -> LiveCodegenPlan:
        return self._plan

    def execute(self, grant: GreenExecutionGrant) -> GreenExecutionResult:
        _validate_codegen_grant(self._runner_task, self._source_task_ref, grant, self._plan)
        result = self._backend.run_codegen(
            runner_task=self._runner_task,
            source_task_ref=self._source_task_ref,
            grant=grant,
        )
        _validate_codegen_result(grant, result)
        return result


def immutable_runner_task_hash(runner_task: RunnerTask) -> str:
    return hashlib.sha256(runner_task.to_json().encode("utf-8")).hexdigest()


def compile_live_codegen_plan(
    runner_task: RunnerTask,
    *,
    source_task_ref: str,
) -> LiveCodegenPlan:
    if runner_task.privacy_boundary != ORDINARY_REPOSITORY_PRIVACY:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PUBLIC_REPOSITORY_PRIVACY_REQUIRED")
    if runner_task.task_kind != "code_edit":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_TASK_KIND_UNSUPPORTED")
    gate = RunnerGate()
    if any(gate.is_protected_path(path) for path in runner_task.allowed_files):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PROTECTED_TARGET")
    bound = bind_runner_operation(
        runner_task=runner_task,
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
        source_task_ref=source_task_ref,
    )
    if bound.policy_decision.effect_class is not EffectClass.GREEN:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GREEN_POLICY_REQUIRED")
    return LiveCodegenPlan(bound=bound)


def run_authorized_live_codegen(
    *,
    runner_task: RunnerTask,
    source_task_ref: str,
    grant: GreenExecutionGrant,
    backend: MechanicalCodegenBackend,
) -> CodegenBackendReceipt:
    plan = compile_live_codegen_plan(
        runner_task,
        source_task_ref=source_task_ref,
    )
    _validate_codegen_grant(runner_task, source_task_ref, grant, plan)
    result = backend.run_codegen(
        runner_task=runner_task,
        source_task_ref=source_task_ref,
        grant=grant,
    )
    _validate_codegen_result(grant, result)
    return CodegenBackendReceipt(
        runner_task_hash=plan.runner_task_hash,
        source_task_ref=source_task_ref,
        source_binding_hash=plan.source_binding_hash,
        grant_operation_id=grant.operation_id,
        grant_idempotency_key=grant.idempotency_key,
        adapter_id=grant.adapter_id,
        result=result,
    )


def codegen_public_projection(receipt: CodegenBackendReceipt) -> dict[str, object]:
    return {
        "schema": "skeleton.runner_vnext_live_codegen_receipt.v1",
        "runner_task_hash": receipt.runner_task_hash,
        "source_task_ref_hash": _text_hash(receipt.source_task_ref),
        "source_binding_hash": receipt.source_binding_hash,
        "operation_id_hash": _text_hash(receipt.grant_operation_id),
        "idempotency_key_hash": _text_hash(receipt.grant_idempotency_key),
        "adapter_id": receipt.adapter_id,
        "result_outcome": receipt.result.outcome,
        "result_validation_status": receipt.result.validation_status,
        "publication_attempted": receipt.publication_attempted,
    }


def _validate_codegen_grant(
    runner_task: RunnerTask,
    source_task_ref: str,
    grant: GreenExecutionGrant,
    plan: LiveCodegenPlan | None = None,
) -> None:
    expected_plan = plan or compile_live_codegen_plan(
        runner_task,
        source_task_ref=source_task_ref,
    )
    bound = expected_plan.bound
    if immutable_runner_task_hash(runner_task) != expected_plan.runner_task_hash:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RUNNER_TASK_HASH_MISMATCH")
    if source_task_ref != expected_plan.source_task_ref:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_SOURCE_TASK_REF_MISMATCH")
    if grant.lane != bound.binding.lane.value or grant.adapter_id != bound.binding.adapter_id:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GRANT_LANE_ADAPTER_MISMATCH")
    if not grant.execution_authorized or grant.effect_execution_started:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_FRESH_GRANT_REQUIRED")
    if grant.planner_envelope_hash != planner_envelope_hash(grant.planner_envelope):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PLANNER_HASH_MISMATCH")
    if grant.planner_envelope.effect_class is not EffectClass.GREEN:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GREEN_GRANT_REQUIRED")
    if grant.planner_envelope.privacy.value != "PUBLIC_SAFE":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PUBLIC_PRIVACY_REQUIRED")

    expected = (
        bound.universal_task.task_id,
        bound.operation_ir.operation_id,
        bound.operation_ir.idempotency_key,
        bound.binding.adapter_id,
        bound.target_state_ref,
        bound.operation_ir.resources,
        bound.operation_ir.effects,
        bound.universal_task.required_capabilities,
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
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GRANT_SCOPE_MISMATCH")
    envelope = grant.planner_envelope
    if (
        envelope.operation_id != grant.operation_id
        or envelope.idempotency_key != grant.idempotency_key
        or envelope.adapter_id != grant.adapter_id
        or envelope.target_state_ref != grant.target_state_ref
        or envelope.fence_token != grant.fence_token
    ):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GRANT_ENVELOPE_MISMATCH")
    if tuple(runner_task.requested_capabilities) != tuple(envelope.required_capabilities):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_CAPABILITY_BINDING_MISMATCH")


def _validate_codegen_result(
    grant: GreenExecutionGrant,
    result: GreenExecutionResult,
) -> None:
    expected = (
        grant.operation_id,
        grant.idempotency_key,
        grant.adapter_id,
        grant.target_state_ref,
        grant.fence_token,
        grant.planner_envelope.effects,
    )
    actual = (
        result.operation_id,
        result.idempotency_key,
        result.adapter_id,
        result.target_state_ref,
        result.fence_token,
        result.effects,
    )
    if actual != expected:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RESULT_BINDING_MISMATCH")
    try:
        validate_actual_touched_resources(
            result,
            authorized_resources=grant.planner_envelope.resources,
        )
    except GreenExecutionError as exc:
        raise RunnerVNextLiveCodegenError(exc.reason_code) from exc
    if result.outcome == "SUCCEEDED" and result.validation_status != "PASS":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_SUCCESS_REQUIRES_VALIDATION_PASS")
    if result.outcome != "SUCCEEDED" and result.validation_status != "FAIL":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_FAILURE_REQUIRES_VALIDATION_FAIL")


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

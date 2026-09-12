from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from core.runner_task import RunnerTask
from core.runner_vnext_authority import ORDINARY_REPOSITORY_PRIVACY
from core.runner_vnext_contracts import EffectClass
from core.runner_vnext_execution import GreenExecutionResult
from core.runner_vnext_execution_gate import GreenExecutionGrant, planner_envelope_hash


class RunnerVNextLiveCodegenError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class MechanicalCodegenBackend(Protocol):
    def run_codegen(self, *, runner_task: RunnerTask, grant: GreenExecutionGrant) -> GreenExecutionResult: ...


@dataclass(frozen=True)
class CodegenBackendReceipt:
    runner_task_hash: str
    grant_operation_id: str
    grant_idempotency_key: str
    adapter_id: str
    result: GreenExecutionResult
    publication_attempted: bool = False


def immutable_runner_task_hash(runner_task: RunnerTask) -> str:
    return hashlib.sha256(runner_task.to_json().encode("utf-8")).hexdigest()


def run_authorized_live_codegen(
    *,
    runner_task: RunnerTask,
    grant: GreenExecutionGrant,
    backend: MechanicalCodegenBackend,
) -> CodegenBackendReceipt:
    _validate_codegen_grant(runner_task, grant)
    result = backend.run_codegen(runner_task=runner_task, grant=grant)
    _validate_codegen_result(grant, result)
    return CodegenBackendReceipt(
        runner_task_hash=immutable_runner_task_hash(runner_task),
        grant_operation_id=grant.operation_id,
        grant_idempotency_key=grant.idempotency_key,
        adapter_id=grant.adapter_id,
        result=result,
    )


def codegen_public_projection(receipt: CodegenBackendReceipt) -> dict[str, object]:
    return {
        "schema": "skeleton.runner_vnext_live_codegen_receipt.v1",
        "runner_task_hash": receipt.runner_task_hash,
        "operation_id_hash": _text_hash(receipt.grant_operation_id),
        "idempotency_key_hash": _text_hash(receipt.grant_idempotency_key),
        "adapter_id": receipt.adapter_id,
        "result_outcome": receipt.result.outcome,
        "result_validation_status": receipt.result.validation_status,
        "publication_attempted": receipt.publication_attempted,
    }


def _validate_codegen_grant(runner_task: RunnerTask, grant: GreenExecutionGrant) -> None:
    if runner_task.privacy_boundary != ORDINARY_REPOSITORY_PRIVACY:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PUBLIC_REPOSITORY_PRIVACY_REQUIRED")
    if grant.lane != "codegen" or grant.adapter_id != "adapter:repo-codegen":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GRANT_LANE_ADAPTER_MISMATCH")
    if not grant.execution_authorized or grant.effect_execution_started:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_FRESH_GRANT_REQUIRED")
    if grant.planner_envelope_hash != planner_envelope_hash(grant.planner_envelope):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PLANNER_HASH_MISMATCH")
    if grant.planner_envelope.effect_class is not EffectClass.GREEN:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_GREEN_GRANT_REQUIRED")
    if grant.planner_envelope.privacy.value != "PUBLIC_SAFE":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_PUBLIC_PRIVACY_REQUIRED")
    if grant.planner_envelope.effects != ("workspace_write",):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_EFFECT_SCOPE_MISMATCH")
    task_resources = tuple(f"repo:{path}" for path in runner_task.allowed_files)
    if grant.planner_envelope.resources != task_resources:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RUNNER_TASK_RESOURCE_MISMATCH")
    if not set(runner_task.requested_capabilities).issubset(set(grant.planner_envelope.required_capabilities)):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_CAPABILITY_BINDING_MISMATCH")
    expected_target = f"git:{runner_task.base_sha}"
    if grant.target_state_ref != expected_target or grant.planner_envelope.target_state_ref != expected_target:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_TARGET_STATE_MISMATCH")
    if not grant.operation_id.startswith("op:") or not grant.idempotency_key.startswith("idem:"):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_AUTHORITY_IDENTITY_REQUIRED")


def _validate_codegen_result(grant: GreenExecutionGrant, result: GreenExecutionResult) -> None:
    if result.operation_id != grant.operation_id or result.idempotency_key != grant.idempotency_key:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RESULT_IDENTITY_MISMATCH")
    if result.adapter_id != grant.adapter_id or result.target_state_ref != grant.target_state_ref:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RESULT_BINDING_MISMATCH")
    if result.fence_token != grant.fence_token:
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RESULT_FENCE_MISMATCH")
    if result.effects != ("workspace_write",):
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_RESULT_EFFECT_SCOPE_MISMATCH")
    if result.outcome == "SUCCEEDED" and result.validation_status != "PASS":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_SUCCESS_REQUIRES_VALIDATION_PASS")
    if result.outcome != "SUCCEEDED" and result.validation_status != "FAIL":
        raise RunnerVNextLiveCodegenError("LIVE_CODEGEN_FAILURE_REQUIRES_VALIDATION_FAIL")


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

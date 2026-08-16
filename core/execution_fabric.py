from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.executor_registry import ExecutorRecord
from core.failure_taxonomy import require_failure_class
from core.model_registry import ModelRecord
from core.model_selector import TaskFitRequest, rank_models


class ExecutionFabricError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeliverableContract:
    min_changed_files: int = 0
    required_artifacts: tuple[str, ...] = ()
    require_tests: bool = False
    require_validation: bool = True
    protected_final_action: bool = False

    def __post_init__(self) -> None:
        if self.min_changed_files < 0:
            raise ExecutionFabricError("invalid_min_changed_files")


@dataclass(frozen=True, slots=True)
class TaskProfile:
    operation: str
    task_class: str
    required_executor_capabilities: tuple[str, ...]
    required_model_capabilities: tuple[tuple[str, float], ...]
    privacy_class: str
    data_class: str
    risk_class: str
    side_effect_class: str
    deliverable: DeliverableContract
    validation_id: str
    budget_policy_ref: str
    timeout_policy_ref: str
    retry_policy_ref: str
    max_cost_usd: float
    max_tokens: int
    timeout_seconds: int
    max_attempts: int

    def __post_init__(self) -> None:
        if not self.operation or not self.task_class or not self.validation_id:
            raise ExecutionFabricError("profile_identity_required")
        if not self.required_executor_capabilities:
            raise ExecutionFabricError("executor_capabilities_required")
        for capability_id, minimum in self.required_model_capabilities:
            if not capability_id or not 0.0 <= float(minimum) <= 1.0:
                raise ExecutionFabricError("invalid_model_capability_threshold")
        if self.max_cost_usd < 0 or self.max_tokens < 0 or self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ExecutionFabricError("invalid_profile_limits")
        if not all((self.privacy_class, self.data_class, self.risk_class, self.side_effect_class)):
            raise ExecutionFabricError("profile_policy_class_required")

    @property
    def model_capability_map(self) -> dict[str, float]:
        return dict(self.required_model_capabilities)


@dataclass(frozen=True, slots=True)
class ExecutionBinding:
    binding_id: str
    executor_id: str
    model_binding_kind: str
    model_id: str | None
    model_alias: str | None
    validation_id: str
    privacy_class: str
    side_effect_class: str
    permissions: tuple[str, ...]
    credential_aliases: tuple[str, ...]
    max_cost_usd: float
    max_tokens: int
    timeout_seconds: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class RouteLease:
    lease_id: str
    task_profile_hash: str
    binding_id: str
    permissions: tuple[str, ...]
    validation_id: str
    max_cost_usd: float
    max_tokens: int
    timeout_seconds: int
    max_attempts: int
    expires_at_epoch: int
    lease_hash: str


@dataclass(frozen=True, slots=True)
class DeliverableEvidence:
    executor_rc: int
    changed_files: tuple[str, ...] = ()
    produced_artifacts: tuple[str, ...] = ()
    tests_passed: bool | None = None
    validation_passed: bool | None = None
    operator_approved: bool = False


@dataclass(frozen=True, slots=True)
class DeliverableValidation:
    accepted: bool
    completion_status: str
    failure_class: str | None
    reason_codes: tuple[str, ...]
    evidence_hash: str


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def task_profile_hash(profile: TaskProfile) -> str:
    return _canonical_hash(asdict(profile))


def derive_task_profile(typed_contract: Mapping[str, Any], policy: Mapping[str, Any]) -> TaskProfile:
    """Derive authority only from typed fields plus code-owned policy.

    Free-form prose keys are intentionally ignored and cannot select a model, provider,
    executor, endpoint, budget or retry behavior.
    """
    model_caps_raw = typed_contract.get("required_model_capabilities", {})
    deliverable_raw = typed_contract.get("deliverable", {})
    if not isinstance(model_caps_raw, Mapping) or not isinstance(deliverable_raw, Mapping):
        raise ExecutionFabricError("typed_contract_invalid")
    required_model_capabilities = tuple(
        sorted((str(key), float(value)) for key, value in model_caps_raw.items())
    )
    return TaskProfile(
        operation=str(typed_contract.get("operation", "")),
        task_class=str(typed_contract.get("task_class", "")),
        required_executor_capabilities=tuple(
            sorted(str(item) for item in typed_contract.get("required_executor_capabilities", ()))
        ),
        required_model_capabilities=required_model_capabilities,
        privacy_class=str(typed_contract.get("privacy_class", "")),
        data_class=str(typed_contract.get("data_class", typed_contract.get("privacy_class", ""))),
        risk_class=str(typed_contract.get("risk_class", "")),
        side_effect_class=str(typed_contract.get("side_effect_class", "")),
        deliverable=DeliverableContract(
            min_changed_files=int(deliverable_raw.get("min_changed_files", 0)),
            required_artifacts=tuple(sorted(str(item) for item in deliverable_raw.get("required_artifacts", ()))),
            require_tests=bool(deliverable_raw.get("require_tests", False)),
            require_validation=bool(deliverable_raw.get("require_validation", True)),
            protected_final_action=bool(deliverable_raw.get("protected_final_action", False)),
        ),
        validation_id=str(typed_contract.get("validation_id", "")),
        budget_policy_ref=str(policy.get("budget_policy_ref", "")),
        timeout_policy_ref=str(policy.get("timeout_policy_ref", "")),
        retry_policy_ref=str(policy.get("retry_policy_ref", "")),
        max_cost_usd=float(policy.get("max_cost_usd", 0.0)),
        max_tokens=int(policy.get("max_tokens", 0)),
        timeout_seconds=int(policy.get("timeout_seconds", 1)),
        max_attempts=int(policy.get("max_attempts", 1)),
    )


def _embedded_model_satisfies(executor: ExecutorRecord, requirements: Mapping[str, float]) -> bool:
    capabilities = executor.embedded_model_capabilities or {}
    return all(float(capabilities.get(capability_id, -1.0)) >= minimum for capability_id, minimum in requirements.items())


def _binding(
    profile: TaskProfile,
    executor: ExecutorRecord,
    *,
    kind: str,
    model_id: str | None,
    model_alias: str | None,
) -> ExecutionBinding:
    identity = {
        "profile": task_profile_hash(profile),
        "executor_id": executor.executor_id,
        "kind": kind,
        "model_id": model_id,
        "model_alias": model_alias,
        "validation_id": profile.validation_id,
    }
    binding_id = "binding-" + _canonical_hash(identity)[:32]
    return ExecutionBinding(
        binding_id=binding_id,
        executor_id=executor.executor_id,
        model_binding_kind=kind,
        model_id=model_id,
        model_alias=model_alias,
        validation_id=profile.validation_id,
        privacy_class=profile.privacy_class,
        side_effect_class=profile.side_effect_class,
        permissions=tuple(sorted(profile.required_executor_capabilities)),
        credential_aliases=tuple(sorted(executor.credential_aliases)),
        max_cost_usd=profile.max_cost_usd,
        max_tokens=profile.max_tokens,
        timeout_seconds=min(profile.timeout_seconds, executor.timeout_seconds),
        max_attempts=profile.max_attempts,
    )


def build_execution_bindings(
    profile: TaskProfile,
    executors: Sequence[ExecutorRecord],
    models: Sequence[ModelRecord],
    *,
    production: bool = True,
) -> tuple[ExecutionBinding, ...]:
    requirements = profile.model_capability_map
    eligible_executors = [
        executor
        for executor in executors
        if executor.supports_task(
            profile.task_class,
            profile.required_executor_capabilities,
            profile.side_effect_class,
            profile.privacy_class,
        )
    ]
    candidates: list[tuple[int, int, int, str, ExecutionBinding]] = []
    for executor in sorted(eligible_executors, key=lambda item: (item.priority_rank, item.executor_id)):
        for kind in executor.binding_kinds:
            if kind == "NO_MODEL":
                if requirements:
                    continue
                binding = _binding(profile, executor, kind=kind, model_id=None, model_alias=None)
                candidates.append((executor.priority_rank, 0, 0, binding.binding_id, binding))
                continue
            if kind == "EMBEDDED_MODEL":
                if not _embedded_model_satisfies(executor, requirements):
                    continue
                binding = _binding(profile, executor, kind=kind, model_id=None, model_alias="embedded")
                candidates.append((executor.priority_rank, 1, 0, binding.binding_id, binding))
                continue
            if kind != "EXTERNAL_MODEL" or not requirements:
                continue
            request = TaskFitRequest(
                task_class=profile.task_class,
                required_capabilities=requirements,
                privacy_class=profile.privacy_class,
                prefer_local=True,
                production_only=production,
            )
            ranked = rank_models(tuple(models), request)
            for model_rank, model in enumerate(ranked):
                if model.provider_family not in executor.compatible_model_provider_families:
                    continue
                binding = _binding(
                    profile,
                    executor,
                    kind=kind,
                    model_id=model.model_id,
                    model_alias=None,
                )
                candidates.append((executor.priority_rank, 2, model_rank, binding.binding_id, binding))
    return tuple(item[-1] for item in sorted(candidates, key=lambda item: item[:-1]))


def make_route_lease(profile: TaskProfile, binding: ExecutionBinding, *, expires_at_epoch: int) -> RouteLease:
    if expires_at_epoch <= 0:
        raise ExecutionFabricError("lease_expiry_required")
    payload = {
        "task_profile_hash": task_profile_hash(profile),
        "binding_id": binding.binding_id,
        "permissions": list(binding.permissions),
        "validation_id": binding.validation_id,
        "max_cost_usd": binding.max_cost_usd,
        "max_tokens": binding.max_tokens,
        "timeout_seconds": binding.timeout_seconds,
        "max_attempts": binding.max_attempts,
        "expires_at_epoch": expires_at_epoch,
    }
    lease_hash = _canonical_hash(payload)
    return RouteLease(
        lease_id="lease-" + lease_hash[:32],
        task_profile_hash=str(payload["task_profile_hash"]),
        binding_id=binding.binding_id,
        permissions=binding.permissions,
        validation_id=binding.validation_id,
        max_cost_usd=binding.max_cost_usd,
        max_tokens=binding.max_tokens,
        timeout_seconds=binding.timeout_seconds,
        max_attempts=binding.max_attempts,
        expires_at_epoch=expires_at_epoch,
        lease_hash=lease_hash,
    )


def validate_deliverable(profile: TaskProfile, evidence: DeliverableEvidence) -> DeliverableValidation:
    evidence_hash = _canonical_hash(asdict(evidence))
    contract = profile.deliverable
    if len(evidence.changed_files) < contract.min_changed_files:
        failure = require_failure_class("DELIVERABLE_MISSING")
        return DeliverableValidation(False, "REJECTED", failure, ("required_changed_files_missing",), evidence_hash)
    missing_artifacts = sorted(set(contract.required_artifacts) - set(evidence.produced_artifacts))
    if missing_artifacts:
        failure = require_failure_class("DELIVERABLE_MISSING")
        return DeliverableValidation(False, "REJECTED", failure, ("required_artifact_missing",), evidence_hash)
    if contract.require_tests and evidence.tests_passed is not True:
        failure = require_failure_class("VALIDATION_FAILED")
        return DeliverableValidation(False, "REJECTED", failure, ("tests_not_passed",), evidence_hash)
    if contract.require_validation and evidence.validation_passed is not True:
        failure = require_failure_class("VALIDATION_FAILED")
        return DeliverableValidation(False, "REJECTED", failure, ("deliverable_validation_not_passed",), evidence_hash)
    if contract.protected_final_action and not evidence.operator_approved:
        return DeliverableValidation(False, "NEEDS_OPERATOR", None, ("protected_action_requires_operator",), evidence_hash)
    return DeliverableValidation(True, "DONE", None, (), evidence_hash)

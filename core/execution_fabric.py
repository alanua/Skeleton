from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Mapping, Sequence

from core.executor_registry import ExecutorRecord
from core.failure_taxonomy import FailureClass
from core.model_registry import ModelRecord
from core.model_selector import TaskFitRequest, rank_models


class ExecutionFabricError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeliverableContract:
    require_changed_files: bool = False
    minimum_changed_files: int = 0
    minimum_artifacts: int = 0
    require_tests_passed: bool = False

    def __post_init__(self) -> None:
        if self.minimum_changed_files < 0 or self.minimum_artifacts < 0:
            raise ExecutionFabricError("invalid_deliverable_minimum")
        if self.require_changed_files and self.minimum_changed_files < 1:
            object.__setattr__(self, "minimum_changed_files", 1)


@dataclass(frozen=True, slots=True)
class TaskProfile:
    operation: str
    task_class: str
    required_executor_capabilities: tuple[str, ...]
    required_model_capabilities: tuple[tuple[str, float], ...]
    privacy_class: str
    side_effect_class: str
    deliverable_contract: DeliverableContract
    validation_id: str
    budget_ref: str
    timeout_seconds: int
    retry_policy_ref: str
    permissions: tuple[str, ...]
    max_attempts: int = 1
    max_tokens: int = 0
    requires_operator: bool = False

    def __post_init__(self) -> None:
        if not self.operation or not self.task_class or not self.privacy_class:
            raise ExecutionFabricError("task_profile_identity_required")
        if not self.validation_id or not self.budget_ref or not self.retry_policy_ref:
            raise ExecutionFabricError("task_profile_policy_ref_required")
        if self.timeout_seconds <= 0 or self.max_attempts <= 0 or self.max_tokens < 0:
            raise ExecutionFabricError("task_profile_limit_invalid")
        for capability, minimum in self.required_model_capabilities:
            if not capability or not 0.0 <= float(minimum) <= 1.0:
                raise ExecutionFabricError("model_capability_threshold_invalid")

    @property
    def model_capability_map(self) -> dict[str, float]:
        return dict(self.required_model_capabilities)


_TYPED_PROFILE_FIELDS = frozenset(
    {
        "operation",
        "task_class",
        "required_executor_capabilities",
        "required_model_capabilities",
        "privacy_class",
        "side_effect_class",
        "deliverable_contract",
        "validation_id",
        "budget_ref",
        "timeout_seconds",
        "retry_policy_ref",
        "permissions",
        "max_attempts",
        "max_tokens",
        "requires_operator",
    }
)


def task_profile_from_contract(contract: Mapping[str, object]) -> TaskProfile:
    """Build authority only from the typed contract, never from free-form prose."""
    unknown = set(contract) - _TYPED_PROFILE_FIELDS
    if unknown:
        raise ExecutionFabricError("unknown_task_profile_authority_field")
    raw_models = contract.get("required_model_capabilities", {})
    if not isinstance(raw_models, Mapping):
        raise ExecutionFabricError("required_model_capabilities_mapping_required")
    raw_deliverable = contract.get("deliverable_contract", {})
    if not isinstance(raw_deliverable, Mapping):
        raise ExecutionFabricError("deliverable_contract_mapping_required")
    return TaskProfile(
        operation=str(contract.get("operation", "")),
        task_class=str(contract.get("task_class", "")),
        required_executor_capabilities=tuple(sorted(str(item) for item in contract.get("required_executor_capabilities", ()))),
        required_model_capabilities=tuple(sorted((str(key), float(value)) for key, value in raw_models.items())),
        privacy_class=str(contract.get("privacy_class", "")),
        side_effect_class=str(contract.get("side_effect_class", "")),
        deliverable_contract=DeliverableContract(
            require_changed_files=bool(raw_deliverable.get("require_changed_files", False)),
            minimum_changed_files=int(raw_deliverable.get("minimum_changed_files", 0)),
            minimum_artifacts=int(raw_deliverable.get("minimum_artifacts", 0)),
            require_tests_passed=bool(raw_deliverable.get("require_tests_passed", False)),
        ),
        validation_id=str(contract.get("validation_id", "")),
        budget_ref=str(contract.get("budget_ref", "")),
        timeout_seconds=int(contract.get("timeout_seconds", 0)),
        retry_policy_ref=str(contract.get("retry_policy_ref", "")),
        permissions=tuple(sorted(str(item) for item in contract.get("permissions", ()))),
        max_attempts=int(contract.get("max_attempts", 1)),
        max_tokens=int(contract.get("max_tokens", 0)),
        requires_operator=bool(contract.get("requires_operator", False)),
    )


def task_profile_hash(profile: TaskProfile) -> str:
    payload = asdict(profile)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionBinding:
    binding_id: str
    executor_id: str
    model_binding_kind: str
    model_id: str | None
    validation_id: str
    privacy_class: str
    side_effect_class: str
    budget_ref: str
    timeout_seconds: int
    credential_aliases: tuple[str, ...]


def _binding_id(executor_id: str, kind: str, model_id: str | None, profile: TaskProfile) -> str:
    payload = {
        "executor_id": executor_id,
        "kind": kind,
        "model_id": model_id,
        "profile": task_profile_hash(profile),
    }
    return "binding-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def build_execution_bindings(
    profile: TaskProfile,
    executors: Sequence[ExecutorRecord],
    models: Sequence[ModelRecord],
    *,
    production: bool = True,
) -> tuple[ExecutionBinding, ...]:
    model_required = bool(profile.required_model_capabilities)
    ranked_models: tuple[ModelRecord, ...] = ()
    if model_required:
        ranked_models = rank_models(
            tuple(models),
            TaskFitRequest(
                task_class=profile.task_class,
                required_capabilities=profile.model_capability_map,
                privacy_class=profile.privacy_class,
                production_only=production,
            ),
        )

    candidates: list[tuple[tuple[object, ...], ExecutionBinding]] = []
    for executor in executors:
        timeout = min(profile.timeout_seconds, executor.max_timeout_seconds)
        if not model_required and executor.supports(
            task_class=profile.task_class,
            capabilities=profile.required_executor_capabilities,
            privacy_class=profile.privacy_class,
            side_effect_class=profile.side_effect_class,
            binding_kind="NO_MODEL",
        ):
            binding = ExecutionBinding(
                binding_id=_binding_id(executor.executor_id, "NO_MODEL", None, profile),
                executor_id=executor.executor_id,
                model_binding_kind="NO_MODEL",
                model_id=None,
                validation_id=profile.validation_id,
                privacy_class=profile.privacy_class,
                side_effect_class=profile.side_effect_class,
                budget_ref=profile.budget_ref,
                timeout_seconds=timeout,
                credential_aliases=executor.credential_aliases,
            )
            candidates.append(((executor.preference_rank, 0, executor.executor_id, ""), binding))

        if model_required and executor.supports(
            task_class=profile.task_class,
            capabilities=profile.required_executor_capabilities,
            privacy_class=profile.privacy_class,
            side_effect_class=profile.side_effect_class,
            binding_kind="EMBEDDED_MODEL",
        ) and all(capability in executor.embedded_model_capabilities for capability in profile.model_capability_map):
            alias = executor.embedded_model_alias
            binding = ExecutionBinding(
                binding_id=_binding_id(executor.executor_id, "EMBEDDED_MODEL", alias, profile),
                executor_id=executor.executor_id,
                model_binding_kind="EMBEDDED_MODEL",
                model_id=alias,
                validation_id=profile.validation_id,
                privacy_class=profile.privacy_class,
                side_effect_class=profile.side_effect_class,
                budget_ref=profile.budget_ref,
                timeout_seconds=timeout,
                credential_aliases=executor.credential_aliases,
            )
            candidates.append(((executor.preference_rank, 1, executor.executor_id, alias or ""), binding))

        if model_required and executor.supports(
            task_class=profile.task_class,
            capabilities=profile.required_executor_capabilities,
            privacy_class=profile.privacy_class,
            side_effect_class=profile.side_effect_class,
            binding_kind="EXTERNAL_MODEL",
        ):
            for model_rank, model in enumerate(ranked_models):
                if executor.supported_model_provider_families and model.provider_family not in executor.supported_model_provider_families:
                    continue
                binding = ExecutionBinding(
                    binding_id=_binding_id(executor.executor_id, "EXTERNAL_MODEL", model.model_id, profile),
                    executor_id=executor.executor_id,
                    model_binding_kind="EXTERNAL_MODEL",
                    model_id=model.model_id,
                    validation_id=profile.validation_id,
                    privacy_class=profile.privacy_class,
                    side_effect_class=profile.side_effect_class,
                    budget_ref=profile.budget_ref,
                    timeout_seconds=timeout,
                    credential_aliases=executor.credential_aliases,
                )
                candidates.append(((executor.preference_rank, 2, model_rank, executor.executor_id, model.model_id), binding))
    return tuple(binding for _, binding in sorted(candidates, key=lambda item: item[0]))


@dataclass(frozen=True, slots=True)
class RouteLease:
    binding_id: str
    task_profile_hash: str
    permissions: tuple[str, ...]
    validation_id: str
    budget_ref: str
    max_tokens: int
    timeout_seconds: int
    max_attempts: int
    expires_at: str
    lease_hash: str


def build_route_lease(profile: TaskProfile, binding: ExecutionBinding, *, expires_at: str) -> RouteLease:
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionFabricError("lease_expiry_invalid") from exc
    if parsed.tzinfo is None:
        raise ExecutionFabricError("lease_expiry_timezone_required")
    payload = {
        "binding_id": binding.binding_id,
        "task_profile_hash": task_profile_hash(profile),
        "permissions": profile.permissions,
        "validation_id": binding.validation_id,
        "budget_ref": binding.budget_ref,
        "max_tokens": profile.max_tokens,
        "timeout_seconds": binding.timeout_seconds,
        "max_attempts": profile.max_attempts,
        "expires_at": parsed.astimezone(UTC).isoformat(),
    }
    lease_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RouteLease(**payload, lease_hash=lease_hash)


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    rc: int
    changed_files: tuple[str, ...] = ()
    artifact_count: int = 0
    tests_passed: bool = False
    validation_passed: bool = False
    validation_head_sha: str | None = None
    current_head_sha: str | None = None
    protected_changed_files: tuple[str, ...] = ()
    high_risk: bool = False


@dataclass(frozen=True, slots=True)
class DeliverableValidation:
    accepted: bool
    failure_class: str | None
    final_action: str
    changed_files_count: int
    artifact_count: int


@dataclass(frozen=True, slots=True)
class TerminalSuccessFinalization:
    status: str
    reason: str
    project_done_label: bool


def finalize_terminal_success(validation: DeliverableValidation, evidence: AttemptEvidence) -> TerminalSuccessFinalization:
    """Canonical terminal-success boundary.

    This is the only control-plane decision that can produce canonical DONE.
    It requires an accepted deliverable validation receipt bound to the exact
    current head. Protected or high-risk accepted deliverables stop at operator
    review instead of being projected as DONE.
    """
    if not validation.accepted:
        return TerminalSuccessFinalization("BLOCKED", validation.failure_class or "validation_not_accepted", False)
    if not evidence.validation_head_sha or not evidence.current_head_sha:
        return TerminalSuccessFinalization("BLOCKED", "exact_head_validation_missing", False)
    if evidence.validation_head_sha.lower() != evidence.current_head_sha.lower():
        return TerminalSuccessFinalization("BLOCKED", "stale_validation_head", False)
    if validation.final_action == "NEEDS_OPERATOR" or evidence.protected_changed_files or evidence.high_risk:
        return TerminalSuccessFinalization("NEEDS_OPERATOR", "operator_review_required", False)
    if validation.final_action != "DONE":
        return TerminalSuccessFinalization("BLOCKED", "unsupported_final_action", False)
    return TerminalSuccessFinalization("DONE", "accepted_exact_head", True)


def validate_deliverable(profile: TaskProfile, evidence: AttemptEvidence) -> DeliverableValidation:
    contract = profile.deliverable_contract
    changed_count = len(set(evidence.changed_files))
    if contract.require_changed_files and changed_count < contract.minimum_changed_files:
        return DeliverableValidation(False, FailureClass.DELIVERABLE_MISSING.value, "RETRY_OR_ESCALATE", changed_count, evidence.artifact_count)
    if evidence.artifact_count < contract.minimum_artifacts:
        return DeliverableValidation(False, FailureClass.DELIVERABLE_MISSING.value, "RETRY_OR_ESCALATE", changed_count, evidence.artifact_count)
    if contract.require_tests_passed and not evidence.tests_passed:
        return DeliverableValidation(False, FailureClass.VALIDATION_FAILED.value, "RETRY_OR_ESCALATE", changed_count, evidence.artifact_count)
    if not evidence.validation_passed:
        return DeliverableValidation(False, FailureClass.VALIDATION_FAILED.value, "RETRY_OR_ESCALATE", changed_count, evidence.artifact_count)
    if evidence.rc != 0:
        return DeliverableValidation(False, FailureClass.NO_PROGRESS.value, "RETRY_OR_ESCALATE", changed_count, evidence.artifact_count)
    final_action = "NEEDS_OPERATOR" if profile.requires_operator else "DONE"
    return DeliverableValidation(True, None, final_action, changed_count, evidence.artifact_count)

from __future__ import annotations

from dataclasses import dataclass

from core.runner_vnext_contracts import (
    EffectClass,
    OperationIR,
    PolicyInput,
    PrivacyClass,
    Reversibility,
    UniversalTask,
)
from core.runner_gate import RunnerGate


COMPATIBILITY_DELETION_CONDITION = (
    "Delete this adapter after validated shadow parity and operator-approved vNext cutover; "
    "it must never become an alternate authoritative task model."
)


class CompatError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LegacyTaskObservation:
    source_task_ref: str
    target_state_ref: str
    repo: str
    branch: str
    task_kind: str
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    privacy_boundary: str
    legacy_effect_class: EffectClass | None
    legacy_status: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdaptedLegacyTask:
    task: UniversalTask
    operation: OperationIR
    policy_input: PolicyInput


_KIND_MAP: dict[str, tuple[str, str, Reversibility]] = {
    "code_edit": ("workspace_write", "workspace_write", Reversibility.REVERSIBLE),
    "code_generation": ("workspace_write", "workspace_write", Reversibility.REVERSIBLE),
    "diagnostic": ("read", "read", Reversibility.REVERSIBLE),
    "publish": ("draft_pr_open", "draft_pr_open", Reversibility.BOUNDED_REVERSIBLE),
    "private_memory": ("private_compute", "private_compute", Reversibility.REVERSIBLE),
}

def adapt_legacy_task(observation: LegacyTaskObservation) -> AdaptedLegacyTask:
    _validate_observation(observation)
    try:
        operation_kind, effect, reversibility = _KIND_MAP[observation.task_kind]
    except KeyError as exc:
        raise CompatError("LEGACY_TASK_KIND_UNSUPPORTED") from exc

    privacy = _privacy(observation.privacy_boundary)
    resources = tuple(_resource_ref(path) for path in observation.allowed_files)
    if observation.task_kind == "private_memory" and privacy is not PrivacyClass.PRIVATE:
        raise CompatError("LEGACY_PRIVATE_TASK_PRIVACY_MISMATCH")

    task = UniversalTask(
        task_id=observation.source_task_ref,
        intent=f"legacy:{observation.task_kind}",
        domain="legacy_runner",
        target_resources=resources,
        required_capabilities=observation.requested_capabilities,
        privacy=privacy,
        reversibility=reversibility,
        expected_effects=(effect,),
        validation=("shadow_parity_only",),
        rollback=("no_effects_executed",),
        idempotency_key=f"shadow:{observation.source_task_ref}@{observation.target_state_ref}",
        operator_boundary_evidence=observation.evidence_refs,
    )
    operation = OperationIR(
        operation_id=f"shadow:{observation.source_task_ref}",
        kind=operation_kind,
        resources=resources,
        effects=(effect,),
        idempotency_key=task.idempotency_key,
    )
    return AdaptedLegacyTask(
        task=task,
        operation=operation,
        policy_input=PolicyInput(
            operation=operation,
            reversibility=reversibility,
            privacy=privacy,
            target_state_ref=observation.target_state_ref,
            operator_boundary_evidence=observation.evidence_refs,
        ),
    )


def _validate_observation(observation: LegacyTaskObservation) -> None:
    for value in (observation.source_task_ref, observation.target_state_ref, observation.repo, observation.branch, observation.legacy_status):
        if not value:
            raise CompatError("LEGACY_REQUIRED_FIELD_MISSING")
    if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-" for ch in observation.legacy_status):
        raise CompatError("LEGACY_STATUS_INVALID")
    if "/" not in observation.repo or observation.repo.startswith(("/", "~")) or ".." in observation.repo:
        raise CompatError("LEGACY_REPO_INVALID")
    if observation.branch.startswith(("/", "~")) or ".." in observation.branch or any(ch.isspace() for ch in observation.branch):
        raise CompatError("LEGACY_BRANCH_INVALID")
    _public_ref(observation.source_task_ref)
    _public_ref(observation.target_state_ref)
    for ref in observation.evidence_refs:
        _public_ref(ref)
    if not observation.requested_capabilities:
        raise CompatError("LEGACY_CAPABILITIES_REQUIRED")
    if not observation.allowed_files:
        raise CompatError("LEGACY_ALLOWED_FILES_REQUIRED")
    for path in observation.allowed_files:
        if not path or path.startswith(("/", "~")) or "\\" in path or ".." in path:
            raise CompatError("LEGACY_REPOSITORY_PATH_INVALID")


def _privacy(value: str) -> PrivacyClass:
    normalized = value.upper()
    if normalized in {"PUBLIC_SAFE", "PUBLIC_SAFE_CODE_AND_TESTS_ONLY", "PUBLIC_SAFE_REPOSITORY_ONLY"}:
        return PrivacyClass.PUBLIC_SAFE
    if normalized in {"PRIVATE", "PRIVATE_LOCAL_ONLY", "PRIVATE_MEMORY"}:
        return PrivacyClass.PRIVATE
    raise CompatError("LEGACY_PRIVACY_BOUNDARY_UNSUPPORTED")


def _resource_ref(path: str) -> str:
    protected = RunnerGate().is_protected_path(path)
    prefix = "protected" if protected else "repo"
    return f"{prefix}:{path}"


def _public_ref(value: str) -> None:
    if value.startswith(("/", "~")) or "\\" in value or ".." in value or ":" not in value:
        raise CompatError("PUBLIC_SAFE_REF_REQUIRED")

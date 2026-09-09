from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EffectClass(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class PrivacyClass(str, Enum):
    PUBLIC_SAFE = "PUBLIC_SAFE"
    PRIVATE = "PRIVATE"


class Reversibility(str, Enum):
    REVERSIBLE = "REVERSIBLE"
    BOUNDED_REVERSIBLE = "BOUNDED_REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"


GREEN_EFFECTS = frozenset({
    "read", "workspace_write", "branch_commit_push", "draft_pr_open", "test",
    "validate", "bounded_service_restart", "artifact_transform", "private_compute",
    "mailbox_label_sort", "workspace_bind",
})
YELLOW_EFFECTS = frozenset({
    "protected_edit", "privileged_maintenance", "service_configuration",
    "control_plane_change", "external_send",
})
RED_EFFECTS = frozenset({
    "protected_merge", "deploy", "runtime_activation", "secret_export",
    "secret_rotation", "firmware_flash", "ota", "destructive_storage",
    "destructive_account", "financial_commitment", "legal_commitment",
})
ORDINARY_OPERATION_KINDS = GREEN_EFFECTS
STATE_BOUND_MUTATION_KINDS = frozenset({
    "workspace_write", "branch_commit_push", "draft_pr_open", "bounded_service_restart",
    "artifact_transform", "mailbox_label_sort", "workspace_bind",
})
RED_RESOURCE_PREFIXES = ("secret:", "deploy:", "firmware:", "finance:", "legal:")
YELLOW_RESOURCE_PREFIXES = ("protected:", "control:", "service-config:")


@dataclass(frozen=True)
class UniversalTask:
    task_id: str
    intent: str
    domain: str
    target_resources: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    privacy: PrivacyClass
    reversibility: Reversibility
    expected_effects: tuple[str, ...]
    validation: tuple[str, ...]
    rollback: tuple[str, ...]
    idempotency_key: str
    operator_boundary_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationIR:
    operation_id: str
    kind: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.kind not in ORDINARY_OPERATION_KINDS:
            raise ValueError("ORDINARY_OPERATION_KIND_REQUIRED")


@dataclass(frozen=True)
class PolicyInput:
    operation: OperationIR
    reversibility: Reversibility
    privacy: PrivacyClass
    target_state_ref: str | None
    operator_boundary_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    effect_class: EffectClass
    reason_code: str
    separate_privileged_pep_required: bool


@dataclass(frozen=True)
class VerificationReceipt:
    operation_id: str
    idempotency_key: str
    reason_code: str
    validation_status: str
    touched_resources: tuple[str, ...]
    before_state_ref: str | None = None
    after_state_ref: str | None = None


def classify_policy(policy_input: PolicyInput) -> PolicyDecision:
    op = policy_input.operation
    effects = frozenset(op.effects)
    resources = tuple(op.resources)

    if not effects or effects - (GREEN_EFFECTS | YELLOW_EFFECTS | RED_EFFECTS):
        return PolicyDecision(EffectClass.YELLOW, "UNRECOGNIZED_EFFECT_FAIL_CLOSED", True)
    if policy_input.reversibility is Reversibility.IRREVERSIBLE:
        return PolicyDecision(EffectClass.RED, "IRREVERSIBLE_EFFECT_BOUNDARY", True)
    if effects & RED_EFFECTS or any(r.startswith(RED_RESOURCE_PREFIXES) for r in resources):
        return PolicyDecision(EffectClass.RED, "RED_EFFECT_BOUNDARY", True)
    if effects & YELLOW_EFFECTS or any(r.startswith(YELLOW_RESOURCE_PREFIXES) for r in resources):
        return PolicyDecision(EffectClass.YELLOW, "YELLOW_EFFECT_BOUNDARY", True)
    if op.kind in STATE_BOUND_MUTATION_KINDS and not policy_input.target_state_ref:
        return PolicyDecision(EffectClass.YELLOW, "TARGET_STATE_EVIDENCE_REQUIRED", True)
    return PolicyDecision(EffectClass.GREEN, "GREEN_AUTONOMOUS_TYPED_EFFECT", False)

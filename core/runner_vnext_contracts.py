from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

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

RED_EFFECTS = frozenset({"protected_merge","deploy","runtime_activation","secret_export","secret_rotation","firmware_flash","ota","destructive_storage","destructive_account","financial_commitment","legal_commitment"})
YELLOW_EFFECTS = frozenset({"protected_edit","privileged_maintenance","service_configuration","control_plane_change","external_send"})
ORDINARY_OPERATION_KINDS = frozenset({"read","workspace_write","branch_commit_push","draft_pr_open","test","validate","bounded_service_restart","artifact_transform","private_compute","mailbox_label_sort","workspace_bind"})

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

def classify_effects(effects: Iterable[str], *, reversibility: Reversibility, privacy: PrivacyClass) -> PolicyDecision:
    effect_set = frozenset(effects)
    if effect_set & RED_EFFECTS or reversibility is Reversibility.IRREVERSIBLE:
        return PolicyDecision(EffectClass.RED, "RED_EFFECT_BOUNDARY", True)
    if effect_set & YELLOW_EFFECTS:
        return PolicyDecision(EffectClass.YELLOW, "YELLOW_EFFECT_BOUNDARY", True)
    _ = privacy
    return PolicyDecision(EffectClass.GREEN, "GREEN_AUTONOMOUS_TYPED_EFFECT", False)


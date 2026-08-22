from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping

from core.architecture_invariants import (
    ArchitectureImpact,
    ArchitectureInvariantDecision,
    ArchitectureInvariantEvidence,
    evaluate_architecture_invariants,
)
from core.quality_evidence import (
    DependencyEvidence,
    EvidenceBundle,
    ReviewEvidence,
    RuntimeEvidence,
    TestEvidence,
    evidence_receipt,
    is_full_sha,
    stable_public_hash,
)


TASK_QUALITY_GATE_SCHEMA: Final = "skeleton.task_quality_gate.v1"


class QualityProfile(Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    PROTECTED = "PROTECTED"


class ReadinessState(Enum):
    BLOCKED = "BLOCKED"
    INCOMPLETE = "INCOMPLETE"
    TESTS_GREEN = "TESTS_GREEN"
    ARCHITECTURE_GREEN = "ARCHITECTURE_GREEN"
    PRODUCTION_READY = "PRODUCTION_READY"
    RUNTIME_PROVEN = "RUNTIME_PROVEN"


@dataclass(frozen=True)
class NormalizedTaskContract:
    schema: str
    repo: str
    branch: str
    base_sha: str
    current_head_sha: str
    task_kind: str
    declared_risk: str
    protected_target_declared: bool
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    validation_commands_count: int
    expected_output_count: int
    policy_change: bool = False


@dataclass(frozen=True)
class TaskQualityEvidence:
    tests: TestEvidence | None = None
    dependencies: DependencyEvidence | None = None
    architecture: ArchitectureInvariantEvidence | None = None
    review: ReviewEvidence | None = None
    runtime: RuntimeEvidence | None = None


@dataclass(frozen=True)
class TaskQualityDecision:
    state: ReadinessState
    profile: QualityProfile
    reason_codes: tuple[str, ...]
    architecture: ArchitectureInvariantDecision
    public_receipt: Mapping[str, object]

    @property
    def allowed(self) -> bool:
        return self.state in {
            ReadinessState.PRODUCTION_READY,
            ReadinessState.RUNTIME_PROVEN,
        }


def normalize_task_contract(value: Mapping[str, Any]) -> NormalizedTaskContract:
    if not isinstance(value, Mapping):
        raise ValueError("INVALID_TASK_CONTRACT")
    required = {
        "schema",
        "repo",
        "branch",
        "base_sha",
        "current_head_sha",
        "task_kind",
        "declared_risk",
        "protected_target_declared",
        "requested_capabilities",
        "allowed_files",
        "validation_commands_count",
        "expected_output_count",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError("MISSING_TASK_CONTRACT_FIELD")
    base_sha = _sha(value["base_sha"], "INVALID_BASE_SHA")
    current_head_sha = _sha(value["current_head_sha"], "INVALID_CURRENT_HEAD_SHA")
    return NormalizedTaskContract(
        schema=_nonempty(value["schema"], "INVALID_TASK_CONTRACT"),
        repo=_nonempty(value["repo"], "INVALID_TASK_CONTRACT"),
        branch=_nonempty(value["branch"], "INVALID_TASK_CONTRACT"),
        base_sha=base_sha,
        current_head_sha=current_head_sha,
        task_kind=_nonempty(value["task_kind"], "INVALID_TASK_CONTRACT"),
        declared_risk=_nonempty(value["declared_risk"], "INVALID_TASK_CONTRACT"),
        protected_target_declared=_bool(value["protected_target_declared"], "INVALID_TASK_CONTRACT"),
        requested_capabilities=_string_tuple(value["requested_capabilities"], "INVALID_TASK_CONTRACT"),
        allowed_files=_safe_paths(value["allowed_files"], "INVALID_TASK_CONTRACT"),
        validation_commands_count=_count(value["validation_commands_count"], "INVALID_TASK_CONTRACT"),
        expected_output_count=_count(value["expected_output_count"], "INVALID_TASK_CONTRACT"),
        policy_change=_bool(value.get("policy_change", False), "INVALID_TASK_CONTRACT"),
    )


def evaluate_task_quality(
    *,
    contract: NormalizedTaskContract,
    evidence: TaskQualityEvidence | EvidenceBundle | None,
) -> TaskQualityDecision:
    reasons: list[str] = []
    if not isinstance(contract, NormalizedTaskContract):
        reasons.append("INVALID_TASK_CONTRACT")
        contract = _blocked_contract()
    if evidence is None:
        reasons.append("MISSING_EVIDENCE_BUNDLE")
        evidence = TaskQualityEvidence()
    elif isinstance(evidence, EvidenceBundle):
        evidence = TaskQualityEvidence(
            tests=evidence.tests,
            dependencies=evidence.dependencies,
            review=evidence.review,
            runtime=evidence.runtime,
        )
    elif not isinstance(evidence, TaskQualityEvidence):
        reasons.append("INVALID_EVIDENCE_BUNDLE")
        evidence = TaskQualityEvidence()

    profile = _profile(contract)
    contract_reasons = _contract_reason_codes(contract)
    reasons.extend(contract_reasons)

    dependencies = evidence.dependencies
    if dependencies is None:
        reasons.append("DEPENDENCY_EVIDENCE_REQUIRED")
    elif not dependencies.is_satisfied:
        reasons.append("DECLARED_DEPENDENCY_MISSING")

    tests = evidence.tests
    tests_green = bool(tests and tests.is_green)
    if tests is None:
        reasons.append("TEST_EVIDENCE_REQUIRED")
    elif not tests_green:
        if tests.production_contract_required and tests.strength.value == "MOCK_ONLY":
            reasons.append("MOCK_ONLY_EVIDENCE_INSUFFICIENT")
        else:
            reasons.append("TEST_EVIDENCE_NOT_GREEN")
    _validate_bound_sha(tests.bound_head_sha if tests else None, contract.current_head_sha, "TEST_EVIDENCE_SHA", reasons)

    architecture = evaluate_architecture_invariants(
        touched_files=contract.allowed_files,
        declared_risk=contract.declared_risk,
        requested_capabilities=contract.requested_capabilities,
        evidence=evidence.architecture,
        review=evidence.review,
        current_head_sha=contract.current_head_sha,
    )
    reasons.extend(architecture.reason_codes)

    if profile in {QualityProfile.YELLOW, QualityProfile.PROTECTED} and not architecture.allowed:
        reasons.append("ARCHITECTURE_GREEN_REQUIRED")

    runtime = evidence.runtime
    runtime_proven = bool(runtime and runtime.is_runtime_proven)
    _validate_bound_sha(runtime.bound_head_sha if runtime else None, contract.current_head_sha, "RUNTIME_EVIDENCE_SHA", reasons)

    state = _state(
        reason_codes=tuple(sorted(set(reasons))),
        tests_green=tests_green,
        architecture_green=architecture.allowed,
        runtime_proven=runtime_proven,
    )
    reason_codes = tuple(sorted(set(reasons)))
    receipt = _receipt(
        contract=contract,
        profile=profile,
        state=state,
        reason_codes=reason_codes,
        architecture=architecture,
        evidence=evidence,
    )
    return TaskQualityDecision(
        state=state,
        profile=profile,
        reason_codes=reason_codes,
        architecture=architecture,
        public_receipt=receipt,
    )


def _state(
    *,
    reason_codes: tuple[str, ...],
    tests_green: bool,
    architecture_green: bool,
    runtime_proven: bool,
) -> ReadinessState:
    if _has_hard_block(reason_codes):
        return ReadinessState.BLOCKED
    if runtime_proven:
        return ReadinessState.RUNTIME_PROVEN
    if architecture_green and tests_green:
        return ReadinessState.PRODUCTION_READY
    if architecture_green:
        return ReadinessState.ARCHITECTURE_GREEN
    if tests_green:
        return ReadinessState.TESTS_GREEN
    if reason_codes:
        return ReadinessState.BLOCKED
    return ReadinessState.INCOMPLETE


def _has_hard_block(reason_codes: tuple[str, ...]) -> bool:
    hard_codes = {
        "INVALID_TASK_CONTRACT",
        "MISSING_EVIDENCE_BUNDLE",
        "DEPENDENCY_EVIDENCE_REQUIRED",
        "DECLARED_DEPENDENCY_MISSING",
        "MOCK_ONLY_EVIDENCE_INSUFFICIENT",
        "TEST_EVIDENCE_NOT_GREEN",
        "VALIDATION_COMMAND_REQUIRED",
        "EXPECTED_OUTPUT_REQUIRED",
        "REQUESTED_CAPABILITY_REQUIRED",
        "ALLOWED_FILE_REQUIRED",
        "SELF_MODIFYING_POLICY_INVARIANT_BLOCKED",
        "PROTECTED_REVIEW_REQUIRED",
    }
    return any(
        code in hard_codes
        or code.startswith("INVALID_")
        or code.endswith("_MISMATCH")
        for code in reason_codes
    )


def _contract_reason_codes(contract: NormalizedTaskContract) -> tuple[str, ...]:
    reasons: list[str] = []
    if contract.validation_commands_count < 1:
        reasons.append("VALIDATION_COMMAND_REQUIRED")
    if contract.expected_output_count < 1:
        reasons.append("EXPECTED_OUTPUT_REQUIRED")
    if not contract.requested_capabilities:
        reasons.append("REQUESTED_CAPABILITY_REQUIRED")
    if not contract.allowed_files:
        reasons.append("ALLOWED_FILE_REQUIRED")
    return tuple(reasons)


def _profile(contract: NormalizedTaskContract) -> QualityProfile:
    if contract.protected_target_declared:
        return QualityProfile.PROTECTED
    impact = evaluate_architecture_invariants(
        touched_files=contract.allowed_files or ("placeholder",),
        declared_risk=contract.declared_risk,
        requested_capabilities=contract.requested_capabilities,
    ).impact
    if impact is ArchitectureImpact.PROTECTED:
        return QualityProfile.PROTECTED
    if impact is ArchitectureImpact.YELLOW:
        return QualityProfile.YELLOW
    return QualityProfile.GREEN


def _validate_bound_sha(
    evidence_sha: str | None,
    current_head_sha: str,
    label: str,
    reasons: list[str],
) -> None:
    if evidence_sha is None:
        return
    if not is_full_sha(evidence_sha):
        reasons.append(label + "_INVALID")
    elif evidence_sha.lower() != current_head_sha.lower():
        reasons.append(label + "_MISMATCH")


def _receipt(
    *,
    contract: NormalizedTaskContract,
    profile: QualityProfile,
    state: ReadinessState,
    reason_codes: tuple[str, ...],
    architecture: ArchitectureInvariantDecision,
    evidence: TaskQualityEvidence,
) -> dict[str, object]:
    quality = evidence_receipt(
        reason_codes=reason_codes,
        tests=evidence.tests,
        dependencies=evidence.dependencies,
        review=evidence.review,
        runtime=evidence.runtime,
    )
    return {
        "schema": TASK_QUALITY_GATE_SCHEMA,
        "state": state.value,
        "profile": profile.value,
        "reason_codes": reason_codes,
        "capability_count": len(contract.requested_capabilities),
        "allowed_file_count": len(contract.allowed_files),
        "allowed_file_set_hash": stable_public_hash(contract.allowed_files),
        "head_sha_hash": stable_public_hash((contract.current_head_sha.lower(),)),
        "base_sha_hash": stable_public_hash((contract.base_sha.lower(),)),
        "architecture": architecture.public_receipt(),
        "evidence": quality,
    }


def _blocked_contract() -> NormalizedTaskContract:
    sha = "0" * 40
    return NormalizedTaskContract(
        schema=TASK_QUALITY_GATE_SCHEMA,
        repo="invalid/invalid",
        branch="invalid",
        base_sha=sha,
        current_head_sha=sha,
        task_kind="invalid",
        declared_risk="protected",
        protected_target_declared=True,
        requested_capabilities=(),
        allowed_files=("invalid",),
        validation_commands_count=0,
        expected_output_count=0,
    )


def _sha(value: object, code: str) -> str:
    if not is_full_sha(value):
        raise ValueError(code)
    assert isinstance(value, str)
    return value.lower()


def _nonempty(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(code)
    return value


def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(code)
    return value


def _count(value: object, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(code)
    return value


def _string_tuple(value: object, code: str) -> tuple[str, ...]:
    if isinstance(value, tuple):
        items = value
    elif isinstance(value, list):
        items = tuple(value)
    else:
        raise ValueError(code)
    if any(not isinstance(item, str) or not item for item in items):
        raise ValueError(code)
    return tuple(sorted(set(items)))


def _safe_paths(value: object, code: str) -> tuple[str, ...]:
    items = _string_tuple(value, code)
    for item in items:
        if item.strip() != item or item.startswith("/") or "\\" in item:
            raise ValueError(code)
        if any(part in {"", ".", ".."} for part in item.split("/")):
            raise ValueError(code)
    return items

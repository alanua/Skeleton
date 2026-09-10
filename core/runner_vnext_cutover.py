from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from core.runner_vnext_shadow import DivergenceKind, ShadowParityReceipt


class CutoverReadinessError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CutoverPolicy:
    minimum_parity_observations: int
    required_validation_profiles: tuple[str, ...]
    required_failure_cases: tuple[str, ...]
    allow_incomparable: bool = False


@dataclass(frozen=True)
class ValidationEvidence:
    profile: str
    commit_ref: str
    passed: bool
    test_count: int
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class FailureInjectionEvidence:
    case_id: str
    commit_ref: str
    passed: bool
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class CutoverEvidence:
    stack_head_refs: tuple[str, ...]
    parity_receipts: tuple[ShadowParityReceipt, ...]
    validations: tuple[ValidationEvidence, ...]
    failure_cases: tuple[FailureInjectionEvidence, ...]
    rollback_evidence_ref: str | None
    unresolved_started_count: int
    privacy_boundary_verified: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CutoverReadinessReceipt:
    status: str
    reason_codes: tuple[str, ...]
    policy_hash: str
    evidence_hash: str
    stack_head_hash: str
    stack_head_refs: tuple[str, ...]
    parity_observation_count: int
    parity_match_count: int
    parity_vnext_stricter_count: int
    parity_legacy_stricter_count: int
    parity_incomparable_count: int
    parity_invalid_count: int
    validation_pass_count: int
    failure_case_pass_count: int
    unresolved_started_count: int
    rollback_evidence_present: bool
    privacy_boundary_verified: bool
    operator_approval_required: bool = True
    cutover_authorized: bool = False


class CutoverReadinessEvaluator:
    """Pure readiness gate. It can request an operator decision but can never cut over."""

    def evaluate(self, *, policy: CutoverPolicy, evidence: CutoverEvidence) -> CutoverReadinessReceipt:
        _validate_policy(policy)
        _validate_evidence_shape(evidence)

        blockers: list[str] = []
        missing: list[str] = []
        parity = evidence.parity_receipts
        counts = {kind: sum(1 for receipt in parity if receipt.divergence is kind) for kind in DivergenceKind}

        if evidence.blockers:
            blockers.append("DECLARED_BLOCKER_PRESENT")
        if not evidence.privacy_boundary_verified:
            blockers.append("PRIVACY_BOUNDARY_NOT_VERIFIED")
        if evidence.unresolved_started_count > 0:
            blockers.append("UNRESOLVED_STARTED_OPERATIONS")
        if counts[DivergenceKind.LEGACY_STRICTER] > 0:
            blockers.append("LEGACY_STRICTER_DIVERGENCE")
        if counts[DivergenceKind.INVALID_LEGACY_INPUT] > 0:
            blockers.append("INVALID_LEGACY_INPUT_PRESENT")
        if counts[DivergenceKind.INCOMPARABLE] > 0 and not policy.allow_incomparable:
            missing.append("INCOMPARABLE_PARITY_REQUIRES_EVIDENCE")
        if len(parity) < policy.minimum_parity_observations:
            missing.append("PARITY_OBSERVATION_MINIMUM_NOT_MET")

        validation_by_profile = {item.profile: item for item in evidence.validations}
        for profile in policy.required_validation_profiles:
            item = validation_by_profile.get(profile)
            if item is None:
                missing.append(f"VALIDATION_PROFILE_MISSING_{_reason_token(profile)}")
            elif not item.passed or item.test_count < 1:
                blockers.append(f"VALIDATION_PROFILE_FAILED_{_reason_token(profile)}")

        failure_by_case = {item.case_id: item for item in evidence.failure_cases}
        for case_id in policy.required_failure_cases:
            item = failure_by_case.get(case_id)
            if item is None:
                missing.append(f"FAILURE_CASE_MISSING_{_reason_token(case_id)}")
            elif not item.passed:
                blockers.append(f"FAILURE_CASE_FAILED_{_reason_token(case_id)}")

        if evidence.rollback_evidence_ref is None:
            missing.append("ROLLBACK_EVIDENCE_MISSING")

        if blockers:
            status = "BLOCKED"
            reasons = tuple(sorted(set(blockers + missing)))
        elif missing:
            status = "NEEDS_MORE_EVIDENCE"
            reasons = tuple(sorted(set(missing)))
        else:
            status = "READY_FOR_OPERATOR_DECISION"
            reasons = ("CUTOVER_EVIDENCE_COMPLETE",)

        return CutoverReadinessReceipt(
            status=status, reason_codes=reasons, policy_hash=_policy_hash(policy), evidence_hash=_evidence_hash(evidence),
            stack_head_hash=_refs_hash(evidence.stack_head_refs),
            stack_head_refs=evidence.stack_head_refs, parity_observation_count=len(parity),
            parity_match_count=counts[DivergenceKind.MATCH],
            parity_vnext_stricter_count=counts[DivergenceKind.VNEXT_STRICTER],
            parity_legacy_stricter_count=counts[DivergenceKind.LEGACY_STRICTER],
            parity_incomparable_count=counts[DivergenceKind.INCOMPARABLE],
            parity_invalid_count=counts[DivergenceKind.INVALID_LEGACY_INPUT],
            validation_pass_count=sum(1 for item in evidence.validations if item.passed),
            failure_case_pass_count=sum(1 for item in evidence.failure_cases if item.passed),
            unresolved_started_count=evidence.unresolved_started_count,
            rollback_evidence_present=evidence.rollback_evidence_ref is not None,
            privacy_boundary_verified=evidence.privacy_boundary_verified,
        )

    @staticmethod
    def public_projection(receipt: CutoverReadinessReceipt) -> dict[str, object]:
        return {
            "schema": "skeleton.runner_cutover_readiness_receipt.v1",
            "status": receipt.status, "reason_codes": list(receipt.reason_codes),
            "policy_hash": receipt.policy_hash, "evidence_hash": receipt.evidence_hash,
            "stack_head_hash": receipt.stack_head_hash, "stack_head_refs": list(receipt.stack_head_refs),
            "parity_observation_count": receipt.parity_observation_count,
            "parity_match_count": receipt.parity_match_count,
            "parity_vnext_stricter_count": receipt.parity_vnext_stricter_count,
            "parity_legacy_stricter_count": receipt.parity_legacy_stricter_count,
            "parity_incomparable_count": receipt.parity_incomparable_count,
            "parity_invalid_count": receipt.parity_invalid_count,
            "validation_pass_count": receipt.validation_pass_count,
            "failure_case_pass_count": receipt.failure_case_pass_count,
            "unresolved_started_count": receipt.unresolved_started_count,
            "rollback_evidence_present": receipt.rollback_evidence_present,
            "privacy_boundary_verified": receipt.privacy_boundary_verified,
            "operator_approval_required": receipt.operator_approval_required,
            "cutover_authorized": receipt.cutover_authorized,
        }


def _validate_policy(policy: CutoverPolicy) -> None:
    if policy.minimum_parity_observations < 1:
        raise CutoverReadinessError("CUTOVER_PARITY_MINIMUM_INVALID")
    if not policy.required_validation_profiles or not policy.required_failure_cases:
        raise CutoverReadinessError("CUTOVER_REQUIRED_EVIDENCE_EMPTY")
    if len(set(policy.required_validation_profiles)) != len(policy.required_validation_profiles):
        raise CutoverReadinessError("CUTOVER_VALIDATION_PROFILE_DUPLICATE")
    if len(set(policy.required_failure_cases)) != len(policy.required_failure_cases):
        raise CutoverReadinessError("CUTOVER_FAILURE_CASE_DUPLICATE")


def _validate_evidence_shape(evidence: CutoverEvidence) -> None:
    if not evidence.stack_head_refs or any(not _public_ref(ref) for ref in evidence.stack_head_refs):
        raise CutoverReadinessError("CUTOVER_STACK_HEAD_REF_INVALID")
    if len(set(evidence.stack_head_refs)) != len(evidence.stack_head_refs):
        raise CutoverReadinessError("CUTOVER_STACK_HEAD_REF_DUPLICATE")
    if evidence.unresolved_started_count < 0:
        raise CutoverReadinessError("CUTOVER_UNRESOLVED_COUNT_INVALID")
    if evidence.rollback_evidence_ref is not None and not _public_ref(evidence.rollback_evidence_ref):
        raise CutoverReadinessError("CUTOVER_ROLLBACK_REF_INVALID")
    for receipt in evidence.parity_receipts:
        if receipt.side_effects_executed:
            raise CutoverReadinessError("CUTOVER_SHADOW_SIDE_EFFECT_DETECTED")
        if not _public_ref(receipt.source_task_ref) or not _public_ref(receipt.target_state_ref):
            raise CutoverReadinessError("CUTOVER_PARITY_REF_INVALID")
        if not receipt.evidence_refs or any(not _public_ref(ref) for ref in receipt.evidence_refs):
            raise CutoverReadinessError("CUTOVER_PARITY_EVIDENCE_INVALID")
    profiles = [item.profile for item in evidence.validations]
    cases = [item.case_id for item in evidence.failure_cases]
    if len(set(profiles)) != len(profiles) or len(set(cases)) != len(cases):
        raise CutoverReadinessError("CUTOVER_EVIDENCE_ID_DUPLICATE")
    for item in evidence.validations:
        if not item.profile or not _public_ref(item.commit_ref) or item.test_count < 0 or not item.evidence_refs:
            raise CutoverReadinessError("CUTOVER_VALIDATION_EVIDENCE_INVALID")
        if any(not _public_ref(ref) for ref in item.evidence_refs):
            raise CutoverReadinessError("CUTOVER_VALIDATION_EVIDENCE_INVALID")
    for item in evidence.failure_cases:
        if not item.case_id or not _public_ref(item.commit_ref) or not item.evidence_refs:
            raise CutoverReadinessError("CUTOVER_FAILURE_EVIDENCE_INVALID")
        if any(not _public_ref(ref) for ref in item.evidence_refs):
            raise CutoverReadinessError("CUTOVER_FAILURE_EVIDENCE_INVALID")


def _public_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value and not any(ch.isspace() for ch in value)


def _refs_hash(values: tuple[str, ...]) -> str:
    payload = json.dumps(list(values), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _policy_hash(policy: CutoverPolicy) -> str:
    payload = {
        "minimum_parity_observations": policy.minimum_parity_observations,
        "required_validation_profiles": list(policy.required_validation_profiles),
        "required_failure_cases": list(policy.required_failure_cases),
        "allow_incomparable": policy.allow_incomparable,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _evidence_hash(evidence: CutoverEvidence) -> str:
    payload = {
        "stack_head_refs": list(evidence.stack_head_refs),
        "parity": [
            {
                "source_task_ref": item.source_task_ref,
                "target_state_ref": item.target_state_ref,
                "legacy_effect_class": item.legacy_effect_class.value if item.legacy_effect_class else None,
                "legacy_status": item.legacy_status,
                "vnext_effect_class": item.vnext_effect_class.value if item.vnext_effect_class else None,
                "vnext_reason_code": item.vnext_reason_code,
                "divergence": item.divergence.value,
                "evidence_refs": list(item.evidence_refs),
                "side_effects_executed": item.side_effects_executed,
            } for item in evidence.parity_receipts
        ],
        "validations": [
            {"profile": item.profile, "commit_ref": item.commit_ref, "passed": item.passed,
             "test_count": item.test_count, "evidence_refs": list(item.evidence_refs)}
            for item in evidence.validations
        ],
        "failure_cases": [
            {"case_id": item.case_id, "commit_ref": item.commit_ref, "passed": item.passed,
             "evidence_refs": list(item.evidence_refs)}
            for item in evidence.failure_cases
        ],
        "rollback_evidence_ref": evidence.rollback_evidence_ref,
        "unresolved_started_count": evidence.unresolved_started_count,
        "privacy_boundary_verified": evidence.privacy_boundary_verified,
        "blockers": list(evidence.blockers),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _reason_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in value.upper())
    return token.strip("_") or "UNKNOWN"

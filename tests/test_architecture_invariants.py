from __future__ import annotations

from core.architecture_invariants import (
    ArchitectureEvidence,
    ArchitectureStatus,
    EvidenceBinding,
    EvidenceKind,
    evaluate_architecture_invariants,
    public_receipt,
)


BASE_SHA = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"
HEAD_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
REPO = "alanua/Skeleton"


def binding(head_sha: str = HEAD_SHA) -> EvidenceBinding:
    return EvidenceBinding(repository=REPO, base_sha=BASE_SHA, head_sha=head_sha)


def evidence(
    *,
    kind: EvidenceKind = EvidenceKind.STATIC_REVIEW,
    passed: bool = True,
    head_sha: str = HEAD_SHA,
) -> ArchitectureEvidence:
    return ArchitectureEvidence(
        binding=binding(head_sha),
        kind=kind,
        invariant_ids=("phase1-no-runtime-mutation", "phase1-no-observed-diff"),
        passed=passed,
        reviewer_id="public-review-3156",
    )


def test_architecture_not_required_does_not_block_tests_green_path() -> None:
    result = evaluate_architecture_invariants(
        required=False,
        evidence=None,
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    assert result.status is ArchitectureStatus.NOT_REQUIRED
    assert result.reason_codes == ()


def test_required_architecture_evidence_goes_green_when_bound_and_passing() -> None:
    result = evaluate_architecture_invariants(
        required=True,
        evidence=evidence(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    assert result.status is ArchitectureStatus.GREEN
    assert result.green
    assert result.invariant_count == 2


def test_mock_architecture_evidence_fails_closed() -> None:
    result = evaluate_architecture_invariants(
        required=True,
        evidence=evidence(kind=EvidenceKind.MOCK),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    assert result.status is ArchitectureStatus.BLOCKED
    assert "MOCK_ARCHITECTURE_EVIDENCE" in result.reason_codes


def test_exact_head_movement_invalidates_head_bound_architecture_evidence() -> None:
    result = evaluate_architecture_invariants(
        required=True,
        evidence=evidence(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        current_head_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    assert result.status is ArchitectureStatus.BLOCKED
    assert "HEAD_MOVED" in result.reason_codes


def test_architecture_receipt_is_public_safe_metadata() -> None:
    result = evaluate_architecture_invariants(
        required=True,
        evidence=evidence(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    receipt = public_receipt(result)

    assert receipt["status"] == "ARCHITECTURE_GREEN"
    assert receipt["invariant_count"] == 2
    assert "invariant_ids" not in receipt

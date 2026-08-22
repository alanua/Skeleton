from __future__ import annotations

import pytest

from core.quality_evidence import (
    EvidenceLevel,
    EvidenceValidationError,
    HeadBoundEvidence,
)


BASE = "a" * 40
HEAD = "b" * 40


def test_head_bound_evidence_is_invalidated_by_head_movement() -> None:
    evidence = HeadBoundEvidence.from_mapping(
        {
            "repo": "alanua/Skeleton",
            "base_sha": BASE,
            "head_sha": HEAD,
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "tests_passed": True,
        }
    )

    assert evidence.is_valid_for_head(
        repo="alanua/Skeleton",
        base_sha=BASE,
        head_sha=HEAD,
    )
    assert not evidence.is_valid_for_head(
        repo="alanua/Skeleton",
        base_sha=BASE,
        head_sha="c" * 40,
    )


@pytest.mark.parametrize(
    "level",
    [EvidenceLevel.RUNTIME_PROVEN.value, EvidenceLevel.ARCHITECTURE_GREEN.value],
)
def test_phase1_rejects_unreachable_evidence_levels(level: str) -> None:
    with pytest.raises(EvidenceValidationError) as excinfo:
        HeadBoundEvidence.from_mapping(
            {
                "repo": "alanua/Skeleton",
                "base_sha": BASE,
                "head_sha": HEAD,
                "validation_commands": [["python3", "-m", "pytest", "-q"]],
                "tests_passed": True,
                "evidence_level": level,
            }
        )

    assert excinfo.value.reason_code == "UNREACHABLE_PHASE1_EVIDENCE"

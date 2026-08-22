from __future__ import annotations

import pytest

from core.quality_evidence import EvidenceReceipt, QualityEvidenceError, TaskSpec


def claims(**overrides):
    base = {
        "repo": "alanua/Skeleton",
        "idempotency_key": "validate-pr-branch:alanua/Skeleton:pr-3181:47320dab",
        "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "allowed_files": ["core/task_quality_gate.py"],
        "risk": "low",
    }
    base.update(overrides)
    return base


def test_task_spec_includes_normalized_risk_and_protected_intent_fields() -> None:
    spec = TaskSpec.from_claims(
        claims(risk_level="green", protected_intent="true")
    )

    assert spec.normalized_risk == "LOW"
    assert spec.protected_intent is True
    assert spec.public_mapping()["normalized_risk"] == "LOW"
    assert spec.public_mapping()["protected_intent"] is True


def test_current_runner_private_composite_boundary_normalizes_without_private_leak() -> None:
    spec = TaskSpec.from_claims(
        claims(
            privacy_boundary="PROTECTED_PRIVATE",
            private_claims={"token": "do-not-leak"},
            allowed_files=["tests/**"],
        )
    )

    public = spec.public_mapping()
    assert spec.private_or_composite_boundary is True
    assert public["privacy_boundary"] == "PROTECTED_PRIVATE"
    assert public["raw_private_claim_present"] is True
    assert "do-not-leak" not in repr(public)


def test_current_runner_repository_globs_normalize_as_declared_only() -> None:
    spec = TaskSpec.from_claims(
        claims(allowed_files=["home_edge/generative_visuals/**", "tests/**"])
    )

    assert spec.declared_globs == ("home_edge/generative_visuals/**", "tests/**")
    assert spec.declared_exact_paths == ()


def test_validation_style_idempotency_key_is_accepted_and_bounded() -> None:
    spec = TaskSpec.from_claims(claims())

    assert (
        spec.idempotency_key
        == "validate-pr-branch:alanua/Skeleton:pr-3181:47320dab"
    )


@pytest.mark.parametrize(
    "scope",
    [
        "/core/task_quality_gate.py",
        "../core/task_quality_gate.py",
        "core/../gate_engine.py",
        "*",
        "**",
        "**/*",
        "**/gate_engine.py",
        "core/*/gate.py",
        "tests/\x00bad.py",
    ],
)
def test_absolute_traversal_unbounded_wildcard_and_control_scope_reject(scope) -> None:
    with pytest.raises(QualityEvidenceError):
        TaskSpec.from_claims(claims(allowed_files=[scope]))


def test_no_input_can_produce_runtime_proven_in_phase1() -> None:
    receipt = EvidenceReceipt.from_mapping(
        {"evidence_type": "unit_tests", "state": "RUNTIME_PROVEN"}
    )

    assert receipt.state == "HEAD_BOUND"


def test_head_movement_invalidates_head_bound_evidence() -> None:
    old = "a" * 40
    new = "b" * 40
    spec = TaskSpec.from_claims(
        claims(
            evidence_receipts=[
                {"evidence_type": "unit_tests", "state": "HEAD_BOUND", "head_sha": old}
            ]
        )
    ).bind_evidence_to_head(new)

    assert spec.evidence_receipts[0].state == "INVALIDATED"


def test_public_mapping_excludes_observed_diff_impact_touched_files_and_private_raw_values() -> None:
    spec = TaskSpec.from_claims(
        claims(
            private_values={"secret": "raw-secret"},
            touched_files=["core/gate_engine.py"],
            ObservedDiffImpact={"touched_files": ["core/gate_engine.py"]},
        )
    )

    public = spec.public_mapping()
    assert "touched_files" not in public
    assert "ObservedDiffImpact" not in public
    assert "raw-secret" not in repr(public)

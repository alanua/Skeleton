from __future__ import annotations

from core.quality_evidence import (
    PROTECTED_SURFACE,
    build_quality_evidence,
    caller_proof_rejection_matrix,
    classify_privacy,
    is_protected_surface_path,
    protected_surface_matrix,
)


def claim(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "project": "skeleton",
        "base": "main",
        "base_sha": "53ee95215f903be0684eadee0f70aae3ab43c370",
        "branch": "runner/phase1",
        "task_kind": "code_generation",
        "payload": {"operation": "phase1"},
        "requested_capabilities": ["repository_read", "repository_write", "test_execution"],
        "allowed_files": ["tests/test_quality_evidence.py"],
        "forbidden_actions": ["no merge"],
        "validation": ["python3 -m pytest -q"],
        "required_tests": ["tests/test_quality_evidence.py"],
        "expected_output": ["draft PR"],
        "privacy": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "idempotency": "phase1",
        "risk": "green",
    }
    base.update(overrides)
    return base


def test_private_public_safe_composite_is_protected_and_public_output_omits_raw_private_portion() -> None:
    evidence = build_quality_evidence(
        claim(
            privacy="PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY / PUBLIC_SAFE_HASH_STATUS_ONLY",
            allowed_files=["README.md"],
        )
    )
    assert evidence.privacy_classification == "protected_private_public_safe_composite"
    assert evidence.protected_required is True
    public = evidence.to_public_mapping()
    assert public["privacy_classification"] == "protected_private_public_safe_composite_redacted"
    assert "PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY" not in str(public)


def test_complete_canonical_protected_surface_classifies_protected() -> None:
    matrix = protected_surface_matrix()
    assert {row["surface"] for row in matrix} == set(PROTECTED_SURFACE)
    assert all(row["protected"] is True for row in matrix)
    assert is_protected_surface_path("INVARIANTS.yaml")
    assert is_protected_surface_path("core/architecture_invariants.py")
    assert is_protected_surface_path(".github/workflows/runner.yml")
    assert is_protected_surface_path("deploy/prod.yml")
    assert is_protected_surface_path("server/app.py")
    assert is_protected_surface_path("finance/report.py")
    assert is_protected_surface_path("legal/policy.md")
    assert is_protected_surface_path("governance/rule.yaml")
    assert is_protected_surface_path("secrets/example")
    assert is_protected_surface_path("Runner_core/runner.py")
    assert is_protected_surface_path("adapter_boundaries/contract.md")


def test_live_allowed_files_mark_runner_script_as_protected() -> None:
    evidence = build_quality_evidence(
        claim(
            allowed_files=[
                "scripts/runner_poll_github_tasks.py",
                "tests/test_runner_poll_github_tasks.py",
                "docs/RUNNER_MAINTENANCE_TASKS.md",
                "docs/CONTROL_PLANE_SELF_HEALING.md",
            ],
            risk="yellow",
        )
    )
    assert evidence.protected_files == ("scripts/runner_poll_github_tasks.py",)
    assert evidence.protected_required is True
    assert evidence.risk == "critical"


def test_green_public_safe_ordinary_scope_remains_public_review_allowed() -> None:
    evidence = build_quality_evidence(
        claim(
            allowed_files=["tests/**", "README.md"],
            privacy="PUBLIC_SAFE_POLICY_METADATA_ONLY",
            risk="green",
        )
    )
    assert evidence.protected_files == ()
    assert evidence.privacy_classification == "public_safe"
    assert evidence.risk == "green"
    assert evidence.review_required is False
    assert evidence.protected_required is False


def test_yellow_is_review_relevant_not_automatically_protected() -> None:
    evidence = build_quality_evidence(
        claim(
            allowed_files=["docs/RUNNER_MAINTENANCE_TASKS.md"],
            privacy="PUBLIC_SAFE_POLICY_METADATA_ONLY",
            risk="yellow",
        )
    )
    assert evidence.review_required is True
    assert evidence.protected_required is False
    assert evidence.risk == "yellow"


def test_red_high_and_critical_protected_cannot_be_downgraded() -> None:
    red = build_quality_evidence(claim(risk="high"))
    critical = build_quality_evidence(
        claim(risk="green", allowed_files=["core/action_gate.py"])
    )
    assert red.risk == "red"
    assert red.review_required is True
    assert critical.risk == "critical"
    assert critical.protected_required is True


def test_caller_proof_rejection_matrix_excludes_runtime_and_observed_proof_emission() -> None:
    evidence = build_quality_evidence(claim())
    public = evidence.to_public_mapping()
    assert public["architecture_required"] is False
    assert "ObservedDiffImpact" not in public
    assert "touched_files" not in public
    assert "RUNTIME_PROVEN" not in public
    assert {row["status"] for row in caller_proof_rejection_matrix()} == {"rejected"}


def test_architecture_required_remains_later_phase_not_caller_satisfiable() -> None:
    evidence = build_quality_evidence(claim())
    assert evidence.architecture_required is False
    assert any(
        row["caller_field"] == "ARCHITECTURE_GREEN"
        and "later-phase" in row["reason"]
        for row in caller_proof_rejection_matrix()
    )


def test_privacy_classifier_private_public_composite() -> None:
    assert (
        classify_privacy("PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY / PUBLIC_SAFE_HASH_STATUS_ONLY")
        == "protected_private_public_safe_composite"
    )

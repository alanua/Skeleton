from __future__ import annotations

from copy import deepcopy

import pytest

from core.quality_evidence import (
    ARCHITECTURE_REVIEW_REQUIRED,
    PRODUCTION_CONTRACT_REVIEW_REQUIRED,
    PROTECTED_REVIEW_REQUIRED,
    PUBLIC_REVIEW_ALLOWED,
)
from core.task_quality_gate import (
    PROTECTED_SURFACE,
    TaskQualityGateError,
    is_protected_declared_scope,
    normalize_task_spec,
    normalize_task_spec_public,
    validate_head_bound_metadata,
)


BASE_SHA = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"
HEAD_SHA = "97320dab7740b6c26d006e1b6e3e8d23cd7bcca5"


def _taskspec() -> dict[str, object]:
    return {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "base": "main",
        "base_sha": BASE_SHA,
        "branch": "runner/production-qa-phase1-lossless-no-proof-v1",
        "head_sha": HEAD_SHA,
        "task_kind": "code_generation",
        "payload": {
            "operation": "implement_lossless_phase1_taskspec_normalizer_no_caller_proof",
            "task": "preserve JSON-compatible payload",
            "nested": {"items": [1, True, None, "safe"]},
        },
        "requested_capabilities": [
            "repository_read",
            "repository_write",
            "test_execution",
        ],
        "allowed_files": [
            "core/task_quality_gate.py",
            "tests/test_task_quality_gate.py",
        ],
        "forbidden_actions": [
            "no caller-shaped architecture/production/runtime receipt accepted as proof",
            "no ObservedDiffImpact/touched_files synthesis",
        ],
        "validation": [
            "python3 -m pytest -q",
            "python3 -m py_compile touched Python",
            "git diff --check",
        ],
        "expected_output": [
            "current-Runner lossless normalization matrix",
            "NO MERGE",
        ],
        "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "idempotency_key": "production-qa-phase1-lossless-no-proof-47320dab-20260822-v1",
        "risk": "LOW",
        "protected_intent": False,
        "architecture_required": False,
        "production_contract_required": False,
    }


def test_realistic_current_task_normalizes_without_semantic_loss() -> None:
    task = normalize_task_spec(_taskspec())

    assert task.repo == "alanua/Skeleton"
    assert task.base == "main"
    assert task.base_sha == BASE_SHA
    assert task.branch == "runner/production-qa-phase1-lossless-no-proof-v1"
    assert task.head_sha == HEAD_SHA
    assert task.task_kind == "code_generation"
    assert task.payload["nested"]["items"] == (1, True, None, "safe")
    assert task.requested_capabilities == (
        "repository_read",
        "repository_write",
        "test_execution",
    )
    assert task.allowed_files == (
        "core/task_quality_gate.py",
        "tests/test_task_quality_gate.py",
    )
    assert all(scope.mode == "DECLARED_ONLY" for scope in task.allowed_scopes)
    assert task.validation == (
        "python3 -m pytest -q",
        "python3 -m py_compile touched Python",
        "git diff --check",
    )
    assert task.expected_output == (
        "current-Runner lossless normalization matrix",
        "NO MERGE",
    )
    assert task.privacy_boundary == "PUBLIC_SAFE_POLICY_METADATA_ONLY"
    assert task.idempotency_key.endswith("20260822-v1")
    assert task.risk == "LOW"
    assert task.classification.protected_status == PUBLIC_REVIEW_ALLOWED


def test_aliases_normalize_deterministically() -> None:
    data = _taskspec()
    data["repository"] = data.pop("repo")
    data["base_ref"] = data.pop("base")
    data["head_ref"] = data.pop("branch")
    data["expected_head_sha"] = data.pop("head_sha")
    data["capabilities"] = data.pop("requested_capabilities")
    data["allowed_scopes"] = data.pop("allowed_files")
    data["validation_commands"] = data.pop("validation")
    data["privacy"] = data.pop("privacy_boundary")
    data["risk_level"] = data.pop("risk")
    data["idempotency"] = data.pop("idempotency_key")

    task = normalize_task_spec(data)

    assert task.repo == "alanua/Skeleton"
    assert task.base == "main"
    assert task.branch.startswith("runner/")
    assert task.head_sha == HEAD_SHA
    assert task.requested_capabilities == (
        "repository_read",
        "repository_write",
        "test_execution",
    )


def test_ambiguous_aliases_fail_closed() -> None:
    data = _taskspec()
    data["base_ref"] = "release"

    with pytest.raises(TaskQualityGateError, match="aliases disagree"):
        normalize_task_spec(data)


def test_validation_style_idempotency_key_with_repository_is_accepted() -> None:
    data = _taskspec()
    data["idempotency_key"] = "validate:alanua/Skeleton:47320dab:20260822"

    assert normalize_task_spec(data).idempotency_key == data["idempotency_key"]


def test_composite_private_boundary_is_protected_without_raw_private_public_values() -> None:
    data = _taskspec()
    data["privacy_boundary"] = "PUBLIC_SAFE_POLICY_METADATA_ONLY+PRIVATE_RUNTIME_CONTEXT"
    data["payload"] = {"private_value": "do-not-expose"}

    public = normalize_task_spec_public(data)

    assert public["classification"]["protected_status"] == PROTECTED_REVIEW_REQUIRED
    assert "payload" not in public
    assert "private_value" not in str(public)
    assert "do-not-expose" not in str(public)


def test_bounded_declared_only_globs_are_accepted() -> None:
    data = _taskspec()
    data["allowed_files"] = ["tests/**", "home_edge/generative_visuals/**"]

    task = normalize_task_spec(data)

    assert task.allowed_files == ("home_edge/generative_visuals/**", "tests/**")
    assert all(scope.mode == "DECLARED_ONLY" for scope in task.allowed_scopes)


@pytest.mark.parametrize(
    "path",
    [
        "/absolute.py",
        "../escape.py",
        "core/../escape.py",
        "core/*",
        "**",
        "core/**/deep.py",
        "core/name\n.py",
    ],
)
def test_invalid_paths_reject(path: str) -> None:
    data = _taskspec()
    data["allowed_files"] = [path]

    with pytest.raises(TaskQualityGateError):
        normalize_task_spec(data)


@pytest.mark.parametrize(
    "path",
    [
        "INVARIANTS.yaml",
        "core/architecture_invariants.py",
        ".github/workflows/**",
    ],
)
def test_public_safe_protected_paths_require_review(path: str) -> None:
    data = _taskspec()
    data["allowed_files"] = [path]

    task = normalize_task_spec(data)

    assert task.classification.protected_status == PROTECTED_REVIEW_REQUIRED


@pytest.mark.parametrize("surface", PROTECTED_SURFACE)
def test_every_canonical_protected_surface_item_is_covered(surface: str) -> None:
    if surface.endswith("/**"):
        exact = surface[:-3]
        nested = surface[:-2] + "nested/file.py"
        assert is_protected_declared_scope(exact)
        assert is_protected_declared_scope(nested)
    elif surface.endswith("_**"):
        assert is_protected_declared_scope(surface[:-2] + "example.py")
    else:
        assert is_protected_declared_scope(surface)


def test_protected_intent_cannot_be_downgraded_by_public_safe_low() -> None:
    data = _taskspec()
    data["protected_intent"] = True

    task = normalize_task_spec(data)

    assert task.classification.protected_status == PROTECTED_REVIEW_REQUIRED


def test_private_boundary_cannot_be_downgraded_by_ordinary_scope() -> None:
    data = _taskspec()
    data["privacy_boundary"] = "PRIVATE_LOCAL"

    task = normalize_task_spec(data)

    assert task.classification.protected_status == PROTECTED_REVIEW_REQUIRED


@pytest.mark.parametrize("risk", ["HIGH", "CRITICAL", "PROTECTED"])
def test_protected_risk_cannot_be_downgraded_by_green_claims(risk: str) -> None:
    data = _taskspec()
    data["risk"] = risk
    data["protected_intent"] = False

    task = normalize_task_spec(data)

    assert task.classification.protected_status == PROTECTED_REVIEW_REQUIRED


def test_benign_public_safe_low_risk_ordinary_scope_may_remain_public_allowed() -> None:
    task = normalize_task_spec(_taskspec())

    assert task.classification.protected_status == PUBLIC_REVIEW_ALLOWED
    assert task.classification.review_requirements == ()


def test_architecture_required_ignores_caller_receipt_shaped_mappings() -> None:
    data = _taskspec()
    data["architecture_required"] = True
    data["payload"] = {
        "architecture_review": {
            "green": True,
            "reviewer_id": "reviewer",
            "invariant_ids": ["INV-1"],
            "receipt": {"status": "complete"},
        }
    }

    task = normalize_task_spec(data)

    assert task.classification.architecture_status == ARCHITECTURE_REVIEW_REQUIRED


def test_production_contract_required_ignores_caller_evidence() -> None:
    data = _taskspec()
    data["production_contract_required"] = True
    data["payload"] = {
        "production_contract": {
            "reviewer_id": "reviewer",
            "receipt": {"status": "complete"},
        }
    }

    task = normalize_task_spec(data)

    assert (
        task.classification.production_contract_status
        == PRODUCTION_CONTRACT_REVIEW_REQUIRED
    )


@pytest.mark.parametrize(
    "claim",
    [
        {"payload": {"state": "ARCHITECTURE_GREEN"}},
        {"payload": {"runtime": "RUNTIME_PROVEN"}},
        {"expected_output": ["ARCHITECTURE_GREEN"]},
        {"validation": ["RUNTIME_PROVEN"]},
    ],
)
def test_explicit_green_or_runtime_proven_claims_reject(claim: dict[str, object]) -> None:
    data = _taskspec()
    data.update(claim)

    with pytest.raises(TaskQualityGateError):
        normalize_task_spec(data)


def test_public_mapping_omits_touched_files_observed_diff_impact_and_raw_payload() -> None:
    data = _taskspec()
    data["payload"] = {
        "private": "secret",
        "touched_files": ["core/gate_engine.py"],
        "ObservedDiffImpact": {"risk": "low"},
    }

    public = normalize_task_spec_public(data)

    assert "payload" not in public
    assert "touched_files" not in public
    assert "ObservedDiffImpact" not in public
    assert "secret" not in str(public)


def test_head_movement_invalidates_only_head_bound_metadata() -> None:
    task = normalize_task_spec(_taskspec())

    assert validate_head_bound_metadata(task, current_head_sha=HEAD_SHA)
    assert not validate_head_bound_metadata(
        task,
        current_head_sha="a7320dab7740b6c26d006e1b6e3e8d23cd7bcca5",
    )
    assert task.classification.protected_status == PUBLIC_REVIEW_ALLOWED


def test_no_head_sha_means_no_head_bound_validation_to_invalidate() -> None:
    data = _taskspec()
    data.pop("head_sha")

    task = normalize_task_spec(data)

    assert validate_head_bound_metadata(task, current_head_sha=None)


@pytest.mark.parametrize(
    "mutation",
    [
        {"allowed_files": ["scripts/runner_poll_github_tasks.py"]},
        {"allowed_files": ["core/gate_engine.py"]},
        {"allowed_files": ["core/action_gate.py"]},
        {"allowed_files": [".github/workflows/release.yml"]},
        {"allowed_files": ["CAPABILITY_REGISTRY.yaml"]},
    ],
)
def test_regression_protected_surfaces_remain_rejected_for_public_auto_path(
    mutation: dict[str, object],
) -> None:
    data = deepcopy(_taskspec())
    data.update(mutation)

    assert normalize_task_spec(data).classification.protected_status == (
        PROTECTED_REVIEW_REQUIRED
    )

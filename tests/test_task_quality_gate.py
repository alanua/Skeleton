from __future__ import annotations

import pytest

from core.task_quality_gate import (
    Phase1TaskClaim,
    TaskQualityGateError,
    validate_repository_path,
    validate_task_claim,
)


BASE_SHA = "53ee95215f903be0684eadee0f70aae3ab43c370"


def live_claim() -> dict[str, object]:
    return {
        "project": "skeleton",
        "repo": "alanua/Skeleton",
        "base": "main",
        "base_sha": BASE_SHA,
        "branch": "runner/production-qa-phase1-literal-live-contract-v1",
        "task_kind": "code_generation",
        "payload": {"operation": "finalize_phase1_with_literal_live_tasks"},
        "requested_capabilities": [
            "repository_read",
            "repository_write",
            "test_execution",
        ],
        "allowed_files": [
            "scripts/runner_poll_github_tasks.py",
            "tests/test_runner_poll_github_tasks.py",
            "docs/RUNNER_MAINTENANCE_TASKS.md",
            "docs/CONTROL_PLANE_SELF_HEALING.md",
        ],
        "forbidden_actions": ["no merge #3228; no deploy: scripts/runner_poll_github_tasks.py"],
        "validation": ["python3 -m pytest -q"],
        "required_tests": ["tests/test_runner_poll_github_tasks.py"],
        "expected_output": ["NO MERGE / NO RUNTIME ACTION"],
        "privacy": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "idempotency": "production-qa-phase1-literal-live-contract-v1",
        "risk": "yellow",
    }


def reason(mapping: dict[str, object]) -> str:
    with pytest.raises(TaskQualityGateError) as excinfo:
        validate_task_claim(mapping)
    return excinfo.value.reason_code


def test_literal_live_current_3228_claim_preserves_supported_fields_losslessly() -> None:
    source = live_claim()
    claim = validate_task_claim(source)
    assert isinstance(claim, Phase1TaskClaim)
    assert claim.project == "skeleton"
    assert claim.base == "main"
    assert claim.base_sha == BASE_SHA
    assert claim.branch == "runner/production-qa-phase1-literal-live-contract-v1"
    assert claim.task_kind == "code_generation"
    assert claim.payload["operation"] == "finalize_phase1_with_literal_live_tasks"
    assert claim.requested_capabilities == tuple(source["requested_capabilities"])
    assert claim.allowed_files == tuple(source["allowed_files"])
    assert claim.forbidden_actions == tuple(source["forbidden_actions"])
    assert claim.validation == tuple(source["validation"])
    assert claim.required_tests == tuple(source["required_tests"])
    assert claim.expected_output == tuple(source["expected_output"])
    assert claim.privacy == "PUBLIC_SAFE_POLICY_METADATA_ONLY"
    assert claim.idempotency == "production-qa-phase1-literal-live-contract-v1"
    assert claim.risk == "yellow"


def test_cross_project_directory_scopes_are_preserved_as_scopes() -> None:
    source = live_claim()
    source.update(
        {
            "project": "home_edge",
            "allowed_files": ["home_edge/generative_visuals/**", "tests/**", "README.md"],
        }
    )
    claim = validate_task_claim(source)
    assert claim.allowed_files == (
        "home_edge/generative_visuals/**",
        "tests/**",
        "README.md",
    )


def test_expected_output_and_forbidden_action_text_preserve_safe_punctuation() -> None:
    source = live_claim()
    source["expected_output"] = [
        "next_action=SEMANTIC_EXACT_HEAD_REVIEW_THEN_RELEASE_PHASE1B_3150",
        "safe path docs/RUNNER_MAINTENANCE_TASKS.md #3228: keep punctuation.",
    ]
    source["forbidden_actions"] = [
        "no caller-shaped ObservedDiffImpact/touched_files accepted as proof #3151",
    ]
    claim = validate_task_claim(source)
    assert claim.expected_output == tuple(source["expected_output"])
    assert claim.forbidden_actions == tuple(source["forbidden_actions"])


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("expected_output", ["safe\ninjected: false"], "INVALID_EXPECTED_OUTPUT"),
        ("forbidden_actions", ["safe\runsafe"], "INVALID_FORBIDDEN_ACTIONS"),
        ("validation", ["pytest\x00-q"], "INVALID_VALIDATION"),
        ("payload", {"note": "line\nbreak"}, "INVALID_PAYLOAD.NOTE"),
    ],
)
def test_text_control_and_newline_injection_fails_closed(
    field: str,
    value: object,
    code: str,
) -> None:
    source = live_claim()
    source[field] = value
    assert reason(source) == code


@pytest.mark.parametrize(
    "path",
    [
        "/absolute/path.py",
        "../outside.py",
        "safe/../outside.py",
        "**",
        "docs/*.md",
        "docs/**/nested.py",
        "docs*",
        "docs/***",
        "docs//file.py",
        "docs/",
        "docs/\x00file.py",
    ],
)
def test_repository_path_rejects_absolute_traversal_and_unsafe_wildcards(path: str) -> None:
    with pytest.raises(TaskQualityGateError):
        validate_repository_path(path)


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("README.md", "exact_path"),
        (".github/workflows/**", "directory_scope"),
        ("home_edge/generative_visuals/**", "directory_scope"),
    ],
)
def test_repository_path_accepts_exact_paths_and_directory_scopes(
    path: str,
    kind: str,
) -> None:
    assert validate_repository_path(path).kind == kind


@pytest.mark.parametrize(
    "field",
    [
        "ARCHITECTURE_GREEN",
        "PRODUCTION_CONTRACT_GREEN",
        "ObservedDiffImpact",
        "touched_files",
        "RUNTIME_PROVEN",
        "architecture_required",
    ],
)
def test_caller_shaped_proof_fields_cannot_satisfy_phase1_claims(field: str) -> None:
    source = live_claim()
    source[field] = True
    assert reason(source) == "CALLER_PROOF_REJECTED"


def test_regression_3216_simplified_exact_file_fixture_was_insufficient() -> None:
    simplified = live_claim()
    simplified["allowed_files"] = ["README.md"]
    live = live_claim()
    live["allowed_files"] = ["home_edge/generative_visuals/**", "tests/**", "README.md"]

    simplified_claim = validate_task_claim(simplified)
    live_claim_with_scopes = validate_task_claim(live)

    assert simplified_claim.allowed_files == ("README.md",)
    assert live_claim_with_scopes.allowed_files != simplified_claim.allowed_files
    assert "tests/**" in live_claim_with_scopes.allowed_files

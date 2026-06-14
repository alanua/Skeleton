from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core import merge_policy_checker
from core.merge_policy_checker import (
    AUTO_MERGE_ALLOWED,
    NEVER_AUTO,
    OPERATOR_APPROVAL_REQUIRED,
    REVIEW_REQUIRED,
    DELEGATED_MERGE_VERDICTS,
    DelegatedMergePolicyInput,
    check_delegated_merge_policy,
    evaluate_delegated_merge_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "policies" / "DELEGATED_MERGE_POLICY.yaml"


def candidate_input(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "changed_files": ("docs/example_public_doc.md", "tests/test_example.py"),
        "validation_passed": True,
        "diff_clean": True,
        "secrets_detected": False,
        "approved_scope": True,
        "public_safe": True,
        "file_count_limit": 5,
    }
    values.update(overrides)
    return values


def test_policy_document_defines_review_verdict_not_runtime_action() -> None:
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))

    assert policy["scope"] == "review_verdict_only"
    assert "not a runtime action" in policy["definition"]["delegated_merge"]
    assert "does not enable automatic merging" in policy["definition"]["delegated_merge"]
    assert set(policy["decision_outputs"]) == DELEGATED_MERGE_VERDICTS
    assert policy["auto_merge_candidate_requires"] == [
        "approved_scope",
        "validation_passed",
        "diff_clean",
        "no_secrets",
        "file_count_within_limit",
        "no_protected_files",
        "public_safe_output",
    ]


@pytest.mark.parametrize(
    "example",
    (
        "scripts/runner_poll_github_tasks.py",
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        ".github/workflows/",
        "policies/",
        "deploy",
        "runtime",
        "secrets",
        "server",
        "finance",
        "legal",
        "governance",
        "Hermes install/service",
        "skill approval/promotion",
    ),
)
def test_policy_document_lists_required_hard_stop_examples(example: str) -> None:
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))

    assert example in policy["hard_stop_protected_paths"]


def test_auto_merge_allowed_requires_all_candidate_conditions() -> None:
    result = evaluate_delegated_merge_policy(**candidate_input())

    assert result.verdict == AUTO_MERGE_ALLOWED
    assert result.reasons == ()
    assert result.protected_files == ()


@pytest.mark.parametrize(
    "changed_file",
    (
        "scripts/runner_poll_github_tasks.py",
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        ".github/workflows/delegated-check.yaml",
        "policies/DELEGATED_MERGE_POLICY.yaml",
        "deploy/service.yaml",
        "runtime/worker.py",
        "secrets/example.env",
        "server/app.py",
        "finance/report_schema.py",
        "legal/terms.md",
        "governance/rule.yaml",
        "scripts/skeleton-runner-poll.service",
        "skills/example/approval/rule.yaml",
        "skills/example/promotion/rule.yaml",
    ),
)
def test_hard_stop_protected_paths_never_auto_merge(changed_file: str) -> None:
    result = evaluate_delegated_merge_policy(
        **candidate_input(changed_files=(changed_file,))
    )

    assert result.verdict == OPERATOR_APPROVAL_REQUIRED
    assert result.protected_files == (changed_file,)
    assert result.reasons == (f"protected files changed: {changed_file}",)


def test_secrets_detected_is_never_auto() -> None:
    result = evaluate_delegated_merge_policy(
        **candidate_input(secrets_detected=True)
    )

    assert result.verdict == NEVER_AUTO
    assert result.reasons == ("secrets detected",)


def test_public_unsafe_output_is_never_auto() -> None:
    result = evaluate_delegated_merge_policy(**candidate_input(public_safe=False))

    assert result.verdict == NEVER_AUTO
    assert result.reasons == ("output is not public-safe",)


def test_review_required_collects_candidate_failures() -> None:
    result = evaluate_delegated_merge_policy(
        **candidate_input(
            validation_passed=False,
            diff_clean=False,
            approved_scope=False,
            changed_files=("docs/a.md", "docs/b.md", "docs/c.md"),
            file_count_limit=2,
        )
    )

    assert result.verdict == REVIEW_REQUIRED
    assert result.reasons == (
        "approved scope is required",
        "validation must pass",
        "diff must be clean",
        "file count exceeds limit: 3 > 2",
    )


def test_mapping_wrapper_returns_verdict_and_reasons() -> None:
    result = check_delegated_merge_policy(
        candidate_input(validation_passed=False)
    )

    assert result.verdict == REVIEW_REQUIRED
    assert result.reasons == ("validation must pass",)


def test_dataclass_wrapper_accepts_plain_input_data() -> None:
    request = DelegatedMergePolicyInput(
        changed_files=("docs/example.md",),
        validation_passed=True,
        diff_clean=True,
        secrets_detected=False,
        approved_scope=True,
        public_safe=True,
        file_count_limit=1,
    )

    result = check_delegated_merge_policy(request)

    assert result.verdict == AUTO_MERGE_ALLOWED


def test_checker_module_does_not_import_impure_capabilities() -> None:
    assert not hasattr(merge_policy_checker, "subprocess")
    assert not hasattr(merge_policy_checker, "requests")
    assert not hasattr(merge_policy_checker, "urllib")
    assert not hasattr(merge_policy_checker, "Github")

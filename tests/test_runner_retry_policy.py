from __future__ import annotations

from core.runner_retry_policy import (
    ExpectedOutputValidation,
    expected_output_validation,
    one_time_override_hash,
)


def test_expected_output_validation_rejects_missing_empty_and_placeholder() -> None:
    assert expected_output_validation(None) == ExpectedOutputValidation(
        False, "missing_expected_output"
    )
    assert expected_output_validation(["done", " "]) == ExpectedOutputValidation(
        False, "empty_expected_output"
    )
    assert expected_output_validation("{expected_output}") == ExpectedOutputValidation(
        False, "placeholder_expected_output"
    )


def test_expected_output_validation_accepts_non_placeholder_list() -> None:
    assert expected_output_validation(["update PR only", "report test totals"]) == (
        ExpectedOutputValidation(True)
    )


def test_one_time_override_hash_is_stable_and_scope_sensitive() -> None:
    first = {
        "action": "publish_existing_issue_worktree",
        "allowed_files": ["README.md"],
        "source_issue": 123,
    }
    reordered = {
        "source_issue": 123,
        "allowed_files": ["README.md"],
        "action": "publish_existing_issue_worktree",
    }
    changed = {**first, "allowed_files": ["docs/README.md"]}

    assert one_time_override_hash(first) == one_time_override_hash(reordered)
    assert one_time_override_hash(first) != one_time_override_hash(changed)

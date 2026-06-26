from __future__ import annotations

from pathlib import Path

import pytest

from core.merge_policy_checker import (
    ASK_OPERATOR,
    AUTO_MERGE,
    AUTO_MERGE_ALLOWED,
    BLOCKED,
    DELEGATED_DECISIONS,
    NEVER_AUTO,
    OPERATOR_APPROVAL_REQUIRED,
    REVIEW_REQUIRED,
    DECISIONS,
    DEFAULT_DELEGATED_POLICY_PATH,
    DEFAULT_POLICY_PATH,
    DelegatedMergePolicyChecker,
    DelegatedMergePolicyRequest,
    MERGE_APPROVAL_ACTION,
    OPERATOR_MERGE_APPROVAL_HEAD_MISMATCH,
    OPERATOR_MERGE_APPROVAL_MALFORMED,
    OPERATOR_MERGE_APPROVAL_MISSING,
    OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH,
    OPERATOR_MERGE_APPROVAL_VALID,
    MergePolicyChecker,
    MergePolicyRequest,
    TASK_EXPLICITLY_FORBIDS_MERGE,
    check_delegated_merge_policy,
    check_merge_policy,
    load_delegated_merge_policy,
    load_merge_decision_policy,
    validate_operator_merge_approval,
)


ROOT = Path(__file__).resolve().parents[1]


HARD_STOP_FILES = (
    "scripts/runner_poll_github_tasks.py",
    "PROJECT_TREE.yaml",
    "CAPABILITY_REGISTRY.yaml",
    "core/gate_engine.py",
    "core/action_gate.py",
    "adapters/chatgpt/SYSTEM_PROMPT.md",
)


ASK_OPERATOR_TRIGGERS = (
    "execution_mode_changed",
    "architecture_direction_changed",
    "external_service_added",
    "cost_related_change",
    "cross_repo_execution_enabled",
    "live_runtime_capability_added",
)

REPOSITORY = "alanua/Skeleton"
PR_NUMBER = 1081
HEAD_SHA = "a" * 40
MERGE_METHOD = "squash"


def clean_pr_data(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "changed_files": ("docs/ACTION_GATE.md",),
        "clean_pr": True,
        "evidence": {
            "tests_passed": True,
            "diff_check_passed": True,
            "review_context_present": True,
        },
        "risk_level": "green",
        "triggers": (),
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "expected_head_sha": HEAD_SHA,
        "merge_method": MERGE_METHOD,
    }
    values.update(overrides)
    return values


def clean_request(**overrides: object) -> MergePolicyRequest:
    values = {
        "changed_files": ("docs/ACTION_GATE.md",),
        "clean_pr": True,
        "evidence_present": True,
        "execution_mode_changed": False,
        "risk_level": "green",
        "triggers": (),
    }
    values.update(overrides)
    return MergePolicyRequest(**values)


def clean_delegated_request(**overrides: object) -> DelegatedMergePolicyRequest:
    values = {
        "changed_files": ("docs/ACTION_GATE.md",),
        "clean_pr": True,
        "evidence_present": True,
        "risk_level": "green",
        "triggers": (),
    }
    values.update(overrides)
    return DelegatedMergePolicyRequest(**values)


def valid_merge_approval(**overrides: object) -> dict[str, object]:
    approval: dict[str, object] = {
        "action": MERGE_APPROVAL_ACTION,
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "expected_head_sha": HEAD_SHA,
        "merge_method": MERGE_METHOD,
    }
    approval.update(overrides)
    return approval


def test_clean_routine_pr_without_approval_does_not_get_merge_authority() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(clean_pr_data())

    assert result.decision == ASK_OPERATOR
    assert result.reasons == (OPERATOR_MERGE_APPROVAL_MISSING,)
    assert result.hard_stop_files_found == ()
    assert result.ask_triggers_found == ()


def test_clean_routine_pr_auto_merges_only_with_exact_operator_merge_approval() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(operator_merge_approval=valid_merge_approval())
    )

    assert result.decision == AUTO_MERGE
    assert result.reasons == (OPERATOR_MERGE_APPROVAL_VALID,)
    assert result.public_metadata == {
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "expected_head_sha": HEAD_SHA,
        "merge_method": MERGE_METHOD,
    }


def test_exact_valid_approval_passes_pure_validation_gate() -> None:
    result = validate_operator_merge_approval(
        valid_merge_approval(),
        repository=REPOSITORY,
        pr_number=PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        merge_method=MERGE_METHOD,
    )

    assert result.valid is True
    assert result.reason_token == OPERATOR_MERGE_APPROVAL_VALID


@pytest.mark.parametrize(
    ("override", "reason"),
    (
        ({"repository": "alanua/Other"}, OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH),
        ({"pr_number": 1073}, OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH),
        ({"action": "deploy_runtime"}, OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH),
        ({"merge_method": "merge"}, OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH),
    ),
)
def test_wrong_approval_scope_blocks(
    override: dict[str, object], reason: str
) -> None:
    result = validate_operator_merge_approval(
        valid_merge_approval(**override),
        repository=REPOSITORY,
        pr_number=PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        merge_method=MERGE_METHOD,
    )

    assert result.valid is False
    assert result.reason_token == reason


def test_changed_head_sha_invalidates_prior_approval() -> None:
    result = validate_operator_merge_approval(
        valid_merge_approval(expected_head_sha="b" * 40),
        repository=REPOSITORY,
        pr_number=PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        merge_method=MERGE_METHOD,
    )

    assert result.valid is False
    assert result.reason_token == OPERATOR_MERGE_APPROVAL_HEAD_MISMATCH


@pytest.mark.parametrize(
    "approval",
    (
        None,
        "+",
        "runner:done",
        {"tests_passed": True, "reviewer_approved": True},
        {
            "action": "install_graphify_runtime",
            "repository": REPOSITORY,
            "pr_number": PR_NUMBER,
            "expected_head_sha": HEAD_SHA,
            "merge_method": MERGE_METHOD,
        },
    ),
)
def test_generic_status_review_and_runtime_approval_do_not_count(
    approval: object,
) -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(
            operator_merge_approval=approval,
            evidence={
                "tests_passed": True,
                "diff_check_passed": True,
                "reviewer_approved": True,
                "runner_done": True,
            },
        )
    )

    assert result.decision == ASK_OPERATOR
    assert result.reasons in {
        (OPERATOR_MERGE_APPROVAL_MISSING,),
        (OPERATOR_MERGE_APPROVAL_MALFORMED,),
        (OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH,),
    }


def test_issue_1073_no_merge_shape_blocks_even_with_structured_approval() -> None:
    body = (
        "Incident: issue #1073 explicitly prohibited merge.\n"
        "Expected output: Draft PR only. No merge."
    )
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(
            pr_number=1081,
            task_body=body,
            operator_merge_approval=valid_merge_approval(pr_number=1081),
        )
    )

    assert result.decision == BLOCKED
    assert result.reasons == (TASK_EXPLICITLY_FORBIDS_MERGE,)


def test_no_merge_prohibition_dominates_generic_plus_approval() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(task_body="do not merge", operator_merge_approval="+")
    )

    assert result.decision == BLOCKED
    assert result.reasons == (TASK_EXPLICITLY_FORBIDS_MERGE,)


def test_later_explicit_superseding_merge_approval_can_clear_no_merge() -> None:
    body = "Expected output: draft PR only."
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(
            task_body=body,
            operator_merge_approval=valid_merge_approval(
                supersedes_task_prohibition="draft pr only",
            ),
        )
    )

    assert result.decision == AUTO_MERGE
    assert result.reasons == (OPERATOR_MERGE_APPROVAL_VALID,)


def test_public_report_exposes_no_approval_payload_or_raw_comment_text() -> None:
    raw_comment = "operator said + and pasted private token secret-value"
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(
            task_body=raw_comment,
            operator_merge_approval=valid_merge_approval(
                private_comment=raw_comment,
            ),
        )
    )

    public_text = repr((result.reasons, result.public_metadata))
    assert "secret-value" not in public_text
    assert raw_comment not in public_text


def test_missing_or_malformed_approval_fields_block() -> None:
    result = validate_operator_merge_approval(
        {"action": MERGE_APPROVAL_ACTION, "repository": REPOSITORY},
        repository=REPOSITORY,
        pr_number=PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        merge_method=MERGE_METHOD,
    )

    assert result.valid is False
    assert result.reason_token == OPERATOR_MERGE_APPROVAL_MALFORMED


@pytest.mark.parametrize("changed_file", HARD_STOP_FILES)
def test_hard_stop_file_asks_operator(changed_file: str) -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(changed_files=(changed_file,))
    )

    assert result.decision == ASK_OPERATOR
    assert result.hard_stop_files_found == (changed_file,)
    assert result.ask_triggers_found == ()
    assert result.reasons == (f"hard-stop file changed: {changed_file}",)


def test_level_red_trigger_is_never_auto() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(risk_level="red", triggers=("deploy",))
    )

    assert result.decision == NEVER_AUTO
    assert result.hard_stop_files_found == ()
    assert result.ask_triggers_found == ()
    assert result.reasons == (
        "level_red trigger found: risk_level:red, deploy",
    )


def test_missing_evidence_blocks() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(evidence=None)
    )

    assert result.decision == BLOCKED
    assert result.reasons == ("missing required merge evidence.",)


def test_unverifiable_evidence_blocks() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(evidence={"tests_passed": True, "diff_check_passed": None})
    )

    assert result.decision == BLOCKED
    assert result.reasons == ("unverifiable merge evidence: diff_check_passed",)


@pytest.mark.parametrize("trigger", ASK_OPERATOR_TRIGGERS)
def test_ask_trigger_asks_operator(trigger: str) -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(triggers=(trigger,))
    )

    assert result.decision == ASK_OPERATOR
    assert result.ask_triggers_found == (trigger,)
    assert result.reasons == (f"operator review trigger found: {trigger}",)


def test_dirty_pr_asks_operator() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(clean_pr=False)
    )

    assert result.decision == ASK_OPERATOR
    assert result.reasons == ("clean PR condition is not satisfied.",)


def test_policy_loading() -> None:
    policy = load_merge_decision_policy(DEFAULT_POLICY_PATH)

    assert (ROOT / "policies" / "MERGE_DECISION_POLICY.yaml").is_file()
    assert policy["version"] == "1.0.0"
    assert set(policy["decisions"]) == DECISIONS
    assert policy["auto_merge_requires"] == [
        "clean_pr",
        "evidence_all_true",
        "no_hard_stop_files",
        "no_ask_operator_triggers",
        "no_level_red_triggers",
        "operator_merge_approval_valid",
    ]
    assert policy["operator_merge_approval_requires"] == [
        "action=merge_pull_request",
        "repository_full_name",
        "pull_request_number",
        "expected_head_sha",
        "merge_method",
        "no_task_explicit_merge_prohibition_unless_superseded",
    ]
    assert set(HARD_STOP_FILES[:-1]).issubset(set(policy["hard_stop_file_patterns"]))
    assert "adapters/**" in policy["hard_stop_file_patterns"]
    assert set(ASK_OPERATOR_TRIGGERS).issubset(set(policy["ask_operator_triggers"]))


def test_legacy_function_uses_aligned_result_fields() -> None:
    result = check_merge_policy(clean_request(changed_files=("BOOT_MANIFEST.yaml",)))

    assert result.decision == ASK_OPERATOR
    assert result.hard_stop_files_found == ("BOOT_MANIFEST.yaml",)


def test_legacy_default_policy_path_is_unchanged() -> None:
    assert DEFAULT_POLICY_PATH == ROOT / "policies" / "MERGE_DECISION_POLICY.yaml"


def test_delegated_policy_loading() -> None:
    policy = load_delegated_merge_policy(DEFAULT_DELEGATED_POLICY_PATH)

    assert (ROOT / "policies" / "DELEGATED_MERGE_POLICY.yaml").is_file()
    assert policy["version"] == "1.0.0"
    assert set(policy["verdicts"]) == DELEGATED_DECISIONS
    assert policy["auto_merge_allowed_requires"] == [
        "clean_pr",
        "evidence_all_true",
        "no_operator_approval_files",
        "no_operator_approval_triggers",
        "no_review_required_triggers",
        "no_never_auto_triggers",
    ]
    assert set(HARD_STOP_FILES[:-1]).issubset(
        set(policy["operator_approval_file_patterns"])
    )
    assert "adapters/**" in policy["operator_approval_file_patterns"]


def test_delegated_clean_pr_allows_auto_merge_review_verdict() -> None:
    result = DelegatedMergePolicyChecker(DEFAULT_DELEGATED_POLICY_PATH).check(
        clean_pr_data()
    )

    assert result.verdict == AUTO_MERGE_ALLOWED
    assert result.reasons == ()
    assert result.protected_files_found == ()
    assert result.review_triggers_found == ()


def test_delegated_missing_evidence_requires_review() -> None:
    result = DelegatedMergePolicyChecker(DEFAULT_DELEGATED_POLICY_PATH).check(
        clean_pr_data(evidence=None)
    )

    assert result.verdict == REVIEW_REQUIRED
    assert result.reasons == ("missing required merge evidence.",)


def test_delegated_review_trigger_requires_review() -> None:
    result = DelegatedMergePolicyChecker(DEFAULT_DELEGATED_POLICY_PATH).check(
        clean_pr_data(triggers=("test_gap",))
    )

    assert result.verdict == REVIEW_REQUIRED
    assert result.review_triggers_found == ("test_gap",)
    assert result.reasons == ("review trigger found: test_gap",)


def test_delegated_operator_file_requires_operator_approval() -> None:
    result = DelegatedMergePolicyChecker(DEFAULT_DELEGATED_POLICY_PATH).check(
        clean_pr_data(changed_files=("BOOT_MANIFEST.yaml",))
    )

    assert result.verdict == OPERATOR_APPROVAL_REQUIRED
    assert result.protected_files_found == ("BOOT_MANIFEST.yaml",)
    assert result.reasons == (
        "operator approval file changed: BOOT_MANIFEST.yaml",
    )


def test_delegated_level_red_is_never_auto() -> None:
    result = DelegatedMergePolicyChecker(DEFAULT_DELEGATED_POLICY_PATH).check(
        clean_pr_data(risk_level="red", triggers=("deploy",))
    )

    assert result.verdict == NEVER_AUTO
    assert result.reasons == (
        "never-auto trigger found: risk_level:red, deploy",
    )


def test_delegated_function_uses_aligned_result_fields() -> None:
    result = check_delegated_merge_policy(
        clean_delegated_request(triggers=("needs_human_review",))
    )

    assert result.verdict == REVIEW_REQUIRED
    assert result.review_triggers_found == ("needs_human_review",)

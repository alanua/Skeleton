from __future__ import annotations

from pathlib import Path

from core.merge_policy_checker import (
    ASK_OPERATOR,
    AUTO_MERGE,
    BLOCKED,
    NEVER_AUTO,
    DECISIONS,
    DEFAULT_POLICY_PATH,
    MergePolicyChecker,
    MergePolicyRequest,
    check_merge_policy,
    load_merge_decision_policy,
)


ROOT = Path(__file__).resolve().parents[1]


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


def test_clean_routine_pr_auto_merges_when_all_evidence_true() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(clean_pr_data())

    assert result.decision == AUTO_MERGE
    assert result.reasons == ()
    assert result.hard_stop_files_found == ()
    assert result.ask_triggers_found == ()


def test_hard_stop_file_asks_operator() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(changed_files=("scripts/runner_poll_github_tasks.py",))
    )

    assert result.decision == ASK_OPERATOR
    assert result.hard_stop_files_found == ("scripts/runner_poll_github_tasks.py",)
    assert result.ask_triggers_found == ()
    assert result.reasons == (
        "hard-stop file changed: scripts/runner_poll_github_tasks.py",
    )


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


def test_ask_trigger_asks_operator() -> None:
    result = MergePolicyChecker(DEFAULT_POLICY_PATH).check(
        clean_pr_data(triggers=("execution_mode_changed",))
    )

    assert result.decision == ASK_OPERATOR
    assert result.ask_triggers_found == ("execution_mode_changed",)
    assert result.reasons == (
        "operator review trigger found: execution_mode_changed",
    )


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
    ]


def test_legacy_function_uses_aligned_result_fields() -> None:
    result = check_merge_policy(clean_request(changed_files=("BOOT_MANIFEST.yaml",)))

    assert result.decision == ASK_OPERATOR
    assert result.hard_stop_files_found == ("BOOT_MANIFEST.yaml",)

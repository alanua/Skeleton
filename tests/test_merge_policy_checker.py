from __future__ import annotations

from pathlib import Path

import pytest

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
    ]
    assert set(HARD_STOP_FILES[:-1]).issubset(set(policy["hard_stop_file_patterns"]))
    assert "adapters/**" in policy["hard_stop_file_patterns"]
    assert set(ASK_OPERATOR_TRIGGERS).issubset(set(policy["ask_operator_triggers"]))


def test_legacy_function_uses_aligned_result_fields() -> None:
    result = check_merge_policy(clean_request(changed_files=("BOOT_MANIFEST.yaml",)))

    assert result.decision == ASK_OPERATOR
    assert result.hard_stop_files_found == ("BOOT_MANIFEST.yaml",)

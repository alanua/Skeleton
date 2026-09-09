from __future__ import annotations

from pathlib import Path

from core import runner_self_heal_policy as policy


def decide(**updates: object) -> policy.SelfHealDecision:
    values = {"task_id": "issue-3555"}
    values.update(updates)
    return policy.decide_queue_self_heal_action(
        policy.QueueTaskState(**values)
    )


def test_dirty_disposable_worktree_classifies_cleanup_only() -> None:
    decision = decide(disposable_worktree=True, worktree_dirty=True)

    assert decision.action == "clean-disposable-worktree"
    assert decision.reason == "dirty_disposable_worktree"
    assert decision.blocked_by_gate is False


def test_dirty_non_disposable_worktree_needs_operator() -> None:
    decision = decide(disposable_worktree=False, worktree_dirty=True)

    assert decision.action == "needs-operator"
    assert decision.reason == "dirty_non_disposable_worktree"
    assert decision.blocked_by_gate is True


def test_stale_base_waits_for_refresh_instead_of_retrying() -> None:
    decision = decide(base_stale=True)

    assert decision.action == "wait"
    assert decision.reason == "stale_base_requires_refresh"


def test_temporary_validation_failure_retries_until_bounded() -> None:
    decision = decide(
        validation_state="temporary-failure",
        temporary_validation_failures=1,
        max_temporary_validation_retries=2,
    )

    assert decision.action == "retry"
    assert decision.reason == "temporary_validation_failure"


def test_temporary_validation_failure_exhaustion_needs_operator() -> None:
    decision = decide(
        validation_state="temporary-failure",
        temporary_validation_failures=3,
        max_temporary_validation_retries=2,
    )

    assert decision.action == "needs-operator"
    assert decision.reason == "temporary_validation_retry_exhausted"


def test_repeated_identical_blocker_needs_operator() -> None:
    decision = decide(
        current_blocker_signature="abc123",
        prior_blocker_signatures=("other", "abc123", "abc123"),
    )

    assert decision.action == "needs-operator"
    assert decision.reason == "repeated_identical_blocker"


def test_protected_merge_boundary_needs_operator_before_other_heal() -> None:
    decision = decide(
        protected_merge_boundary=True,
        disposable_worktree=True,
        worktree_dirty=True,
        validation_state="temporary-failure",
    )

    assert decision.action == "needs-operator"
    assert decision.reason == "protected_merge_boundary"
    assert decision.blocked_by_gate is True


def test_validation_not_run_classifies_validate() -> None:
    decision = decide(validation_state="not-run")

    assert decision.action == "validate"
    assert decision.reason == "validation_not_run"


def test_superseded_task_classifies_supersede() -> None:
    decision = decide(superseded_by="issue-3556")

    assert decision.action == "supersede"
    assert decision.reason == "newer_task_supersedes_current"


def test_policy_module_has_no_execution_dependencies() -> None:
    source = Path(policy.__file__).read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "os.system" not in source
    assert "shutil.rmtree" not in source
    assert "git " not in source

#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'REMOTE'
set -euo pipefail

REPO="alanua/Skeleton"
BASE="ef002c22f3fae1d674e054e15b5366d7bc90e492"
EXPECTED_HEAD="4daa01205bff451c71cb6eb18e97490d5c827957"
BRANCH="runner/idle-run-now-selfheal-suppression-v2"
WT="$HOME/agent-dev/worktrees/skeleton/manual-2998-v2"

WD="$(systemctl show skeleton-runner-poll.service -p WorkingDirectory --value 2>/dev/null || true)"
if [ -z "$WD" ] || [ ! -d "$WD/.git" ]; then
  echo "RESULT=BLOCKED_RUNNER_CHECKOUT_NOT_FOUND"
  exit 1
fi

git -C "$WD" fetch --quiet origin main "$BRANCH"
MAIN="$(git -C "$WD" rev-parse origin/main)"
if [ "$MAIN" != "$BASE" ]; then
  echo "RESULT=BLOCKED_BASE_MOVED"
  echo "CURRENT_MAIN=$MAIN"
  exit 2
fi

if ! git -C "$WT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "RESULT=BLOCKED_REPAIR_WORKTREE_NOT_FOUND"
  exit 3
fi
cd "$WT"

CURRENT_HEAD="$(git rev-parse HEAD)"
if [ "$CURRENT_HEAD" != "$EXPECTED_HEAD" ]; then
  echo "RESULT=BLOCKED_PR_HEAD_MOVED"
  echo "CURRENT_HEAD=$CURRENT_HEAD"
  exit 4
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "RESULT=BLOCKED_WORKTREE_DIRTY"
  exit 5
fi

python3 - <<'PY'
from pathlib import Path

path = Path("scripts/runner_poll_github_tasks.py")
source = path.read_text()
start = source.index("def select_run_now_queue_intake_targets(")
end = source.index("\n\ndef _promote_queue_replenisher_issue", start)
replacement = '''def select_run_now_queue_intake_targets(
    ready_issues: list[dict[str, Any]],
    candidate_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for issue in candidate_issues:
        labels = _issue_label_names(issue)
        if LABEL_RUN_NOW not in labels or LABEL_AGENT_TASK not in labels:
            continue
        if labels & TERMINAL_RUNNER_LABELS:
            continue
        if LABEL_READY in labels or LABEL_RUNNING in labels:
            continue
        if LABEL_WAITING_DEPENDENCY in labels:
            continue
        if labels & QUEUE_REPLENISHER_NEEDS_OPERATOR_LABELS:
            continue
        candidates.append(issue)

    maintenance_candidates: list[dict[str, Any]] = []
    generic_candidates: list[dict[str, Any]] = []
    for issue in candidates:
        maintenance_mode, maintenance_task_id = extract_runtime_maintenance_task_id(
            str(issue.get("body") or "")
        )
        if (
            maintenance_mode
            and maintenance_task_id in RUNTIME_MAINTENANCE_TASK_IDS
            and is_open_task_issue(dict(issue))
        ):
            maintenance_candidates.append(issue)
            continue
        generic_candidates.append(issue)

    selection = _runner_queue_replenishment_selection(
        ready_issues,
        generic_candidates,
        target_min_depth=len(ready_issues) + len(generic_candidates),
        target_max_depth=len(ready_issues) + len(generic_candidates),
    )
    generic_selected = list(selection.selected)

    used_intents = {
        intent_key
        for issue in (*ready_issues, *generic_selected)
        if (intent_key := _queue_replenisher_intent_key(issue))
    }
    maintenance_selected: list[dict[str, Any]] = []
    for issue in maintenance_candidates:
        intent_key = _queue_replenisher_intent_key(issue)
        if not intent_key or intent_key in used_intents:
            continue
        used_intents.add(intent_key)
        maintenance_selected.append(issue)

    return [
        issue
        for issue in candidates
        if issue in maintenance_selected or issue in generic_selected
    ]
'''
path.write_text(source[:start] + replacement + source[end:])


test_path = Path("tests/test_runner_poll_github_tasks.py")
tests = test_path.read_text()
marker = "def test_run_now_maintenance_route_preserves_running_needs_operator_and_intent_guards"
if marker not in tests:
    tests += '''\n\n\ndef _run_now_maintenance_issue_for_guard_test(\n    number: int,\n    *,\n    labels: tuple[str, ...] = (),\n    idempotency_key: str = "maintenance-guard-intent",\n) -> dict[str, object]:\n    return {\n        "number": number,\n        "state": "OPEN",\n        "title": "maintenance guard test",\n        "body": "\\n".join(\n            (\n                f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",\n                f"Maintenance Task ID: {runner.CHECK_SKELETON_FRESHNESS}",\n                f"Repository: {runner.REPO}",\n                f"Expected Main SHA: {HEAD_SHA}",\n                "Privacy Boundary: PUBLIC_SAFE_REPOSITORY_ONLY",\n                f"Idempotency Key: {idempotency_key}",\n            )\n        ),\n        "labels": [\n            {"name": runner.LABEL_AGENT_TASK},\n            {"name": runner.LABEL_RUN_NOW},\n            *({"name": label} for label in labels),\n        ],\n    }\n\n\ndef test_run_now_maintenance_route_preserves_running_needs_operator_and_intent_guards() -> None:\n    running = _run_now_maintenance_issue_for_guard_test(4101, labels=(runner.LABEL_RUNNING,))\n    needs_operator = _run_now_maintenance_issue_for_guard_test(\n        4102, labels=("runner:needs-operator",)\n    )\n    selected = runner.select_run_now_queue_intake_targets([], [running, needs_operator])\n    assert selected == []\n\n    ready_duplicate = _run_now_maintenance_issue_for_guard_test(\n        4103,\n        labels=(runner.LABEL_READY,),\n        idempotency_key="same-maintenance-intent",\n    )\n    candidate_duplicate = _run_now_maintenance_issue_for_guard_test(\n        4104,\n        idempotency_key="same-maintenance-intent",\n    )\n    selected = runner.select_run_now_queue_intake_targets(\n        [ready_duplicate], [candidate_duplicate]\n    )\n    assert selected == []\n'''
    test_path.write_text(tests)
PY

python3 -m py_compile scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py -k 'run_now_intake or run_now_maintenance_route'
git diff --check
python3 -m pytest -q

CHANGED="$(git diff --name-only | sort)"
EXPECTED=$'scripts/runner_poll_github_tasks.py\ntests/test_runner_poll_github_tasks.py'
if [ "$CHANGED" != "$EXPECTED" ]; then
  echo "RESULT=BLOCKED_UNEXPECTED_CHANGED_FILES"
  printf '%s\n' "$CHANGED"
  exit 6
fi

git add scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
git commit -q -m "runner: preserve maintenance RUN_NOW safety guards"
HEAD="$(git rev-parse HEAD)"
git push -q origin "$BRANCH"

gh pr comment 3003 --repo "$REPO" --body "$(cat <<EOF
REVIEW_REPAIR_V2_READY
base_sha=$BASE
prior_head_sha=$EXPECTED_HEAD
head_sha=$HEAD
running_guard=passed
needs_operator_guard=passed
intent_dedupe_guard=passed
focused_run_now_tests=passed
full_pytest=passed
py_compile=passed
git_diff_check=passed
changed_files=scripts/runner_poll_github_tasks.py,tests/test_runner_poll_github_tasks.py
provider_calls=0
protected_change=yes
merge_status=NO_MERGE
next_action=EXACT_HEAD_REVIEW
EOF
)" >/dev/null

gh issue comment 2998 --repo "$REPO" --body "REVIEW_REPAIR_V2_READY head_sha=$HEAD pr=https://github.com/alanua/Skeleton/pull/3003 full_pytest=passed merge_status=NO_MERGE next_action=EXACT_HEAD_REVIEW" >/dev/null

echo "RESULT=PR_REPAIR_V2_READY"
echo "PR=https://github.com/alanua/Skeleton/pull/3003"
echo "HEAD=$HEAD"
REMOTE

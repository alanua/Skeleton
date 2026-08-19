#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'REMOTE'
set -euo pipefail

REPO="alanua/Skeleton"
BASE="ef002c22f3fae1d674e054e15b5366d7bc90e492"
BRANCH="runner/idle-run-now-selfheal-suppression-v2"
WT="$HOME/agent-dev/worktrees/skeleton/manual-2998-v2"

WD="$(systemctl show skeleton-runner-poll.service -p WorkingDirectory --value 2>/dev/null || true)"
if [ -z "$WD" ] || [ ! -d "$WD/.git" ]; then
  echo "RESULT=BLOCKED_RUNNER_CHECKOUT_NOT_FOUND"
  exit 1
fi

git -C "$WD" fetch --quiet origin main
MAIN="$(git -C "$WD" rev-parse origin/main)"
if [ "$MAIN" != "$BASE" ]; then
  echo "RESULT=BLOCKED_BASE_MOVED"
  echo "CURRENT_MAIN=$MAIN"
  exit 2
fi

if [ -e "$WT" ]; then
  echo "RESULT=BLOCKED_WORKTREE_EXISTS"
  exit 3
fi
if git -C "$WD" show-ref --verify --quiet "refs/heads/$BRANCH" || git -C "$WD" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  echo "RESULT=BLOCKED_BRANCH_EXISTS"
  exit 4
fi

git -C "$WD" worktree add -q -b "$BRANCH" "$WT" "$BASE"
cd "$WT"

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
    candidates = [
        issue
        for issue in candidate_issues
        if LABEL_RUN_NOW in _issue_label_names(issue)
        and LABEL_AGENT_TASK in _issue_label_names(issue)
        and not (_issue_label_names(issue) & TERMINAL_RUNNER_LABELS)
        and LABEL_READY not in _issue_label_names(issue)
        and LABEL_WAITING_DEPENDENCY not in _issue_label_names(issue)
    ]

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
    return [
        issue
        for issue in candidates
        if issue in maintenance_candidates or issue in generic_selected
    ]
'''
path.write_text(source[:start] + replacement + source[end:])


test_path = Path("tests/test_runner_poll_github_tasks.py")
tests = test_path.read_text()
marker = "def test_run_now_intake_selects_registered_runtime_maintenance_without_codegen_contract"
if marker not in tests:
    tests += '''\n\n\ndef test_run_now_intake_selects_registered_runtime_maintenance_without_codegen_contract() -> None:\n    issue = {\n        "number": 2997,\n        "state": "OPEN",\n        "title": "maintenance run now",\n        "body": "\\n".join(\n            (\n                f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",\n                f"Maintenance Task ID: {runner.CHECK_SKELETON_FRESHNESS}",\n                f"Repository: {runner.REPO}",\n                f"Expected Main SHA: {HEAD_SHA}",\n                "Privacy Boundary: PUBLIC_SAFE_REPOSITORY_ONLY",\n            )\n        ),\n        "labels": [\n            {"name": runner.LABEL_AGENT_TASK},\n            {"name": runner.LABEL_RUN_NOW},\n            {"name": "risk:green"},\n            {"name": runner.LABEL_PRIORITY_1},\n        ],\n    }\n\n    assert runner._queue_replenisher_allowed_files(issue) == frozenset()\n    assert not runner._queue_replenisher_issue_is_discoverable(issue)\n\n    selected = runner.select_run_now_queue_intake_targets([], [issue])\n\n    assert [item["number"] for item in selected] == [2997]\n\n\ndef test_run_now_intake_does_not_bypass_unknown_maintenance_task() -> None:\n    issue = {\n        "number": 3997,\n        "state": "OPEN",\n        "title": "unknown maintenance run now",\n        "body": "\\n".join(\n            (\n                f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",\n                "Maintenance Task ID: unknown_queue_repair_task",\n                f"Repository: {runner.REPO}",\n                "Privacy Boundary: PUBLIC_SAFE_REPOSITORY_ONLY",\n            )\n        ),\n        "labels": [\n            {"name": runner.LABEL_AGENT_TASK},\n            {"name": runner.LABEL_RUN_NOW},\n        ],\n    }\n\n    selected = runner.select_run_now_queue_intake_targets([], [issue])\n\n    assert selected == []\n'''
    test_path.write_text(tests)
PY

python3 -m py_compile scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py -k 'run_now_intake'
git diff --check
python3 -m pytest -q

CHANGED="$(git diff --name-only | sort)"
EXPECTED=$'scripts/runner_poll_github_tasks.py\ntests/test_runner_poll_github_tasks.py'
if [ "$CHANGED" != "$EXPECTED" ]; then
  echo "RESULT=BLOCKED_UNEXPECTED_CHANGED_FILES"
  printf '%s\n' "$CHANGED"
  exit 5
fi

git add scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
git commit -q -m "runner: fix RUN_NOW maintenance self-heal intake"
HEAD="$(git rev-parse HEAD)"
git push -q -u origin "$BRANCH"

PR_URL="$(gh pr list --repo "$REPO" --head "$BRANCH" --state open --json url --jq '.[0].url // empty')"
if [ -z "$PR_URL" ]; then
  PR_URL="$(gh pr create --repo "$REPO" --base main --head "$BRANCH" --draft \
    --title "P0 fix RUN_NOW maintenance self-heal intake" \
    --body $'Parent: #2998 #2997 #2926\n\nRoot cause: autonomous RUN_NOW intake passed registered maintenance issues into the generic backlog/codegen selector, which requires skeleton.runner_task.v1 plus non-empty allowed_files. Registered RUNTIME_MAINTENANCE_TASK issues therefore produced zero selection and QUEUE_RECOVERY_NO_PROGRESS.\n\nFix: registered allowlisted runtime maintenance RUN_NOW tasks use their route-specific intake path; non-maintenance tasks remain under the existing generic selector gates.\n\nValidation: focused RUN_NOW tests, full pytest, py_compile, git diff --check.\n\nProtected Runner poller change. DRAFT / NO MERGE without exact-head operator approval.')"
fi

gh issue comment 2998 --repo "$REPO" --body "$(cat <<EOF
MANUAL_REPAIR_CANDIDATE_READY
root_cause=RUN_NOW_MAINTENANCE_SENT_TO_GENERIC_CODEGEN_SELECTOR
base_sha=$BASE
head_sha=$HEAD
changed_files=scripts/runner_poll_github_tasks.py,tests/test_runner_poll_github_tasks.py
focused_run_now_tests=passed
full_pytest=passed
py_compile=passed
git_diff_check=passed
provider_calls=0
protected_change=yes
merge_status=NO_MERGE
pr=$PR_URL
next_action=EXACT_HEAD_REVIEW
EOF
)" >/dev/null

echo "RESULT=DRAFT_PR_READY"
echo "PR=$PR_URL"
echo "HEAD=$HEAD"
REMOTE

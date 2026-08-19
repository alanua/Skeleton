#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'REMOTE'
set -euo pipefail

REPO="alanua/Skeleton"
BASE="7bc2cd47181f144f24b8adedbbe43323a51f9948"
BRANCH="runner/run-now-action-route-v1"
WT="$HOME/agent-dev/worktrees/skeleton/manual-3010-v1"

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

rm -rf "$WT" 2>/dev/null || true
git -C "$WD" worktree prune >/dev/null 2>&1 || true
if git -C "$WD" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -C "$WD" branch -D "$BRANCH" >/dev/null
fi
if git -C "$WD" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  echo "RESULT=BLOCKED_REMOTE_BRANCH_EXISTS"
  exit 3
fi

git -C "$WD" worktree add -q -b "$BRANCH" "$WT" "$BASE"
cd "$WT"

python3 - <<'PY'
from pathlib import Path

path = Path("scripts/runner_poll_github_tasks.py")
source = path.read_text()
old = '''    token = _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.set(candidate_issues)
    try:
        try:
            report = replenish_runner_queue("")
        except Exception:
            return _autonomous_queue_blocked_report("QUEUE_RECOVERY_EXCEPTION")
    finally:
        _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.reset(token)

    ready_after = get_ready_issues()
'''
new = '''    run_now_selected = bool(eligible) and all(
        LABEL_RUN_NOW in _issue_label_names(issue) for issue in eligible
    )
    if run_now_selected:
        try:
            for issue in eligible:
                _promote_queue_replenisher_issue(issue)
        except Exception:
            return _autonomous_queue_blocked_report("QUEUE_RECOVERY_EXCEPTION")
        selected_numbers = [
            str(number)
            for issue in eligible
            if (number := _queue_replenisher_issue_number(issue)) is not None
        ]
        report = _maintenance_report(
            "DONE",
            REPLENISH_RUNNER_QUEUE,
            [
                f"ready_depth_before={len(ready_before)}",
                f"selected_count={len(selected_numbers)}",
                "selected_issues=" + (",".join(selected_numbers) or "none"),
                "waiting_dependency_count=0",
                "telegram_notifications=0",
            ],
            "met",
        )
    else:
        token = _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.set(candidate_issues)
        try:
            try:
                report = replenish_runner_queue("")
            except Exception:
                return _autonomous_queue_blocked_report("QUEUE_RECOVERY_EXCEPTION")
        finally:
            _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.reset(token)

    ready_after = get_ready_issues()
'''
if old not in source:
    raise SystemExit("PATCH_TARGET_NOT_FOUND")
path.write_text(source.replace(old, new, 1))

test_path = Path("tests/test_runner_poll_github_tasks.py")
tests = test_path.read_text()
marker = "def test_autonomous_queue_replenish_action_promotes_run_now_without_generic_reselection"
if marker not in tests:
    tests += '''\n\n\ndef test_autonomous_queue_replenish_action_promotes_run_now_without_generic_reselection(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    issue = {\n        "number": 3010,\n        "state": "OPEN",\n        "title": "fresh run now maintenance",\n        "body": "\\n".join(\n            (\n                f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",\n                f"Maintenance Task ID: {runner.CHECK_SKELETON_FRESHNESS}",\n                "Idempotency Key: action-route-test",\n            )\n        ),\n        "labels": [\n            {"name": runner.LABEL_AGENT_TASK},\n            {"name": runner.LABEL_RUN_NOW},\n        ],\n    }\n    generation = "initial"\n    expected_key = runner._autonomous_queue_occurrence_key([issue], generation)\n    promoted: list[int] = []\n\n    monkeypatch.setattr(\n        runner,\n        "_autonomous_queue_eligible_snapshot",\n        lambda: ([], [], [issue], [issue]),\n    )\n    monkeypatch.setattr(\n        runner,\n        "_promote_queue_replenisher_issue",\n        lambda item: promoted.append(int(item["number"])),\n    )\n    monkeypatch.setattr(runner, "get_ready_issues", lambda: [issue])\n\n    def forbidden_generic_replenish(_body: str) -> str:\n        raise AssertionError("RUN_NOW eligible set must not be generically reselected")\n\n    monkeypatch.setattr(runner, "replenish_runner_queue", forbidden_generic_replenish)\n\n    report = runner._autonomous_queue_replenish_action(expected_key, generation)\n\n    assert promoted == [3010]\n    assert report.startswith("DONE:")\n    assert "selected_issues=3010" in report\n\n\ndef test_autonomous_queue_replenish_action_keeps_generic_replenisher_path(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    issue = {\n        "number": 5010,\n        "state": "OPEN",\n        "title": "generic backlog task",\n        "body": "schema: skeleton.runner_task.v1\\nIdempotency Key: generic-route-test",\n        "labels": [{"name": runner.LABEL_AGENT_TASK}],\n    }\n    generation = "initial"\n    expected_key = runner._autonomous_queue_occurrence_key([issue], generation)\n    calls: list[str] = []\n\n    monkeypatch.setattr(\n        runner,\n        "_autonomous_queue_eligible_snapshot",\n        lambda: ([], [], [issue], [issue]),\n    )\n    monkeypatch.setattr(runner, "get_ready_issues", lambda: [issue])\n\n    def fake_replenish(body: str) -> str:\n        calls.append(body)\n        return runner._maintenance_report(\n            "DONE",\n            runner.REPLENISH_RUNNER_QUEUE,\n            ["ready_depth_before=0", "selected_count=1", "selected_issues=5010"],\n            "met",\n        )\n\n    monkeypatch.setattr(runner, "replenish_runner_queue", fake_replenish)\n    monkeypatch.setattr(\n        runner,\n        "_promote_queue_replenisher_issue",\n        lambda _issue: (_ for _ in ()).throw(AssertionError("generic path must stay unchanged")),\n    )\n\n    report = runner._autonomous_queue_replenish_action(expected_key, generation)\n\n    assert calls == [""]\n    assert report.startswith("DONE:")\n'''
    test_path.write_text(tests)
PY

python3 -m py_compile scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py -k 'autonomous_queue_replenish_action or run_now_intake'
git diff --check
python3 -m pytest -q

CHANGED="$(git diff --name-only | sort)"
EXPECTED=$'scripts/runner_poll_github_tasks.py\ntests/test_runner_poll_github_tasks.py'
if [ "$CHANGED" != "$EXPECTED" ]; then
  echo "RESULT=BLOCKED_UNEXPECTED_CHANGED_FILES"
  printf '%s\n' "$CHANGED"
  exit 4
fi

git add scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
git commit -q -m "runner: complete RUN_NOW self-heal action route"
HEAD="$(git rev-parse HEAD)"
git push -q -u origin "$BRANCH"

PR_URL="$(gh pr create --repo "$REPO" --base main --head "$BRANCH" --draft \
  --title "P0 complete RUN_NOW self-heal action route" \
  --body $'Parent: #2998 #3010 #3003 #2926\n\nLive canary #3010 proved PR #3003 fixed RUN_NOW eligibility but not the action boundary. `_autonomous_queue_replenish_action()` still re-ran the already-selected RUN_NOW set through generic `replenish_runner_queue()`, which rejects registered maintenance and returns no progress.\n\nFix: when recovery is acting on an already-selected RUN_NOW eligible set, promote exactly that bounded set with the existing label-promotion primitive. Non-RUN_NOW generic backlog recovery remains on the existing replenisher path. Existing terminal/running/waiting/needs-operator/intent guards remain upstream and unchanged.\n\nValidation: focused action + RUN_NOW tests, full pytest, py_compile, git diff --check.\n\nProtected Runner poller change. DRAFT / NO MERGE without exact-head operator approval.')"

gh issue comment 2998 --repo "$REPO" --body "$(cat <<EOF
SUCCESSOR_REPAIR_CANDIDATE_READY
root_cause=RUN_NOW_ELIGIBLE_RESELECTED_BY_GENERIC_ACTION_PATH
base_sha=$BASE
head_sha=$HEAD
changed_files=scripts/runner_poll_github_tasks.py,tests/test_runner_poll_github_tasks.py
focused_tests=passed
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

echo "RESULT=SUCCESSOR_PR_READY"
echo "PR=$PR_URL"
echo "HEAD=$HEAD"
REMOTE

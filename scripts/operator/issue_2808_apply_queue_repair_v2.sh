#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail

ISSUE=2808
REPO=alanua/Skeleton
BASE=9a155435fb608858a60b43b9aba835c96f0d330d
BRANCH=runner/repair-zero-depth-runnow-current-main
CHECKOUT=/home/agent/agent-dev/repos/Skeleton
WT="$(mktemp -d /tmp/skeleton-2808-v2.XXXXXX)"
BODY="$(mktemp)"
FOCUSED="$(mktemp)"
FULL="$(mktemp)"
CREATED_BRANCH=0

cleanup() {
  git -C "$CHECKOUT" worktree remove --force "$WT" >/dev/null 2>&1 || true
  if [ "$CREATED_BRANCH" = 1 ]; then
    if ! git -C "$CHECKOUT" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git -C "$CHECKOUT" branch -D "$BRANCH" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$BODY" "$FOCUSED" "$FULL"
}
trap cleanup EXIT

publish_blocked() {
  local reason="$1"
  local log_file="${2:-}"
  {
    echo '### #2808 deterministic patch v2 receipt'
    echo
    echo '```text'
    echo 'STATUS=BLOCKED'
    echo "REASON=$reason"
    echo "BASE=$BASE"
    if [ -n "$log_file" ] && [ -f "$log_file" ]; then
      echo 'FAILURE_TAIL_BEGIN'
      tail -n 18 "$log_file" | sed -E 's#/home/[^ /]+/[^ ]*#<path>#g' | cut -c1-220
      echo 'FAILURE_TAIL_END'
    fi
    echo '```'
  } >"$BODY"
  gh issue comment "$ISSUE" --repo "$REPO" --body-file "$BODY" >/dev/null 2>&1 || true
  echo "RESULT=BLOCKED:$reason"
  exit 1
}

test "$(hostname)" = "hetzner-agent-runner-1" || publish_blocked wrong_host
test "$(whoami)" = "agent" || publish_blocked wrong_user
test -d "$CHECKOUT/.git" || publish_blocked checkout_missing
origin="$(git -C "$CHECKOUT" remote get-url origin)"
case "$origin" in
  https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git) ;;
  *) publish_blocked wrong_origin ;;
esac

git -C "$CHECKOUT" fetch --quiet origin main
LIVE="$(git -C "$CHECKOUT" rev-parse origin/main)"
test "$LIVE" = "$BASE" || publish_blocked base_moved

if git -C "$CHECKOUT" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  publish_blocked remote_branch_exists
fi

if git -C "$CHECKOUT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  LOCAL_SHA="$(git -C "$CHECKOUT" rev-parse "$BRANCH")"
  test "$LOCAL_SHA" = "$BASE" || publish_blocked stale_local_branch_not_base
  if git -C "$CHECKOUT" worktree list --porcelain | grep -Fq "branch refs/heads/$BRANCH"; then
    publish_blocked stale_local_branch_still_checked_out
  fi
  git -C "$CHECKOUT" branch -D "$BRANCH" >/dev/null
  echo 'STATUS=STALE_LOCAL_BRANCH_REMOVED'
fi

git -C "$CHECKOUT" worktree add -q -b "$BRANCH" "$WT" "$BASE"
CREATED_BRANCH=1

python3 - "$WT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
runner_path = root / "scripts/runner_poll_github_tasks.py"
test_path = root / "tests/test_runner_poll_github_tasks.py"

runner = runner_path.read_text(encoding="utf-8")

old_field = '''    task_fields = _queue_replenisher_task_fields(issue)\n    if typed_key in task_fields:\n        return task_fields[typed_key]\n    metadata = _queue_replenisher_metadata(issue)\n'''
new_field = '''    task_fields = _queue_replenisher_task_fields(issue)\n    if typed_key in task_fields:\n        return task_fields[typed_key]\n    payload = task_fields.get("payload")\n    if isinstance(payload, Mapping) and typed_key in payload:\n        return payload[typed_key]\n    metadata = _queue_replenisher_metadata(issue)\n'''
if runner.count(old_field) != 1:
    raise SystemExit("runner_field_anchor_count_not_one")
runner = runner.replace(old_field, new_field)

old_selection = '''    selection = _runner_queue_replenishment_selection(\n        ready_issues,\n        candidates,\n        target_min_depth=len(ready_issues) + len(candidates),\n        target_max_depth=len(ready_issues) + len(candidates),\n    )\n'''
new_selection = '''    dependency_context = [\n        issue\n        for issue in candidate_issues\n        if LABEL_DONE in _issue_label_names(issue)\n    ]\n    selection = _runner_queue_replenishment_selection(\n        ready_issues,\n        [*candidates, *dependency_context],\n        target_min_depth=len(ready_issues) + len(candidates),\n        target_max_depth=len(ready_issues) + len(candidates),\n    )\n'''
if runner.count(old_selection) != 1:
    raise SystemExit("runner_selection_anchor_count_not_one")
runner_path.write_text(runner.replace(old_selection, new_selection), encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
anchor = '''def test_run_now_intake_selects_valid_missing_ready_agent_task() -> None:\n    issue = _queue_candidate_issue(\n        2502,\n        allowed_files=("scripts/runner_poll_github_tasks.py",),\n        idempotency_key="runner-run-now-auto-intake-2502",\n        labels=(runner.LABEL_AGENT_TASK, runner.LABEL_RUN_NOW),\n    )\n\n    selected = runner.select_run_now_queue_intake_targets([], [issue])\n\n    assert [item["number"] for item in selected] == [2502]\n\n\n'''
addition = '''def test_run_now_intake_reads_nested_payload_and_done_dependency_context() -> None:\n    issue = _queue_candidate_issue(\n        2785,\n        labels=(runner.LABEL_AGENT_TASK, runner.LABEL_RUN_NOW),\n    )\n    issue["body"] = "\\n".join(\n        (\n            "Mode: RUNNER_TASK",\n            "Privacy Boundary: PUBLIC_SAFE_QUEUE_AND_SYNTHETIC_TESTS_ONLY",\n            "```task",\n            "schema: skeleton.runner_task.v1",\n            "repo: alanua/Skeleton",\n            "task_kind: code_generation",\n            "payload:",\n            "  operation: nested_run_now_canary",\n            "  allowed_files:",\n            "    - docs/nested-run-now.md",\n            "  depends_on:",\n            "    - '#2784'",\n            "  idempotency_key: nested-run-now-2785",\n            "```",\n        )\n    )\n    dependency = _queue_candidate_issue(\n        2784,\n        allowed_files=("docs/nested-dependency.md",),\n        idempotency_key="nested-dependency-2784",\n        labels=(runner.LABEL_DONE,),\n    )\n\n    assert runner._queue_replenisher_allowed_files(issue) == frozenset(\n        ("docs/nested-run-now.md",)\n    )\n    assert runner._queue_replenisher_dependencies(issue) == frozenset((2784,))\n\n    selected = runner.select_run_now_queue_intake_targets([], [dependency, issue])\n\n    assert [item["number"] for item in selected] == [2785]\n\n\n'''
if tests.count(anchor) != 1:
    raise SystemExit("test_anchor_count_not_one")
test_path.write_text(tests.replace(anchor, anchor + addition), encoding="utf-8")
PY

mapfile -t CHANGED < <(git -C "$WT" diff --name-only | sort)
EXPECTED=(scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py)
test "${#CHANGED[@]}" -eq 2 || publish_blocked unexpected_changed_file_count
test "${CHANGED[0]}" = "${EXPECTED[0]}" || publish_blocked unexpected_changed_files
test "${CHANGED[1]}" = "${EXPECTED[1]}" || publish_blocked unexpected_changed_files

git -C "$WT" diff --check || publish_blocked diff_check_failed
python3 -m py_compile "$WT/scripts/runner_poll_github_tasks.py" || publish_blocked py_compile_failed

set +e
(
  cd "$WT" && python3 -m pytest -q tests/test_runner_poll_github_tasks.py
) >"$FOCUSED" 2>&1
FOCUSED_RC=$?
set -e
test "$FOCUSED_RC" -eq 0 || publish_blocked focused_tests_failed "$FOCUSED"

set +e
(
  cd "$WT" && python3 -m pytest -q
) >"$FULL" 2>&1
FULL_RC=$?
set -e
test "$FULL_RC" -eq 0 || publish_blocked full_tests_failed "$FULL"

FOCUSED_SUMMARY="$(tail -n 1 "$FOCUSED" | tr -cd '[:alnum:] ._:-' | cut -c1-180)"
FULL_SUMMARY="$(tail -n 1 "$FULL" | tr -cd '[:alnum:] ._:-' | cut -c1-180)"

git -C "$WT" config user.name "Skeleton deterministic patch"
git -C "$WT" config user.email "alanua@users.noreply.github.com"
git -C "$WT" add -- scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
git -C "$WT" commit -q -m "Fix RUN_NOW nested payload and done dependency context"
HEAD="$(git -C "$WT" rev-parse HEAD)"
git -C "$WT" push -q origin "HEAD:refs/heads/$BRANCH"

PR_URL="$(gh pr create --repo "$REPO" --base main --head "$BRANCH" --draft \
  --title "Fix RUN_NOW nested payload and dependency context" \
  --body "Fixes the #2808 zero-depth RUN_NOW root causes on exact base $BASE. The replenisher now reads typed task fields nested under payload and preserves runner:done issues strictly as dependency-resolution context while keeping RUN_NOW eligibility filtering unchanged. Adds a synthetic #2785-shaped regression. Protected file changed; merge requires exact-head operator review.\n\nValidation: focused Runner tests PASS; full pytest PASS; py_compile PASS; git diff --check PASS.\n\nRefs #2808 #2785 #2786")"

{
  echo '### #2808 deterministic patch v2 receipt'
  echo
  echo '```text'
  echo 'STATUS=DRAFT_PR_READY'
  echo "BASE=$BASE"
  echo "HEAD=$HEAD"
  echo 'CHANGED_FILES=scripts/runner_poll_github_tasks.py,tests/test_runner_poll_github_tasks.py'
  echo "FOCUSED_TESTS=$FOCUSED_SUMMARY"
  echo "FULL_TESTS=$FULL_SUMMARY"
  echo 'PY_COMPILE=PASS'
  echo 'DIFF_CHECK=PASS'
  echo 'PROTECTED_CHANGE=YES'
  echo 'NEXT_ACTION=EXACT_HEAD_OPERATOR_REVIEW'
  echo '```'
  echo
  echo "Draft PR: $PR_URL"
} >"$BODY"
URL="$(gh issue comment "$ISSUE" --repo "$REPO" --body-file "$BODY" 2>/dev/null || true)"

echo 'RESULT=DRAFT_PR_READY'
echo "HEAD=$HEAD"
echo "PR=$PR_URL"
[ -n "$URL" ] && echo "RECEIPT_REF=$URL"
REMOTE

rc=$?
echo "RETURNED_TO_TERMUX=1"
echo "REMOTE_RC=$rc"
exit "$rc"

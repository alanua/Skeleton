#!/usr/bin/env bash
set -euo pipefail

REPO='alanua/Skeleton'
BASE='c282c0608acebb994bfd4cffd5a5bea80cb67823'
BRANCH='runner/mail-gmail-canary-dispatch-direct-v1'
WT='/home/agent/agent-dev/worktrees/skeleton/issue-3029'
RECOVERY_ROOT='/home/agent/agent-dev/private-recovery/skeleton/issue-3029'

cd "$WT"
test -e .git || { echo 'BLOCKED=issue_worktree_missing'; exit 1; }

git fetch --prune origin main >/dev/null
MAIN="$(git rev-parse origin/main)"
HEAD="$(git rev-parse HEAD)"
echo "origin_main=$MAIN"
echo "worktree_head=$HEAD"
test "$MAIN" = "$BASE" || { echo 'BLOCKED=main_moved'; exit 2; }
test "$HEAD" = "$BASE" || { echo 'BLOCKED=worktree_head_mismatch'; exit 3; }

ORIGIN_URL="$(git remote get-url origin)"
case "$ORIGIN_URL" in
  *alanua/Skeleton*|*alanua/Skeleton.git*) ;;
  *) echo 'BLOCKED=wrong_origin'; exit 4 ;;
esac

test -z "$(git diff --cached --name-only)" || { echo 'BLOCKED=staged_changes_present'; exit 5; }
test -z "$(git ls-files --others --exclude-standard)" || { echo 'BLOCKED=untracked_files_present'; exit 6; }

mapfile -t changed < <(git diff --name-only | sort -u)
((${#changed[@]} > 0)) || { echo 'BLOCKED=no_dirty_changes'; exit 7; }

allowed_file() {
  case "$1" in
    scripts/runner_poll_github_tasks.py|tests/test_runner_poll_github_tasks.py|docs/MAIL_OPERATIONS.md) return 0 ;;
    *) return 1 ;;
  esac
}
for f in "${changed[@]}"; do
  allowed_file "$f" || { echo "BLOCKED=unexpected_changed_file:$f"; exit 8; }
done
printf '%s\n' "${changed[@]}" | grep -Fx 'scripts/runner_poll_github_tasks.py' >/dev/null
printf '%s\n' "${changed[@]}" | grep -Fx 'tests/test_runner_poll_github_tasks.py' >/dev/null

install -d -m 700 "$RECOVERY_ROOT"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$RECOVERY_ROOT/issue-3029-prepublish-$stamp.patch"
git diff --binary --no-ext-diff >"$backup"
chmod 600 "$backup"
sha256sum "$backup" >"$backup.sha256"
chmod 600 "$backup.sha256"
echo "local_recovery_patch_sha256=$(cut -d' ' -f1 "$backup.sha256")"

runner='scripts/runner_poll_github_tasks.py'
tests='tests/test_runner_poll_github_tasks.py'
grep -F 'MAIL_GMAIL_READONLY_CANARY_TASK_ID' "$runner" >/dev/null || { echo 'BLOCKED=missing_task_id_marker'; exit 9; }
grep -F 'def mail_gmail_readonly_canary_v1' "$runner" >/dev/null || { echo 'BLOCKED=missing_dispatch_helper'; exit 10; }
grep -F 'allowed_gmail_canary_accounts' "$runner" >/dev/null || { echo 'BLOCKED=missing_alias_allowlist'; exit 11; }
grep -F 'run_gmail_readonly_canary' "$runner" >/dev/null || { echo 'BLOCKED=missing_canary_call'; exit 12; }
grep -F 'blocked_gmail_readonly_receipt' "$runner" >/dev/null || { echo 'BLOCKED=missing_blocked_receipt'; exit 13; }
grep -F 'test_gmail_readonly_canary_task_is_registered' "$tests" >/dev/null || { echo 'BLOCKED=missing_registration_test'; exit 14; }

echo '=== VALIDATE DIRTY DELIVERABLE ==='
python3 -m py_compile "$runner" "$tests"
git diff --check
python3 -m pytest -q "$tests" -k 'gmail_readonly_canary'
python3 -m pytest -q

mapfile -t changed_after < <(git diff --name-only | sort -u)
for f in "${changed_after[@]}"; do
  allowed_file "$f" || { echo "BLOCKED=validation_changed_unexpected_file:$f"; exit 15; }
done

git config user.name 'Skeleton Operator Helper'
git config user.email 'operator-helper@skeleton.local'
git add -- "$runner" "$tests"
if printf '%s\n' "${changed_after[@]}" | grep -Fx 'docs/MAIL_OPERATIONS.md' >/dev/null; then
  git add -- docs/MAIL_OPERATIONS.md
fi

git diff --cached --check
git commit -m 'Register Gmail read-only canary dispatch'
PATCH_HEAD="$(git rev-parse HEAD)"
git push -f origin "HEAD:refs/heads/$BRANCH"

echo "PATCH_HEAD=$PATCH_HEAD"
echo "BASE=$BASE"
echo "BRANCH=$BRANCH"
echo 'CHANGED_FILES:'
git diff-tree --no-commit-id --name-only -r "$PATCH_HEAD" | sort
echo 'VALIDATION=PASS'
echo 'NO_MERGE=1'
echo 'NO_RUNTIME_SYNC=1'
echo 'NO_LIVE_GMAIL_CANARY=1'

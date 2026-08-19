#!/usr/bin/env bash
set -uo pipefail

REPO='alanua/Skeleton'
ISSUE='3029'
HELPER_BRANCH='operator/mail-gmail-canary-patch-helper-v1'
HELPER_PATH='scripts/operator_mail_gmail_canary_patch_3029.sh'
WT='/home/agent/agent-dev/worktrees/skeleton/issue-3029'

cd "$WT" || exit 1
git fetch origin "$HELPER_BRANCH" >/dev/null 2>&1 || exit 2

log="$(mktemp /tmp/skeleton-mail-3029.XXXXXX.log)"
report="$(mktemp /tmp/skeleton-mail-3029.XXXXXX.md)"
trap 'rm -f "$log" "$report"' EXIT

set +e
git show "origin/${HELPER_BRANCH}:${HELPER_PATH}" | bash >"$log" 2>&1
rc=$?
set -e

{
  echo "Operator helper report for #3029"
  echo
  echo "exit_code=${rc}"
  echo '```text'
  tail -n 80 "$log" \
    | sed -E 's/((token|secret|password|authorization|cookie|credential)[=:][^[:space:]]+)/[REDACTED]/Ig'
  echo '```'
} >"$report"

if command -v gh >/dev/null 2>&1; then
  gh issue comment "$ISSUE" --repo "$REPO" --body-file "$report" >/dev/null 2>&1 || true
fi

cat "$report"
exit "$rc"

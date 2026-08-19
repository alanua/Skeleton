#!/usr/bin/env bash
set -uo pipefail

REPO='alanua/Skeleton'
ISSUE='3029'
HELPER_BRANCH='operator/mail-gmail-canary-patch-helper-v1'
HELPER_PATH='scripts/operator_mail_gmail_canary_patch_3029.sh'
REPORT_BRANCH='operator/mail-gmail-canary-patch-report-3029'
REPORT_PATH='reports/issue-3029.txt'
WT='/home/agent/agent-dev/worktrees/skeleton/issue-3029'

cd "$WT" || exit 1
git fetch origin "$HELPER_BRANCH" >/dev/null 2>&1 || exit 2

log="$(mktemp /tmp/skeleton-mail-3029.XXXXXX.log)"
report="$(mktemp /tmp/skeleton-mail-3029.XXXXXX.txt)"
index_file="$(mktemp /tmp/skeleton-mail-3029.XXXXXX.index)"
rm -f "$index_file"
trap 'rm -f "$log" "$report" "$index_file"' EXIT

set +e
git show "origin/${HELPER_BRANCH}:${HELPER_PATH}" | bash >"$log" 2>&1
rc=$?
set -e

{
  echo "operator_helper_report_v2"
  echo "issue=${ISSUE}"
  echo "exit_code=${rc}"
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "---"
  tail -n 100 "$log" \
    | sed -E \
      -e 's/((token|secret|password|authorization|cookie|credential)[=:][^[:space:]]+)/[REDACTED]/Ig' \
      -e 's/(Bearer[[:space:]]+)[A-Za-z0-9._~+\/-]+/\1[REDACTED]/Ig'
} >"$report"

# Persist the sanitized report without changing the issue worktree.
base="$(git rev-parse "origin/${HELPER_BRANCH}")"
GIT_INDEX_FILE="$index_file" git read-tree "$base"
blob="$(git hash-object -w "$report")"
GIT_INDEX_FILE="$index_file" git update-index --add --cacheinfo 100644 "$blob" "$REPORT_PATH"
tree="$(GIT_INDEX_FILE="$index_file" git write-tree)"
commit="$(printf '%s\n' "Record sanitized operator helper result for issue ${ISSUE}" | git commit-tree "$tree" -p "$base")"
git push -f origin "$commit:refs/heads/$REPORT_BRANCH" >/dev/null 2>&1 || true

if command -v gh >/dev/null 2>&1; then
  {
    echo "Operator helper report for #${ISSUE}"
    echo
    echo '```text'
    cat "$report"
    echo '```'
  } | gh issue comment "$ISSUE" --repo "$REPO" --body-file - >/dev/null 2>&1 || true
fi

cat "$report"
exit "$rc"

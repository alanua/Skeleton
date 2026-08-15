#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -u
REPO_FULL="alanua/Skeleton"
PR_NUMBER="2815"
BASE_SHA="9a155435fb608858a60b43b9aba835c96f0d330d"
HEAD_SHA="5aeb17907248c73da9cae00c5bf188254287d7c3"
REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
REF="refs/skeleton-operator/pr2815-head"
WT="$(mktemp -d /tmp/skeleton-pr2815.XXXXXX)"
BODY="$(mktemp)"
LOG="$(mktemp)"
cleanup() {
  git -C "$REPO_DIR" worktree remove --force "$WT" >/dev/null 2>&1 || true
  git -C "$REPO_DIR" update-ref -d "$REF" >/dev/null 2>&1 || true
  rm -rf "$WT" "$BODY" "$LOG"
}
trap cleanup EXIT

EXACT_HEAD=NO
FILES_OK=NO
FOCUSED=FAIL
FULL=FAIL
DIFF=FAIL
RESULT=BLOCKED
FAILURES=""

git -C "$REPO_DIR" fetch --quiet origin "pull/${PR_NUMBER}/head:${REF}" >"$LOG" 2>&1 || true
[ "$(git -C "$REPO_DIR" rev-parse "$REF" 2>/dev/null || true)" = "$HEAD_SHA" ] && EXACT_HEAD=YES
if [ "$EXACT_HEAD" = YES ]; then
  git -C "$REPO_DIR" worktree add --detach "$WT" "$HEAD_SHA" >"$LOG" 2>&1 || true
  if [ "$(git -C "$WT" rev-parse HEAD 2>/dev/null || true)" = "$HEAD_SHA" ]; then
    [ "$(git -C "$WT" diff --name-only "$BASE_SHA...$HEAD_SHA")" = "tests/test_home_edge_media_source_snapshot_static_signer.py" ] && FILES_OK=YES
    CLEAN_ENV=(/usr/bin/env -i HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" LANG="C.UTF-8" LC_ALL="C.UTF-8" PYTHONPATH="$WT" PYTHONDONTWRITEBYTECODE=1)
    if (cd "$WT" && "${CLEAN_ENV[@]}" timeout 300 python3 -m pytest -q tests/test_home_edge_media_source_snapshot_static_signer.py) >"$LOG" 2>&1; then
      FOCUSED=PASS
    else
      FAILURES="$(grep '^FAILED ' "$LOG" | head -5 | sed 's/[[:space:]]\+/ /g' | paste -sd ';' -)"
    fi
    if (cd "$WT" && "${CLEAN_ENV[@]}" timeout 1800 python3 -m pytest -q) >"$LOG" 2>&1; then
      FULL=PASS
    else
      FAILURES="$(grep '^FAILED ' "$LOG" | head -10 | sed 's/[[:space:]]\+/ /g' | paste -sd ';' -)"
    fi
    if git -C "$WT" diff --check "$BASE_SHA...$HEAD_SHA" >"$LOG" 2>&1; then
      DIFF=PASS
    fi
  fi
fi
if [ "$EXACT_HEAD" = YES ] && [ "$FILES_OK" = YES ] && [ "$FOCUSED" = PASS ] && [ "$FULL" = PASS ] && [ "$DIFF" = PASS ]; then
  RESULT=VALIDATED
fi
{
  echo "### PR #2815 exact-head validation"
  echo
  echo '```text'
  echo "HEAD_SHA=$HEAD_SHA"
  echo "EXACT_HEAD=$EXACT_HEAD"
  echo "CHANGED_FILES=$FILES_OK"
  echo "FOCUSED_TESTS=$FOCUSED"
  echo "FULL_TESTS=$FULL"
  echo "DIFF_CHECK=$DIFF"
  [ -n "$FAILURES" ] && echo "FAILED_TESTS=$FAILURES"
  echo "RESULT=$RESULT"
  echo '```'
} >"$BODY"
URL="$(gh pr comment "$PR_NUMBER" --repo "$REPO_FULL" --body-file "$BODY" 2>/dev/null || true)"
echo "RESULT=$RESULT"
[ -n "$URL" ] && echo "RECEIPT_REF=$URL" || { echo "RECEIPT_REF=NOT_PUBLISHED"; cat "$BODY"; }
REMOTE
rc=$?
echo "RETURNED_TO_TERMUX=1"
echo "REMOTE_RC=$rc"
exit "$rc"

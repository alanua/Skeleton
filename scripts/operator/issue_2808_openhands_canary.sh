#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
ISSUE="2808"
REPO="alanua/Skeleton"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -uo pipefail

D="$(mktemp -d /tmp/oh-canary.XXXXXX)"
OUT="$(mktemp)"
BODY="$(mktemp)"
cleanup() { rm -rf "$D" "$OUT" "$BODY"; }
trap cleanup EXIT

git -C "$D" init -q
printf 'baseline\n' >"$D/README.md"

set +e
(
  cd "$D" || exit 90
  timeout 60 openhands --headless --json -t \
    'Create exactly one file named CANARY.txt in the current repository. Put exactly the basename of the current working directory in that file followed by a newline. Do not modify any other file.'
) >"$OUT" 2>&1
RC=$?
set -e

BASE="$(basename "$D")"
[ -f "$D/CANARY.txt" ] && FILE=YES || FILE=NO
[ "$(cat "$D/CANARY.txt" 2>/dev/null || true)" = "$BASE" ] && MATCH=YES || MATCH=NO

if [ "$RC" = 124 ]; then
  CLASS=TIMEOUT
elif grep -Eqi 'unauthorized|authentication|api.?key|401' "$OUT"; then
  CLASS=AUTH
elif grep -Eqi 'quota|rate.?limit|429' "$OUT"; then
  CLASS=QUOTA
elif grep -Eqi 'model.*(not found|unsupported)|unsupported.*model' "$OUT"; then
  CLASS=MODEL
elif [ "$RC" = 0 ] && [ "$FILE" = NO ]; then
  CLASS=FALSE_SUCCESS_NO_PROGRESS
elif [ "$RC" = 0 ] && [ "$MATCH" = YES ]; then
  CLASS=WORKSPACE_WRITE_OK
else
  CLASS=OTHER_FAILURE
fi

{
  echo '### OpenHands isolated write canary'
  echo
  echo '```text'
  echo "OPENHANDS_RC=$RC"
  echo "CANARY_FILE=$FILE"
  echo "CONTENT_MATCH=$MATCH"
  echo "CLASS=$CLASS"
  echo "OPENHANDS_VERSION=$(openhands --version 2>/dev/null | head -1 || echo UNKNOWN)"
  echo '```'
} >"$BODY"

URL="$(gh issue comment 2808 --repo alanua/Skeleton --body-file "$BODY" 2>/dev/null || true)"
if [ -n "$URL" ]; then
  echo 'RESULT=PUBLISHED'
  echo "RECEIPT_REF=$URL"
else
  echo 'RESULT=PUBLISH_FAILED'
  cat "$BODY"
fi
REMOTE

rc=$?
echo "RETURNED_TO_TERMUX=1"
echo "REMOTE_RC=$rc"
exit "$rc"

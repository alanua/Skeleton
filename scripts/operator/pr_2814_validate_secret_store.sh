#!/usr/bin/env bash
set -euo pipefail

REPO="alanua/Skeleton"
PR=2814
BASE_SHA="9a155435fb608858a60b43b9aba835c96f0d330d"
HEAD_SHA="914c19b33a77a761d676f64fedb9d9224505766c"
HOST="49.12.76.236"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "agent@${HOST}" 'bash -s' <<'REMOTE'
set -u

REPO_FULL="alanua/Skeleton"
PR_NUMBER="2814"
BASE_SHA="9a155435fb608858a60b43b9aba835c96f0d330d"
HEAD_SHA="914c19b33a77a761d676f64fedb9d9224505766c"
REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
REF="refs/skeleton-operator/pr2814-head"
WT="$(mktemp -d /tmp/skeleton-pr2814.XXXXXX)"
BODY="$(mktemp)"
LOG="$(mktemp)"
cleanup() {
  git -C "$REPO_DIR" worktree remove --force "$WT" >/dev/null 2>&1 || true
  git -C "$REPO_DIR" update-ref -d "$REF" >/dev/null 2>&1 || true
  rm -rf "$WT" "$BODY" "$LOG"
}
trap cleanup EXIT

HOST_OK=NO
USER_OK=NO
REPO_OK=NO
HEAD_OK=NO
FILES_OK=NO
PY_COMPILE=FAIL
FOCUSED_TESTS=FAIL
FULL_TESTS=FAIL
DIFF_CHECK=FAIL
BWS_CLI=NO
TOKEN_CREDENTIAL_DECLARED=NO
REF_CREDENTIAL_DECLARED=NO
RESULT=BLOCKED

[ "$(hostname)" = "hetzner-agent-runner-1" ] && HOST_OK=YES
[ "$(id -un)" = "agent" ] && USER_OK=YES
if [ "$HOST_OK" != YES ] || [ "$USER_OK" != YES ]; then
  RESULT=IDENTITY_MISMATCH
else
  ORIGIN="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
  case "$ORIGIN" in
    https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git) REPO_OK=YES ;;
  esac
fi

if [ "$REPO_OK" = YES ]; then
  git -C "$REPO_DIR" fetch --quiet origin "pull/${PR_NUMBER}/head:${REF}" >"$LOG" 2>&1 || true
  FETCHED="$(git -C "$REPO_DIR" rev-parse "$REF" 2>/dev/null || true)"
  [ "$FETCHED" = "$HEAD_SHA" ] && HEAD_OK=YES
fi

if [ "$HEAD_OK" = YES ]; then
  git -C "$REPO_DIR" worktree add --detach "$WT" "$HEAD_SHA" >"$LOG" 2>&1 || true
  if [ "$(git -C "$WT" rev-parse HEAD 2>/dev/null || true)" = "$HEAD_SHA" ]; then
    EXPECTED="$(printf '%s\n' \
      core/runner_child_environment.py \
      core/secret_store.py \
      integrations/bitwarden_secret_store.py \
      schemas/secret_reference.schema.json \
      tests/test_bitwarden_secret_store.py \
      tests/test_runner_child_environment_openrouter.py \
      tests/test_secret_store.py | sort)"
    ACTUAL="$(git -C "$WT" diff --name-only "$BASE_SHA...$HEAD_SHA" 2>/dev/null | sort)"
    [ "$ACTUAL" = "$EXPECTED" ] && FILES_OK=YES

    if (
      cd "$WT" &&
      env -u BWS_ACCESS_TOKEN -u OPENROUTER_API_KEY -u CREDENTIALS_DIRECTORY \
          -u LLM_API_KEY -u LLM_MODEL -u LLM_BASE_URL \
        python3 -m py_compile \
          core/secret_store.py \
          integrations/bitwarden_secret_store.py \
          core/runner_child_environment.py \
          tests/test_secret_store.py \
          tests/test_bitwarden_secret_store.py \
          tests/test_runner_child_environment_openrouter.py
    ) >"$LOG" 2>&1; then
      PY_COMPILE=PASS
    fi

    if (
      cd "$WT" &&
      env -u BWS_ACCESS_TOKEN -u OPENROUTER_API_KEY -u CREDENTIALS_DIRECTORY \
          -u LLM_API_KEY -u LLM_MODEL -u LLM_BASE_URL \
        timeout 600 python3 -m pytest -q \
          tests/test_secret_store.py \
          tests/test_bitwarden_secret_store.py \
          tests/test_runner_child_environment.py \
          tests/test_runner_child_environment_openrouter.py
    ) >"$LOG" 2>&1; then
      FOCUSED_TESTS=PASS
    fi

    if (
      cd "$WT" &&
      env -u BWS_ACCESS_TOKEN -u OPENROUTER_API_KEY -u CREDENTIALS_DIRECTORY \
          -u LLM_API_KEY -u LLM_MODEL -u LLM_BASE_URL \
        timeout 1800 python3 -m pytest -q
    ) >"$LOG" 2>&1; then
      FULL_TESTS=PASS
    fi

    if git -C "$WT" diff --check "$BASE_SHA...$HEAD_SHA" >"$LOG" 2>&1; then
      DIFF_CHECK=PASS
    fi
  fi
fi

command -v bws >/dev/null 2>&1 && BWS_CLI=YES
CRED_DECL="$(systemctl show skeleton-runner-poll.service -p LoadCredential -p LoadCredentialEncrypted --value 2>/dev/null || true)"
printf '%s' "$CRED_DECL" | grep -q 'bitwarden-access-token' && TOKEN_CREDENTIAL_DECLARED=YES
printf '%s' "$CRED_DECL" | grep -q 'openrouter-secret-ref' && REF_CREDENTIAL_DECLARED=YES

if [ "$HOST_OK" = YES ] && [ "$USER_OK" = YES ] && [ "$REPO_OK" = YES ] && \
   [ "$HEAD_OK" = YES ] && [ "$FILES_OK" = YES ] && \
   [ "$PY_COMPILE" = PASS ] && [ "$FOCUSED_TESTS" = PASS ] && \
   [ "$FULL_TESTS" = PASS ] && [ "$DIFF_CHECK" = PASS ]; then
  RESULT=VALIDATED
fi

{
  echo "### PR #2814 exact-head secret-store validation"
  echo
  echo '```text'
  echo "BASE_SHA=$BASE_SHA"
  echo "HEAD_SHA=$HEAD_SHA"
  echo "HOST_IDENTITY=$HOST_OK"
  echo "RUNNER_USER=$USER_OK"
  echo "REPOSITORY=$REPO_OK"
  echo "EXACT_HEAD=$HEAD_OK"
  echo "CHANGED_FILES=$FILES_OK"
  echo "PY_COMPILE=$PY_COMPILE"
  echo "FOCUSED_TESTS=$FOCUSED_TESTS"
  echo "FULL_TESTS=$FULL_TESTS"
  echo "DIFF_CHECK=$DIFF_CHECK"
  echo "BWS_CLI=$BWS_CLI"
  echo "BITWARDEN_TOKEN_CREDENTIAL=$TOKEN_CREDENTIAL_DECLARED"
  echo "OPENROUTER_REF_CREDENTIAL=$REF_CREDENTIAL_DECLARED"
  echo "RESULT=$RESULT"
  echo '```'
} >"$BODY"

URL="$(gh pr comment "$PR_NUMBER" --repo "$REPO_FULL" --body-file "$BODY" 2>/dev/null || true)"
echo "RESULT=$RESULT"
if [ -n "$URL" ]; then
  echo "RECEIPT_REF=$URL"
else
  echo "RECEIPT_REF=NOT_PUBLISHED"
  cat "$BODY"
fi
REMOTE

rc=$?
echo "RETURNED_TO_TERMUX=1"
echo "REMOTE_RC=$rc"

#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -u

REPO_FULL="alanua/Skeleton"
PR_NUMBER="2814"
BASE_SHA="9bb2b313bd3a0dc89107a5b2b7df12f847a2ace6"
HEAD_SHA="93375c97b0f1cfe95d6ab65526752e14af39a7c5"
REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
REF="refs/skeleton-operator/pr2814-v3"
WT="$(mktemp -d /tmp/skeleton-pr2814-v3.XXXXXX)"
TMP="$(mktemp -d /tmp/bws-v3.XXXXXX)"
BODY="$(mktemp)"
LOG="$(mktemp)"
cleanup() {
  git -C "$REPO_DIR" worktree remove --force "$WT" >/dev/null 2>&1 || true
  git -C "$REPO_DIR" update-ref -d "$REF" >/dev/null 2>&1 || true
  rm -rf "$WT" "$TMP" "$BODY" "$LOG"
}
trap cleanup EXIT

HOST_OK=NO
USER_OK=NO
REPO_OK=NO
MAIN_OK=NO
HEAD_OK=NO
FILES_OK=NO
PY_COMPILE=FAIL
FOCUSED_TESTS=FAIL
FULL_TESTS=FAIL
DIFF_CHECK=FAIL
BWS_BEFORE=NO
BWS_INSTALL=NOT_ATTEMPTED
BWS_AFTER=NO
BWS_VERSION=UNKNOWN
TOKEN_CREDENTIAL_DECLARED=NO
REF_CREDENTIAL_DECLARED=NO
RESULT=BLOCKED
FAILURES=""

[ "$(hostname)" = "hetzner-agent-runner-1" ] && HOST_OK=YES
[ "$(id -un)" = "agent" ] && USER_OK=YES
ORIGIN="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
case "$ORIGIN" in
  https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git) REPO_OK=YES ;;
esac

if [ "$HOST_OK" = YES ] && [ "$USER_OK" = YES ] && [ "$REPO_OK" = YES ]; then
  git -C "$REPO_DIR" fetch --quiet origin main "pull/${PR_NUMBER}/head:${REF}" >"$LOG" 2>&1 || true
  [ "$(git -C "$REPO_DIR" rev-parse origin/main 2>/dev/null || true)" = "$BASE_SHA" ] && MAIN_OK=YES
  [ "$(git -C "$REPO_DIR" rev-parse "$REF" 2>/dev/null || true)" = "$HEAD_SHA" ] && HEAD_OK=YES
fi

if command -v bws >/dev/null 2>&1; then
  BWS_BEFORE=YES
fi

if [ "$MAIN_OK" = YES ] && [ "$HEAD_OK" = YES ]; then
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

    CLEAN_ENV=(/usr/bin/env -i HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" LANG="C.UTF-8" LC_ALL="C.UTF-8" PYTHONPATH="$WT" PYTHONDONTWRITEBYTECODE=1)

    if (cd "$WT" && "${CLEAN_ENV[@]}" python3 -m py_compile \
        core/secret_store.py integrations/bitwarden_secret_store.py core/runner_child_environment.py \
        tests/test_secret_store.py tests/test_bitwarden_secret_store.py tests/test_runner_child_environment_openrouter.py) >"$LOG" 2>&1; then
      PY_COMPILE=PASS
    fi

    if (cd "$WT" && "${CLEAN_ENV[@]}" timeout 600 python3 -m pytest -q \
        tests/test_secret_store.py tests/test_bitwarden_secret_store.py \
        tests/test_runner_child_environment.py tests/test_runner_child_environment_openrouter.py \
        tests/test_home_edge_media_source_snapshot_static_signer.py) >"$LOG" 2>&1; then
      FOCUSED_TESTS=PASS
    else
      FAILURES="$(grep '^FAILED ' "$LOG" | head -10 | sed 's/[[:space:]]\+/ /g' | paste -sd ';' -)"
    fi

    if (cd "$WT" && "${CLEAN_ENV[@]}" timeout 1800 python3 -m pytest -q) >"$LOG" 2>&1; then
      FULL_TESTS=PASS
    else
      FAILURES="$(grep '^FAILED ' "$LOG" | head -10 | sed 's/[[:space:]]\+/ /g' | paste -sd ';' -)"
    fi

    if git -C "$WT" diff --check "$BASE_SHA...$HEAD_SHA" >"$LOG" 2>&1; then
      DIFF_CHECK=PASS
    fi
  fi
fi

CODE_OK=NO
if [ "$HOST_OK" = YES ] && [ "$USER_OK" = YES ] && [ "$REPO_OK" = YES ] && \
   [ "$MAIN_OK" = YES ] && [ "$HEAD_OK" = YES ] && [ "$FILES_OK" = YES ] && \
   [ "$PY_COMPILE" = PASS ] && [ "$FOCUSED_TESTS" = PASS ] && [ "$FULL_TESTS" = PASS ] && \
   [ "$DIFF_CHECK" = PASS ]; then
  CODE_OK=YES
fi

if [ "$CODE_OK" = YES ] && [ "$BWS_BEFORE" = NO ]; then
  if [ "$(uname -m)" = "x86_64" ] && command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    VERSION="2.1.0"
    ZIP="bws-x86_64-unknown-linux-gnu-${VERSION}.zip"
    URL="https://github.com/bitwarden/sdk-sm/releases/download/bws-v${VERSION}/${ZIP}"
    EXPECTED_SHA256="ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860"
    if curl -fsSL "$URL" -o "$TMP/$ZIP" >"$LOG" 2>&1 && \
       [ "$(sha256sum "$TMP/$ZIP" | awk '{print $1}')" = "$EXPECTED_SHA256" ] && \
       mkdir -p "$TMP/unpacked" && \
       python3 -m zipfile -e "$TMP/$ZIP" "$TMP/unpacked" >"$LOG" 2>&1 && \
       [ -f "$TMP/unpacked/bws" ] && chmod 700 "$TMP/unpacked/bws" && \
       [ "$("$TMP/unpacked/bws" --version 2>/dev/null | head -1)" = "bws ${VERSION}" ] && \
       sudo -n install -o root -g root -m 0755 "$TMP/unpacked/bws" /usr/local/bin/bws >"$LOG" 2>&1; then
      BWS_INSTALL=INSTALLED
    else
      BWS_INSTALL=FAILED
    fi
  else
    BWS_INSTALL=PLATFORM_OR_DEPENDENCY_MISMATCH
  fi
fi

if command -v bws >/dev/null 2>&1; then
  BWS_AFTER=YES
  BWS_VERSION="$(bws --version 2>/dev/null | head -1 | tr -cd '[:alnum:]. _-')"
fi

CRED_DECL="$(systemctl show skeleton-runner-poll.service -p LoadCredential -p LoadCredentialEncrypted --value 2>/dev/null || true)"
printf '%s' "$CRED_DECL" | grep -q 'bitwarden-access-token' && TOKEN_CREDENTIAL_DECLARED=YES
printf '%s' "$CRED_DECL" | grep -q 'openrouter-secret-ref' && REF_CREDENTIAL_DECLARED=YES

if [ "$CODE_OK" = YES ] && [ "$BWS_AFTER" = YES ]; then
  if [ "$TOKEN_CREDENTIAL_DECLARED" = YES ] && [ "$REF_CREDENTIAL_DECLARED" = YES ]; then
    RESULT=READY_FOR_PROTECTED_REVIEW
  else
    RESULT=CODE_VALIDATED_BWS_READY_CREDENTIALS_REQUIRED
  fi
fi

{
  echo "### PR #2814 exact-head validation + pinned bws install v3"
  echo
  echo '```text'
  echo "BASE_SHA=$BASE_SHA"
  echo "HEAD_SHA=$HEAD_SHA"
  echo "HOST_IDENTITY=$HOST_OK"
  echo "RUNNER_USER=$USER_OK"
  echo "REPOSITORY=$REPO_OK"
  echo "EXACT_MAIN=$MAIN_OK"
  echo "EXACT_HEAD=$HEAD_OK"
  echo "CHANGED_FILES=$FILES_OK"
  echo "PY_COMPILE=$PY_COMPILE"
  echo "FOCUSED_TESTS=$FOCUSED_TESTS"
  echo "FULL_TESTS=$FULL_TESTS"
  echo "DIFF_CHECK=$DIFF_CHECK"
  echo "BWS_BEFORE=$BWS_BEFORE"
  echo "BWS_INSTALL=$BWS_INSTALL"
  echo "BWS_AFTER=$BWS_AFTER"
  echo "BWS_VERSION=$BWS_VERSION"
  echo "BITWARDEN_TOKEN_CREDENTIAL=$TOKEN_CREDENTIAL_DECLARED"
  echo "OPENROUTER_REF_CREDENTIAL=$REF_CREDENTIAL_DECLARED"
  [ -n "$FAILURES" ] && echo "FAILED_TESTS=$FAILURES"
  echo "RESULT=$RESULT"
  echo '```'
} >"$BODY"

RECEIPT="$(gh pr comment "$PR_NUMBER" --repo "$REPO_FULL" --body-file "$BODY" 2>/dev/null || true)"
echo "RESULT=$RESULT"
if [ -n "$RECEIPT" ]; then
  echo "RECEIPT_REF=$RECEIPT"
else
  echo "RECEIPT_REF=NOT_PUBLISHED"
  cat "$BODY"
fi
REMOTE

rc=$?
echo "RETURNED_TO_TERMUX=1"
echo "REMOTE_RC=$rc"
exit "$rc"

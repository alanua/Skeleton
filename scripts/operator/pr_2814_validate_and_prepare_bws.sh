#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -u

REPO_FULL="alanua/Skeleton"
PR_NUMBER="2814"
BASE_SHA="9a155435fb608858a60b43b9aba835c96f0d330d"
HEAD_SHA="914c19b33a77a761d676f64fedb9d9224505766c"
REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
REF="refs/skeleton-operator/pr2814-prepare"
WT="$(mktemp -d /tmp/skeleton-pr2814-prepare.XXXXXX)"
TMP="$(mktemp -d /tmp/bws-install.XXXXXX)"
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
HEAD_OK=NO
FILES_OK=NO
MODULE_ORIGIN=UNKNOWN
STATIC_SIGNER=FAIL
PY_COMPILE=FAIL
FOCUSED_TESTS=FAIL
FULL_TESTS=FAIL
DIFF_CHECK=FAIL
BWS_BEFORE=NO
BWS_AFTER=NO
BWS_VERSION=UNKNOWN
BWS_INSTALL=NOT_ATTEMPTED
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

if command -v bws >/dev/null 2>&1; then
  BWS_BEFORE=YES
fi

if [ "$HOST_OK" = YES ] && [ "$USER_OK" = YES ] && [ "$REPO_OK" = YES ]; then
  git -C "$REPO_DIR" fetch --quiet origin "pull/${PR_NUMBER}/head:${REF}" >"$LOG" 2>&1 || true
  [ "$(git -C "$REPO_DIR" rev-parse "$REF" 2>/dev/null || true)" = "$HEAD_SHA" ] && HEAD_OK=YES
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

    CLEAN_ENV=(/usr/bin/env -i HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" LANG="C.UTF-8" LC_ALL="C.UTF-8" PYTHONPATH="$WT" PYTHONDONTWRITEBYTECODE=1)
    ORIGIN_RESULT="$(cd "$WT" && "${CLEAN_ENV[@]}" python3 -c 'from pathlib import Path; import core.home_edge.media_source_snapshot as s; import os; print("WORKTREE" if Path(s.__file__).resolve()==Path(os.environ["PYTHONPATH"]).joinpath("core/home_edge/media_source_snapshot.py").resolve() else "OTHER")' 2>/dev/null || true)"
    [ "$ORIGIN_RESULT" = WORKTREE ] && MODULE_ORIGIN=WORKTREE || MODULE_ORIGIN=OTHER

    if (cd "$WT" && "${CLEAN_ENV[@]}" python3 -m py_compile \
        core/secret_store.py integrations/bitwarden_secret_store.py core/runner_child_environment.py \
        tests/test_secret_store.py tests/test_bitwarden_secret_store.py tests/test_runner_child_environment_openrouter.py) >"$LOG" 2>&1; then
      PY_COMPILE=PASS
    fi

    if (cd "$WT" && "${CLEAN_ENV[@]}" timeout 300 python3 -m pytest -q tests/test_home_edge_media_source_snapshot_static_signer.py) >"$LOG" 2>&1; then
      STATIC_SIGNER=PASS
    else
      FAILURES="$(grep '^FAILED ' "$LOG" | head -5 | sed 's/[[:space:]]\+/ /g' | paste -sd ';' -)"
    fi

    if (cd "$WT" && "${CLEAN_ENV[@]}" timeout 600 python3 -m pytest -q \
        tests/test_secret_store.py tests/test_bitwarden_secret_store.py \
        tests/test_runner_child_environment.py tests/test_runner_child_environment_openrouter.py) >"$LOG" 2>&1; then
      FOCUSED_TESTS=PASS
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
if [ "$HOST_OK" = YES ] && [ "$USER_OK" = YES ] && [ "$REPO_OK" = YES ] && [ "$HEAD_OK" = YES ] && \
   [ "$FILES_OK" = YES ] && [ "$MODULE_ORIGIN" = WORKTREE ] && [ "$STATIC_SIGNER" = PASS ] && \
   [ "$PY_COMPILE" = PASS ] && [ "$FOCUSED_TESTS" = PASS ] && [ "$FULL_TESTS" = PASS ] && [ "$DIFF_CHECK" = PASS ]; then
  CODE_OK=YES
fi

if [ "$CODE_OK" = YES ] && [ "$BWS_BEFORE" = NO ]; then
  VERSION="2.1.0"
  ZIP="bws-x86_64-unknown-linux-gnu-${VERSION}.zip"
  SUMS="bws-sha256-checksums-${VERSION}.txt"
  BASE="https://github.com/bitwarden/sdk-sm/releases/download/bws-v${VERSION}"
  if command -v curl >/dev/null 2>&1 && command -v unzip >/dev/null 2>&1; then
    if curl -fsSL "$BASE/$ZIP" -o "$TMP/$ZIP" >"$LOG" 2>&1 && \
       curl -fsSL "$BASE/$SUMS" -o "$TMP/$SUMS" >"$LOG" 2>&1 && \
       (cd "$TMP" && grep -E "(^|[[:space:]])${ZIP}$" "$SUMS" > expected.sha256 && sha256sum -c expected.sha256 >/dev/null 2>&1) && \
       unzip -q "$TMP/$ZIP" -d "$TMP/unpacked" >"$LOG" 2>&1 && \
       [ -f "$TMP/unpacked/bws" ] && chmod 700 "$TMP/unpacked/bws" && \
       [ "$("$TMP/unpacked/bws" --version 2>/dev/null | head -1)" = "bws ${VERSION}" ] && \
       sudo -n install -o root -g root -m 0755 "$TMP/unpacked/bws" /usr/local/bin/bws >"$LOG" 2>&1; then
      BWS_INSTALL=INSTALLED
    else
      BWS_INSTALL=FAILED
    fi
  else
    BWS_INSTALL=DEPENDENCY_MISSING
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
  echo "### PR #2814 hermetic validation + bws runtime prep"
  echo
  echo '```text'
  echo "HEAD_SHA=$HEAD_SHA"
  echo "EXACT_HEAD=$HEAD_OK"
  echo "CHANGED_FILES=$FILES_OK"
  echo "MODULE_ORIGIN=$MODULE_ORIGIN"
  echo "STATIC_SIGNER_TESTS=$STATIC_SIGNER"
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
exit "$rc"

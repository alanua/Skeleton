#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
BRANCH="diagnostic/3594-supervisor-observer"
BOOTSTRAP_SHA="${1:-}"
EXPECTED_WORK_HEAD="${2:-c6a6d67b01e2d50720e219ae97d9c35c335ff42a}"
ALLOWED_BOOTSTRAP_DELTA=$'.github/workflows/issue-3594-one-shot-bootstrap.yml\ndocs/ops/diagnostics/issue-3594-bootstrap-v3.sh'
PR_URL="https://github.com/alanua/Skeleton/pull/3595"
TMP_ROOT="$(mktemp -d)"
STATUS="BOOTSTRAP_FAILED"
DETAIL="unknown"
NEW_HEAD=""
TEST_LINE=""

finish() {
  if command -v gh >/dev/null 2>&1; then
    BODY="[BOOTSTRAP RECEIPT v3]\nstatus=${STATUS}\ndetail=${DETAIL}\nbootstrap_sha=${BOOTSTRAP_SHA:-none}\nexpected_work_head=${EXPECTED_WORK_HEAD:-none}\nhead=${NEW_HEAD:-none}\nfocused_tests=${TEST_LINE:-not_run}\nruntime_mutation=none\nmerge=none\nrequeue=none"
    gh pr comment 3595 --repo alanua/Skeleton --body "$BODY" >/dev/null 2>&1 || true
    gh issue comment 3594 --repo alanua/Skeleton --body "$BODY" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
  printf '%s\n' "$PR_URL"
}
trap finish EXIT

[[ -n "$BOOTSTRAP_SHA" ]] || { DETAIL="bootstrap_sha_missing"; exit 2; }
[[ "$BOOTSTRAP_SHA" =~ ^[0-9a-f]{40}$ ]] || { DETAIL="bootstrap_sha_invalid:${BOOTSTRAP_SHA}"; exit 2; }
[[ "$EXPECTED_WORK_HEAD" =~ ^[0-9a-f]{40}$ ]] || { DETAIL="expected_work_head_invalid:${EXPECTED_WORK_HEAD}"; exit 2; }

ORIGIN="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null)" || { DETAIL="origin_unavailable"; exit 1; }
git clone -q --shared "$REPO_DIR" "$TMP_ROOT/repo" || { DETAIL="clone_failed"; exit 1; }
cd "$TMP_ROOT/repo" || { DETAIL="clone_cd_failed"; exit 1; }
git remote set-url origin "$ORIGIN" || { DETAIL="remote_set_failed"; exit 1; }
git fetch -q origin "$BRANCH" || { DETAIL="fetch_failed"; exit 1; }
git checkout -q -B "$BRANCH" FETCH_HEAD || { DETAIL="checkout_failed"; exit 1; }
ACTUAL_HEAD="$(git rev-parse HEAD)"

if [[ "$ACTUAL_HEAD" != "$BOOTSTRAP_SHA" ]]; then
  DETAIL="bootstrap_head_mismatch:expected=${BOOTSTRAP_SHA},got=${ACTUAL_HEAD}"
  exit 2
fi

if ! git cat-file -e "${EXPECTED_WORK_HEAD}^{commit}" 2>/dev/null; then
  DETAIL="expected_work_head_missing:${EXPECTED_WORK_HEAD}"
  exit 2
fi
if ! git merge-base --is-ancestor "$EXPECTED_WORK_HEAD" "$BOOTSTRAP_SHA"; then
  DETAIL="ancestry_check_failed:expected=${BOOTSTRAP_SHA}_descendant_of=${EXPECTED_WORK_HEAD},got=${ACTUAL_HEAD}"
  exit 2
fi

BOOTSTRAP_DELTA="$(git diff --name-only "$EXPECTED_WORK_HEAD" "$BOOTSTRAP_SHA")"
if [[ "$BOOTSTRAP_DELTA" != "$ALLOWED_BOOTSTRAP_DELTA" ]]; then
  SAFE_DELTA="${BOOTSTRAP_DELTA//$'\n'/,}"
  DETAIL="bootstrap_scope_mismatch:allowed=${ALLOWED_BOOTSTRAP_DELTA//$'\n'/,},got=${SAFE_DELTA:-none}"
  exit 3
fi

python3 - <<'PY' || { DETAIL="patch_failed"; exit 1; }
from pathlib import Path

path = Path("scripts/runner_poll_github_tasks.py")
text = path.read_text(encoding="utf-8")

wrong = '''        handoff_status_code, handoff_status_output = run_command(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workdir,
            observe_process_spawn=True,
        )
'''
right = '''        handoff_status_code, handoff_status_output = run_command(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workdir,
        )
'''
if wrong in text:
    text = text.replace(wrong, right, 1)
elif right not in text:
    raise SystemExit("handoff anchor mismatch")

old_codegen = '''            codex_code, codex_output = run_command(
                codex_exec_command(task_content, workdir, task),
                cwd=workdir,
            )
'''
new_codegen = '''            codex_code, codex_output = run_command(
                codex_exec_command(task_content, workdir, task),
                cwd=workdir,
                observe_process_spawn=True,
            )
'''
if new_codegen not in text:
    if text.count(old_codegen) != 1:
        raise SystemExit(f"codegen anchor count={text.count(old_codegen)}")
    text = text.replace(old_codegen, new_codegen, 1)

if text.count("observe_process_spawn=True") != 1:
    raise SystemExit("observer must be wired exactly once")

path.write_text(text, encoding="utf-8")
PY

cat > tests/test_runner_process_observer_wiring_target.py <<'PY'
from pathlib import Path


def test_observer_is_wired_only_to_real_codex_spawn() -> None:
    text = Path("scripts/runner_poll_github_tasks.py").read_text(encoding="utf-8")
    assert text.count("observe_process_spawn=True") == 1
    expected = '''            codex_code, codex_output = run_command(
                codex_exec_command(task_content, workdir, task),
                cwd=workdir,
                observe_process_spawn=True,
            )
'''
    assert expected in text
    handoff = '''        handoff_status_code, handoff_status_output = run_command(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workdir,
        )
'''
    assert handoff in text
PY

python3 -m pytest -q tests/test_runner_process_observer.py tests/test_runner_process_observer_wiring.py tests/test_runner_process_observer_wiring_target.py > "$TMP_ROOT/pytest.out" 2>&1 || { DETAIL="focused_tests_failed"; TEST_LINE="$(tail -n 1 "$TMP_ROOT/pytest.out" | tr -d '\r')"; exit 1; }
TEST_LINE="$(tail -n 1 "$TMP_ROOT/pytest.out" | tr -d '\r')"

git add scripts/runner_poll_github_tasks.py tests/test_runner_process_observer_wiring_target.py
CHANGED="$(git diff --cached --name-only)"
[[ "$CHANGED" == $'scripts/runner_poll_github_tasks.py\ntests/test_runner_process_observer_wiring_target.py' ]] || { DETAIL="scope_violation:${CHANGED//$'\n'/,}"; exit 3; }

git commit -q -m "diag: wire observer to real Codex spawn for #3594" || { DETAIL="commit_failed"; exit 1; }
NEW_HEAD="$(git rev-parse HEAD)"
git push -q origin "HEAD:$BRANCH" || { DETAIL="push_failed"; exit 1; }
STATUS="BOOTSTRAP_DELIVERED"
DETAIL="observer_wired_to_real_codex_spawn"

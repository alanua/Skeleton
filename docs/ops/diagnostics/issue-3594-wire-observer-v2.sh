#!/usr/bin/env bash
set -uo pipefail
REPO=alanua/Skeleton
PR=3595
ISSUE=3594
BRANCH=diagnostic/3594-supervisor-observer
EXPECTED_HEAD="${1:?expected head required}"
TMP_ROOT="$(mktemp -d)"
STATUS=FAILED
DETAIL=bootstrap_not_started
NEW_HEAD=""
TEST_LINE="not_run"
report(){
  local body="[BOOTSTRAP_DELIVERY RECEIPT v2]\n\nstatus=$STATUS\ndetail=$DETAIL\nexpected_head=$EXPECTED_HEAD\nnew_head=${NEW_HEAD:-none}\nfocused_tests=$TEST_LINE\nmerge=none\nruntime_mutation=none\nrequeue=none\n"
  if command -v gh >/dev/null 2>&1; then
    gh pr comment "$PR" --repo "$REPO" --body "$body" >/dev/null 2>&1 || true
    gh issue comment "$ISSUE" --repo "$REPO" --body "$body" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
  printf 'https://github.com/%s/pull/%s\n' "$REPO" "$PR"
}
trap report EXIT
fail(){ DETAIL="$1"; exit "${2:-1}"; }
REPO_DIR=/home/agent/agent-dev/repos/Skeleton
ORIGIN="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null)" || fail repo_origin_unavailable
git clone -q --shared "$REPO_DIR" "$TMP_ROOT/repo" || fail clone_failed
cd "$TMP_ROOT/repo" || fail clone_cd_failed
git remote set-url origin "$ORIGIN" || fail remote_setup_failed
git fetch -q origin "$BRANCH" || fail fetch_failed
git checkout -q -B "$BRANCH" FETCH_HEAD || fail checkout_failed
ACTUAL_HEAD="$(git rev-parse HEAD)" || fail head_read_failed
[[ "$ACTUAL_HEAD" == "$EXPECTED_HEAD" ]] || fail "head_mismatch_actual_$ACTUAL_HEAD" 2
python3 - <<'PY' || exit 31
from pathlib import Path
p=Path('scripts/runner_poll_github_tasks.py')
s=p.read_text()
anchor='from core.runner_child_environment import sanitize_codegen_child_environment\n'
replacement='''from core.runner_child_environment import sanitize_codegen_child_environment\nfrom core.runner_process_observer import (\n    build_spawn_trace_command,\n    parse_first_denied_filesystem_event,\n)\n'''
if s.count(anchor)!=1: raise SystemExit('import_anchor_mismatch')
s=s.replace(anchor,replacement,1)
old='''def run_command(\n    args: list[str],\n    cwd: str | Path | None = None,\n    *,\n    timeout: int | None = None,\n    input: str | None = None,\n) -> tuple[int, str]:\n'''
new='''def run_command(\n    args: list[str],\n    cwd: str | Path | None = None,\n    *,\n    timeout: int | None = None,\n    input: str | None = None,\n    observe_process_spawn: bool = False,\n) -> tuple[int, str]:\n'''
if s.count(old)!=1: raise SystemExit('run_command_signature_mismatch')
s=s.replace(old,new,1)
oldrun='''    result = subprocess.run(args, **run_kwargs)\n    return result.returncode, result.stdout + result.stderr\n'''
newrun='''    command = args\n    trace_path: Path | None = None\n    provider_started_at_epoch: float | None = None\n    diagnostic_breadcrumb: str | None = None\n    if observe_process_spawn:\n        if shutil.which("strace") is None:\n            diagnostic_breadcrumb = "tracer_unavailable"\n        else:\n            try:\n                trace_parent = Path(cwd) if cwd is not None else ROOT\n                with tempfile.NamedTemporaryFile(prefix=".runner-codegen-trace-", suffix=".log", dir=trace_parent, delete=False) as trace_file:\n                    trace_path = Path(trace_file.name)\n                provider_started_at_epoch = time.time()\n                command = build_spawn_trace_command(args, trace_path=trace_path)\n            except OSError:\n                trace_path = None\n                provider_started_at_epoch = None\n                diagnostic_breadcrumb = "trace_setup_failed_closed"\n    try:\n        result = subprocess.run(command, **run_kwargs)\n        combined_output = result.stdout + result.stderr\n        if trace_path is not None and provider_started_at_epoch is not None:\n            try:\n                trace_text = trace_path.read_text(encoding="utf-8", errors="replace")\n                evidence = parse_first_denied_filesystem_event(trace_text, provider_started_at_epoch=provider_started_at_epoch, executable=args[0] if args else "unknown")\n            except OSError:\n                diagnostic_breadcrumb = "trace_read_failed_closed"\n            else:\n                if evidence is not None:\n                    combined_output += "\\nRUNNER_PROCESS_DIAGNOSTIC=" + json.dumps(evidence.as_public_dict(), sort_keys=True, separators=(",", ":")) + "\\n"\n                else:\n                    diagnostic_breadcrumb = "completed_no_target_event"\n        if diagnostic_breadcrumb is not None:\n            combined_output += "\\nRUNNER_PROCESS_DIAGNOSTIC_CAPTURE=" + diagnostic_breadcrumb + "\\n"\n        return result.returncode, combined_output\n    finally:\n        if trace_path is not None:\n            try:\n                trace_path.unlink(missing_ok=True)\n            except OSError:\n                pass\n'''
if s.count(oldrun)!=1: raise SystemExit('subprocess_boundary_mismatch')
s=s.replace(oldrun,newrun,1)
marker='        codex_code, codex_output = run_command(\n'
start=s.find(marker)
if start<0 or s.find(marker,start+1)>=0: raise SystemExit('codegen_call_anchor_mismatch')
lines=s[start:].splitlines(keepends=True)
for i,line in enumerate(lines[1:],1):
    if line=='        )\n':
        lines.insert(i,'            observe_process_spawn=True,\n'); break
else: raise SystemExit('codegen_call_close_mismatch')
s=s[:start]+''.join(lines)
p.write_text(s)
PY
rc=$?; [[ $rc -eq 0 ]] || fail "protected_patch_failed_rc_$rc"
cat > tests/test_runner_process_observer_wiring.py <<'PY'
from pathlib import Path
from types import SimpleNamespace
import time
from scripts import runner_poll_github_tasks as runner
from core.runner_process_observer import TARGET_DENIED_PATH

def test_observer_wraps_spawn(monkeypatch,tmp_path:Path):
    captured={}
    def fake_run(args,**kwargs):
        captured['args']=list(args); captured['kwargs']=dict(kwargs)
        trace=Path(args[args.index('-o')+1])
        trace.write_text(f'4242 {time.time()+.001:.6f} openat(AT_FDCWD, "{TARGET_DENIED_PATH}", O_RDONLY) = -1 EACCES (Permission denied)\n')
        return SimpleNamespace(returncode=13,stdout='',stderr='provider failed')
    monkeypatch.setattr(runner.shutil,'which',lambda n:'/usr/bin/strace' if n=='strace' else None)
    monkeypatch.setattr(runner.subprocess,'run',fake_run)
    code,out=runner.run_command(['/usr/local/bin/codex','exec'],cwd=tmp_path,input='synthetic-input',observe_process_spawn=True)
    assert captured['args'][0]=='strace'; assert code==13
    assert 'RUNNER_PROCESS_DIAGNOSTIC=' in out and TARGET_DENIED_PATH in out
    assert 'synthetic-input' not in out
    assert not list(tmp_path.glob('.runner-codegen-trace-*.log'))

def test_observer_no_tracer(monkeypatch,tmp_path:Path):
    captured={}
    def fake_run(args,**kwargs): captured['args']=list(args); return SimpleNamespace(returncode=0,stdout='ok',stderr='')
    monkeypatch.setattr(runner.shutil,'which',lambda n:None)
    monkeypatch.setattr(runner.subprocess,'run',fake_run)
    code,out=runner.run_command(['/usr/local/bin/codex','exec'],cwd=tmp_path,observe_process_spawn=True)
    assert captured['args']==['/usr/local/bin/codex','exec']; assert code==0
    assert 'RUNNER_PROCESS_DIAGNOSTIC_CAPTURE=tracer_unavailable' in out
PY
python3 -m pytest -q tests/test_runner_process_observer.py tests/test_runner_process_observer_wiring.py >"$TMP_ROOT/pytest.out" 2>&1 || { TEST_LINE="$(tail -n 1 "$TMP_ROOT/pytest.out"|tr -d '\r')"; fail tests_failed; }
TEST_LINE="$(tail -n 1 "$TMP_ROOT/pytest.out"|tr -d '\r')"
rm -f docs/ops/diagnostics/issue-3594-wire-observer-v2.sh
git add scripts/runner_poll_github_tasks.py tests/test_runner_process_observer_wiring.py docs/ops/diagnostics/issue-3594-wire-observer-v2.sh || fail git_add_failed
CHANGED="$(git diff --cached --name-only)"
printf '%s\n' "$CHANGED" | grep -Ev '^(scripts/runner_poll_github_tasks\.py|tests/test_runner_process_observer_wiring\.py|docs/ops/diagnostics/issue-3594-wire-observer-v2\.sh)$' >/dev/null && fail scope_violation
git commit -q -m 'diag: wire supervisor observer for #3594' || fail commit_failed
NEW_HEAD="$(git rev-parse HEAD)"
git push -q origin "HEAD:$BRANCH" || fail push_failed
STATUS=SUCCESS
DETAIL=diagnostic_pr_ready_candidate

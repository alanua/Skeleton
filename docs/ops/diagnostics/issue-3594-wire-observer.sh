#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HEAD="${1:?expected branch head required}"
REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
BRANCH="diagnostic/3594-supervisor-observer"
PR_URL="https://github.com/alanua/Skeleton/pull/3595"
SCRIPT_PATH="docs/ops/diagnostics/issue-3594-wire-observer.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

ORIGIN="$(git -C "$REPO_DIR" remote get-url origin)"
git clone -q --shared "$REPO_DIR" "$TMP_ROOT/repo"
cd "$TMP_ROOT/repo"
git remote set-url origin "$ORIGIN"
git fetch -q origin "$BRANCH"
git checkout -q -B "$BRANCH" FETCH_HEAD
ACTUAL_HEAD="$(git rev-parse HEAD)"
if [[ "$ACTUAL_HEAD" != "$EXPECTED_HEAD" ]]; then
  printf 'HEAD_MISMATCH expected=%s actual=%s\n' "$EXPECTED_HEAD" "$ACTUAL_HEAD" >&2
  exit 2
fi

python3 - <<'PY'
from pathlib import Path

path = Path("scripts/runner_poll_github_tasks.py")
text = path.read_text(encoding="utf-8")

import_anchor = "from core.runner_child_environment import sanitize_codegen_child_environment\n"
import_block = """from core.runner_child_environment import sanitize_codegen_child_environment
from core.runner_process_observer import (
    build_spawn_trace_command,
    parse_first_denied_filesystem_event,
)
"""
if text.count(import_anchor) != 1:
    raise SystemExit("protected wiring abort: import anchor mismatch")
text = text.replace(import_anchor, import_block, 1)

signature_old = """def run_command(
    args: list[str],
    cwd: str | Path | None = None,
    *,
    timeout: int | None = None,
    input: str | None = None,
) -> tuple[int, str]:
"""
signature_new = """def run_command(
    args: list[str],
    cwd: str | Path | None = None,
    *,
    timeout: int | None = None,
    input: str | None = None,
    observe_process_spawn: bool = False,
) -> tuple[int, str]:
"""
if text.count(signature_old) != 1:
    raise SystemExit("protected wiring abort: run_command signature mismatch")
text = text.replace(signature_old, signature_new, 1)

run_old = """    result = subprocess.run(args, **run_kwargs)
    return result.returncode, result.stdout + result.stderr
"""
run_new = """    command = args
    trace_path: Path | None = None
    provider_started_at_epoch: float | None = None
    diagnostic_breadcrumb: str | None = None
    if observe_process_spawn:
        if shutil.which(\"strace\") is None:
            diagnostic_breadcrumb = \"tracer_unavailable\"
        else:
            try:
                trace_parent = Path(cwd) if cwd is not None else ROOT
                with tempfile.NamedTemporaryFile(
                    prefix=\".runner-codegen-trace-\",
                    suffix=\".log\",
                    dir=trace_parent,
                    delete=False,
                ) as trace_file:
                    trace_path = Path(trace_file.name)
                provider_started_at_epoch = time.time()
                command = build_spawn_trace_command(args, trace_path=trace_path)
            except OSError:
                trace_path = None
                provider_started_at_epoch = None
                diagnostic_breadcrumb = \"trace_setup_failed_closed\"

    try:
        result = subprocess.run(command, **run_kwargs)
        combined_output = result.stdout + result.stderr
        if trace_path is not None and provider_started_at_epoch is not None:
            try:
                trace_text = trace_path.read_text(encoding=\"utf-8\", errors=\"replace\")
                evidence = parse_first_denied_filesystem_event(
                    trace_text,
                    provider_started_at_epoch=provider_started_at_epoch,
                    executable=args[0] if args else \"unknown\",
                )
            except OSError:
                diagnostic_breadcrumb = \"trace_read_failed_closed\"
            else:
                if evidence is not None:
                    combined_output += (
                        \"\\nRUNNER_PROCESS_DIAGNOSTIC=\"
                        + json.dumps(
                            evidence.as_public_dict(),
                            sort_keys=True,
                            separators=(\",\", \":\"),
                        )
                        + \"\\n\"
                    )
                else:
                    diagnostic_breadcrumb = \"completed_no_target_event\"
        if diagnostic_breadcrumb is not None:
            combined_output += (
                \"\\nRUNNER_PROCESS_DIAGNOSTIC_CAPTURE=\"
                + diagnostic_breadcrumb
                + \"\\n\"
            )
        return result.returncode, combined_output
    finally:
        if trace_path is not None:
            try:
                trace_path.unlink(missing_ok=True)
            except OSError:
                pass
"""
if text.count(run_old) != 1:
    raise SystemExit("protected wiring abort: subprocess boundary mismatch")
text = text.replace(run_old, run_new, 1)

start_marker = "        codex_code, codex_output = run_command(\n"
start = text.find(start_marker)
if start < 0 or text.find(start_marker, start + 1) >= 0:
    raise SystemExit("protected wiring abort: codegen call anchor mismatch")
lines = text[start:].splitlines(keepends=True)
insert_at = None
for i, line in enumerate(lines[1:], start=1):
    if line == "        )\n":
        insert_at = i
        break
if insert_at is None:
    raise SystemExit("protected wiring abort: codegen call close mismatch")
lines.insert(insert_at, "            observe_process_spawn=True,\n")
text = text[:start] + "".join(lines)
path.write_text(text, encoding="utf-8")
PY

cat > tests/test_runner_process_observer_wiring.py <<'PY'
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time

from scripts import runner_poll_github_tasks as runner
from core.runner_process_observer import TARGET_DENIED_PATH


def test_run_command_observer_wraps_real_spawn_and_preserves_contract(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = dict(kwargs)
        trace_path = Path(args[args.index("-o") + 1])
        trace_path.write_text(
            f'4242 {time.time() + 0.001:.6f} openat(AT_FDCWD, "{TARGET_DENIED_PATH}", O_RDONLY) = -1 EACCES (Permission denied)\n',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=13, stdout="", stderr="provider failed")

    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/strace" if name == "strace" else None)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    token = runner._RUN_COMMAND_ENV_OVERRIDE.set({"SYNTHETIC_ENV_NAME": "synthetic-value"})
    try:
        code, output = runner.run_command(
            ["/usr/local/bin/codex", "exec"],
            cwd=tmp_path,
            input="synthetic-input",
            observe_process_spawn=True,
        )
    finally:
        runner._RUN_COMMAND_ENV_OVERRIDE.reset(token)

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert args[0] == "strace"
    assert args[-3:] == ["--", "/usr/local/bin/codex", "exec"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"] == {"SYNTHETIC_ENV_NAME": "synthetic-value"}
    assert kwargs["input"] == "synthetic-input"
    assert code == 13
    assert "RUNNER_PROCESS_DIAGNOSTIC=" in output
    assert TARGET_DENIED_PATH in output
    assert "synthetic-value" not in output
    assert "synthetic-input" not in output
    assert not list(tmp_path.glob(".runner-codegen-trace-*.log"))


def test_run_command_observer_falls_back_without_tracer(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    code, output = runner.run_command(
        ["/usr/local/bin/codex", "exec"],
        cwd=tmp_path,
        observe_process_spawn=True,
    )

    assert captured["args"] == ["/usr/local/bin/codex", "exec"]
    assert code == 0
    assert output.startswith("ok")
    assert "RUNNER_PROCESS_DIAGNOSTIC_CAPTURE=tracer_unavailable" in output
PY

rm -f "$SCRIPT_PATH"
python3 -m pytest -q tests/test_runner_process_observer.py tests/test_runner_process_observer_wiring.py > "$TMP_ROOT/pytest.out"
TEST_LINE="$(tail -n 1 "$TMP_ROOT/pytest.out" | tr -d '\r')"

git add scripts/runner_poll_github_tasks.py core/runner_process_observer.py tests/test_runner_process_observer.py tests/test_runner_process_observer_wiring.py "$SCRIPT_PATH"
if git diff --cached --name-only | grep -Ev '^(scripts/runner_poll_github_tasks\.py|core/runner_process_observer\.py|tests/test_runner_process_observer\.py|tests/test_runner_process_observer_wiring\.py|docs/ops/diagnostics/issue-3594-wire-observer\.sh)$' >/dev/null; then
  echo "scope violation" >&2
  exit 3
fi
git commit -q -m "diag: wire supervisor observer for #3594"
NEW_HEAD="$(git rev-parse HEAD)"
git push -q origin "HEAD:$BRANCH"

COMMENT=$(cat <<EOF
[BOOTSTRAP_DELIVERY RECEIPT]

Protected wiring authorized by Oleksii and applied to PR #3595 only.

head=$NEW_HEAD
focused_tests=$TEST_LINE
scope=scripts/runner_poll_github_tasks.py + observer helper/tests only
runtime_mutation=none
merge=none
requeue=none
milestone=DIAGNOSTIC_PR_READY_CANDIDATE

This is not DIAGNOSIS_COMPLETE. No live codegen canary has run.
EOF
)
if command -v gh >/dev/null 2>&1; then
  gh pr comment 3595 --repo alanua/Skeleton --body "$COMMENT" >/dev/null 2>&1 || true
  gh issue comment 3594 --repo alanua/Skeleton --body "$COMMENT" >/dev/null 2>&1 || true
fi
printf '%s\n' "$PR_URL"

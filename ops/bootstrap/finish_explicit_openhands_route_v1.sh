#!/usr/bin/env bash
set -euo pipefail

REPO=/home/agent/agent-dev/repos/Skeleton
WT=/home/agent/agent-dev/worktrees/openhands-explicit-route-v1
TARGET_BRANCH=runner/temporary-explicit-openhands-route-v1
EXPECTED_MAIN=21e023ae92a78477231ff9b9139980e1e814ce15
EXPECTED_TARGET_HEAD=bbbd15172644749835a7b1bb7c738e7764ebd100

cd "$REPO"

echo "=== SAFETY ==="
test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$EXPECTED_MAIN"
test -z "$(git status --porcelain --untracked-files=all)"

git fetch origin main "$TARGET_BRANCH"
test "$(git rev-parse origin/main)" = "$EXPECTED_MAIN"
test "$(git rev-parse origin/$TARGET_BRANCH)" = "$EXPECTED_TARGET_HEAD"
test ! -e "$WT"

git worktree add --detach "$WT" "origin/$TARGET_BRANCH"
cd "$WT"

python3 - <<'PY'
from pathlib import Path

poller = Path("scripts/runner_poll_github_tasks.py")
text = poller.read_text(encoding="utf-8")

old_import = "from core.runner_child_environment import sanitize_codegen_child_environment\n"
new_import = old_import + '''from core.runner_codegen_router import (\n    CodegenRouteError,\n    codex_failure_allows_secondary,\n    openhands_secondary_command,\n    prepare_openhands_secondary_environment,\n    select_openhands_secondary_route,\n    task_contract_allows_cloud_secondary,\n)\n'''
if text.count(old_import) != 1:
    raise SystemExit("BLOCKED: runner child environment import anchor mismatch")
text = text.replace(old_import, new_import, 1)

old_block = '''        token = _RUN_COMMAND_ENV_OVERRIDE.set(\n            sanitize_codegen_child_environment(os.environ)\n        )\n        try:\n            return run_command(\n                codex_exec_command(task_content, workdir, task),\n                cwd=workdir,\n            )\n        finally:\n            _RUN_COMMAND_ENV_OVERRIDE.reset(token)\n'''
new_block = '''        base_codegen_environment = sanitize_codegen_child_environment(os.environ)\n        token = _RUN_COMMAND_ENV_OVERRIDE.set(base_codegen_environment)\n        try:\n            codex_code, codex_output = run_command(\n                codex_exec_command(task_content, workdir, task),\n                cwd=workdir,\n            )\n        finally:\n            _RUN_COMMAND_ENV_OVERRIDE.reset(token)\n\n        if not codex_failure_allows_secondary(codex_code, codex_output):\n            return codex_code, codex_output\n        if not task_contract_allows_cloud_secondary(task_content):\n            return codex_code, codex_output\n\n        try:\n            secondary_route = select_openhands_secondary_route()\n            openhands_environment, route_receipt = prepare_openhands_secondary_environment(\n                authority_environment=os.environ,\n                base_environment=base_codegen_environment,\n                route=secondary_route,\n            )\n        except CodegenRouteError:\n            return codex_code, codex_output\n\n        openhands_bin = shutil.which(\n            "openhands", path=openhands_environment.get("PATH")\n        )\n        if openhands_bin is None:\n            return codex_code, codex_output\n\n        token = _RUN_COMMAND_ENV_OVERRIDE.set(openhands_environment)\n        try:\n            openhands_code, openhands_output = run_command(\n                openhands_secondary_command(\n                    task_content, executable=openhands_bin\n                ),\n                cwd=workdir,\n                timeout=secondary_route.binding.timeout_seconds,\n            )\n        except (OSError, subprocess.SubprocessError):\n            return codex_code, codex_output\n        finally:\n            _RUN_COMMAND_ENV_OVERRIDE.reset(token)\n\n        route_marker = (\n            "SKELETON_CODEGEN_EXECUTOR=openhands\\n"\n            f"SKELETON_CODEGEN_MODEL={route_receipt['model_id']}\\n"\n            f"SKELETON_CODEGEN_BINDING={route_receipt['binding_id']}\\n"\n            f"SKELETON_CODEGEN_LEASE={route_receipt['lease_hash']}\\n"\n            "SKELETON_CODEGEN_PRIMARY_FAILURE=quota_or_provider_outage\\n"\n        )\n        if openhands_code != 0:\n            return openhands_code, route_marker + openhands_output\n\n        status_code, status_output = run_command(\n            ["git", "status", "--porcelain", "--untracked-files=all"],\n            cwd=workdir,\n        )\n        if status_code != 0 or not status_output.strip():\n            return (\n                1,\n                route_marker\n                + "SKELETON_CODEGEN_SECONDARY_FAILURE=DELIVERABLE_MISSING\\n"\n                + openhands_output,\n            )\n        return 0, route_marker + openhands_output\n'''
if text.count(old_block) != 1:
    raise SystemExit("BLOCKED: run_codex_task anchor mismatch")
text = text.replace(old_block, new_block, 1)
poller.write_text(text, encoding="utf-8")

tests = Path("tests/test_runner_poll_github_tasks.py")
test_text = tests.read_text(encoding="utf-8")
marker = "def test_run_codex_task_explicitly_reroutes_quota_to_openhands"
if marker in test_text:
    raise SystemExit("BLOCKED: integration tests already present")
append = r'''


def test_run_codex_task_explicitly_reroutes_quota_to_openhands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_content = """requested_capabilities: [repository_read, repository_write, test_execution]
privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY
"""
    monkeypatch.setattr(runner, "private_memory_bootstrap_request", lambda *_args: None)
    monkeypatch.setattr(
        runner, "sanitize_codegen_child_environment", lambda _env: {"PATH": "/usr/bin"}
    )
    monkeypatch.setattr(runner, "codex_exec_command", lambda *_args: ["codex"])
    route = mock.Mock()
    route.binding.timeout_seconds = 1800
    monkeypatch.setattr(runner, "select_openhands_secondary_route", lambda: route)
    monkeypatch.setattr(
        runner,
        "prepare_openhands_secondary_environment",
        lambda **_kwargs: (
            {
                "PATH": "/usr/bin",
                "LLM_API_KEY": "synthetic-secret-marker",
                "LLM_MODEL": "openrouter/moonshotai/kimi-k2",
            },
            {
                "model_id": "openrouter-kimi-k2-challenger",
                "binding_id": "binding-test",
                "lease_hash": "a" * 64,
            },
        ),
    )
    monkeypatch.setattr(
        runner.shutil,
        "which",
        lambda name, path=None: "/usr/bin/openhands" if name == "openhands" else None,
    )
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, **_kwargs):
        calls.append(list(args))
        if args[0] == "codex":
            return 1, "usage limit reached"
        if args[0] == "/usr/bin/openhands":
            environment = runner._RUN_COMMAND_ENV_OVERRIDE.get()
            assert environment is not None
            assert environment["LLM_MODEL"] == "openrouter/moonshotai/kimi-k2"
            return 0, "DONE: OpenHands completed the bounded task."
        if args[:3] == ["git", "status", "--porcelain"]:
            return 0, " M core/example.py\n"
        raise AssertionError(args)

    monkeypatch.setattr(runner, "run_command", fake_run)
    code, output = runner.run_codex_task(task_content, str(tmp_path))
    assert code == 0
    assert "SKELETON_CODEGEN_EXECUTOR=openhands" in output
    assert "SKELETON_CODEGEN_MODEL=openrouter-kimi-k2-challenger" in output
    assert "synthetic-secret-marker" not in output
    assert [call[0] for call in calls] == ["codex", "/usr/bin/openhands", "git"]


def test_run_codex_task_does_not_reroute_ordinary_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_content = """requested_capabilities: [repository_read, repository_write, test_execution]
privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY
"""
    monkeypatch.setattr(runner, "private_memory_bootstrap_request", lambda *_args: None)
    monkeypatch.setattr(
        runner, "sanitize_codegen_child_environment", lambda _env: {"PATH": "/usr/bin"}
    )
    monkeypatch.setattr(runner, "codex_exec_command", lambda *_args: ["codex"])
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, **_kwargs):
        calls.append(list(args))
        return 1, "tests failed"

    monkeypatch.setattr(runner, "run_command", fake_run)
    code, output = runner.run_codex_task(task_content, str(tmp_path))
    assert code == 1
    assert output == "tests failed"
    assert calls == [["codex"]]


def test_run_codex_task_rejects_zero_edit_openhands_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_content = """requested_capabilities: [repository_read, repository_write, test_execution]
privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY
"""
    monkeypatch.setattr(runner, "private_memory_bootstrap_request", lambda *_args: None)
    monkeypatch.setattr(
        runner, "sanitize_codegen_child_environment", lambda _env: {"PATH": "/usr/bin"}
    )
    monkeypatch.setattr(runner, "codex_exec_command", lambda *_args: ["codex"])
    route = mock.Mock()
    route.binding.timeout_seconds = 1800
    monkeypatch.setattr(runner, "select_openhands_secondary_route", lambda: route)
    monkeypatch.setattr(
        runner,
        "prepare_openhands_secondary_environment",
        lambda **_kwargs: (
            {"PATH": "/usr/bin", "LLM_API_KEY": "secret", "LLM_MODEL": "model"},
            {
                "model_id": "openrouter-kimi-k2-challenger",
                "binding_id": "binding-test",
                "lease_hash": "b" * 64,
            },
        ),
    )
    monkeypatch.setattr(runner.shutil, "which", lambda *_args, **_kwargs: "/usr/bin/openhands")

    def fake_run(args, cwd=None, **_kwargs):
        if args[0] == "codex":
            return 1, "quota exceeded"
        if args[0] == "/usr/bin/openhands":
            return 0, "RESULT: OK"
        if args[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        raise AssertionError(args)

    monkeypatch.setattr(runner, "run_command", fake_run)
    code, output = runner.run_codex_task(task_content, str(tmp_path))
    assert code == 1
    assert "DELIVERABLE_MISSING" in output
    assert "RESULT: OK" in output


def test_run_codex_task_does_not_send_private_or_read_only_task_to_openhands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_content = """requested_capabilities: [repository_read, repository_write]
privacy_boundary: PRIVATE_LOCAL_ONLY
"""
    monkeypatch.setattr(runner, "private_memory_bootstrap_request", lambda *_args: None)
    monkeypatch.setattr(
        runner, "sanitize_codegen_child_environment", lambda _env: {"PATH": "/usr/bin"}
    )
    monkeypatch.setattr(runner, "codex_exec_command", lambda *_args: ["codex"])
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, **_kwargs):
        calls.append(list(args))
        return 1, "quota exceeded"

    monkeypatch.setattr(runner, "run_command", fake_run)
    code, _output = runner.run_codex_task(task_content, str(tmp_path))
    assert code == 1
    assert calls == [["codex"]]
'''
tests.write_text(test_text + append, encoding="utf-8")
PY

echo "=== ALLOWLIST ==="
CHANGED="$(git diff --name-only origin/main...HEAD; git diff --name-only)"
CHANGED="$(printf '%s\n' "$CHANGED" | sed '/^$/d' | sort -u)"
printf '%s\n' "$CHANGED"
for f in $CHANGED; do
  case "$f" in
    MODEL_REGISTRY.yaml|\
    core/runner_codegen_router.py|\
    scripts/runner_poll_github_tasks.py|\
    tests/test_model_registry.py|\
    tests/test_model_selector.py|\
    tests/test_runner_codegen_router.py|\
    tests/test_runner_poll_github_tasks.py)
      ;;
    *)
      echo "BLOCKED: unexpected file $f"
      exit 1
      ;;
  esac
done

echo "=== FOCUSED ==="
python3 -m pytest -q \
  tests/test_runner_codegen_router.py \
  tests/test_model_registry.py \
  tests/test_model_selector.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py

echo "=== STATIC ==="
python3 -m py_compile \
  core/runner_codegen_router.py \
  scripts/runner_poll_github_tasks.py \
  tests/test_runner_codegen_router.py \
  tests/test_model_registry.py \
  tests/test_model_selector.py \
  tests/test_runner_poll_github_tasks.py
python3 - <<'PY'
import json
for path in ("MODEL_REGISTRY.yaml", "EXECUTOR_REGISTRY.yaml"):
    with open(path, encoding="utf-8") as handle:
        json.load(handle)
print("REGISTRIES=PASS")
PY
git diff --check

echo "=== FULL ==="
python3 -m pytest -q

echo "=== COMMIT ==="
git add \
  MODEL_REGISTRY.yaml \
  core/runner_codegen_router.py \
  scripts/runner_poll_github_tasks.py \
  tests/test_model_registry.py \
  tests/test_model_selector.py \
  tests/test_runner_codegen_router.py \
  tests/test_runner_poll_github_tasks.py

git commit -m "P0 wire explicit OpenHands task-fit secondary route"
NEW_HEAD="$(git rev-parse HEAD)"

git diff --check origin/main...HEAD

echo "=== PUSH SAME BRANCH ==="
git push \
  --force-with-lease="refs/heads/$TARGET_BRANCH:$EXPECTED_TARGET_HEAD" \
  origin "HEAD:refs/heads/$TARGET_BRANCH"

cd "$REPO"
git worktree remove "$WT"

test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$EXPECTED_MAIN"
test -z "$(git status --porcelain --untracked-files=all)"

echo "RESULT=DONE"
echo "HEAD=$NEW_HEAD"
echo "CANONICAL_RUNTIME_HEAD=$(git rev-parse HEAD)"

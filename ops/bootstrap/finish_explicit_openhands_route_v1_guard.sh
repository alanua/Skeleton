#!/usr/bin/env bash
set -euo pipefail

REPO=/home/agent/agent-dev/repos/Skeleton
WT=/home/agent/agent-dev/worktrees/openhands-explicit-route-guard-v1
TARGET_BRANCH=runner/temporary-explicit-openhands-route-v1
EXPECTED_MAIN=21e023ae92a78477231ff9b9139980e1e814ce15
EXPECTED_TARGET_HEAD=e4216e6fd394a6842df4264a8b85a61fff82d572

cd "$REPO"
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

poller = Path('scripts/runner_poll_github_tasks.py')
text = poller.read_text(encoding='utf-8')
old = '''        if not task_contract_allows_cloud_secondary(task_content):\n            return codex_code, codex_output\n\n        try:\n            secondary_route = select_openhands_secondary_route()\n'''
new = '''        if not task_contract_allows_cloud_secondary(task_content):\n            return codex_code, codex_output\n\n        handoff_status_code, handoff_status_output = run_command(\n            [\"git\", \"status\", \"--porcelain\", \"--untracked-files=all\"],\n            cwd=workdir,\n        )\n        if handoff_status_code != 0 or handoff_status_output.strip():\n            return (\n                1,\n                codex_output\n                + \"\\nSKELETON_CODEGEN_SECONDARY_FAILURE=PRIMARY_LEFT_WORKTREE_DIRTY\\n\",\n            )\n\n        try:\n            secondary_route = select_openhands_secondary_route()\n'''
if text.count(old) != 1:
    raise SystemExit('BLOCKED: poller handoff anchor mismatch')
poller.write_text(text.replace(old, new, 1), encoding='utf-8')

tests = Path('tests/test_runner_poll_github_tasks.py')
s = tests.read_text(encoding='utf-8')
s = s.replace(
    'assert [call[0] for call in calls] == ["codex", "/usr/bin/openhands", "git"]',
    'assert [call[0] for call in calls] == ["codex", "git", "/usr/bin/openhands", "git"]',
    1,
)
old_fake = '''    def fake_run(args, cwd=None, **_kwargs):\n        calls.append(list(args))\n        if args[0] == \"codex\":\n            return 1, \"usage limit reached\"\n        if args[0] == \"/usr/bin/openhands\":\n'''
new_fake = '''    status_calls = 0\n\n    def fake_run(args, cwd=None, **_kwargs):\n        nonlocal status_calls\n        calls.append(list(args))\n        if args[0] == \"codex\":\n            return 1, \"usage limit reached\"\n        if args[:3] == [\"git\", \"status\", \"--porcelain\"]:\n            status_calls += 1\n            return (0, \"\" if status_calls == 1 else \" M core/example.py\\n\")\n        if args[0] == \"/usr/bin/openhands\":\n'''
if s.count(old_fake) != 1:
    raise SystemExit('BLOCKED: reroute fake anchor mismatch')
s = s.replace(old_fake, new_fake, 1)
s = s.replace('''        if args[:3] == [\"git\", \"status\", \"--porcelain\"]:\n            return 0, \" M core/example.py\\n\"\n        raise AssertionError(args)\n''','''        raise AssertionError(args)\n''',1)

old_zero = '''    def fake_run(args, cwd=None, **_kwargs):\n        if args[0] == \"codex\":\n            return 1, \"quota exceeded\"\n        if args[0] == \"/usr/bin/openhands\":\n            return 0, \"RESULT: OK\"\n        if args[:3] == [\"git\", \"status\", \"--porcelain\"]:\n            return 0, \"\"\n        raise AssertionError(args)\n'''
new_zero = '''    def fake_run(args, cwd=None, **_kwargs):\n        if args[0] == \"codex\":\n            return 1, \"quota exceeded\"\n        if args[:3] == [\"git\", \"status\", \"--porcelain\"]:\n            return 0, \"\"\n        if args[0] == \"/usr/bin/openhands\":\n            return 0, \"RESULT: OK\"\n        raise AssertionError(args)\n'''
if s.count(old_zero) != 1:
    raise SystemExit('BLOCKED: zero-edit fake anchor mismatch')
s = s.replace(old_zero, new_zero, 1)

append = r'''


def test_run_codex_task_does_not_handoff_dirty_primary_worktree(
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
        if args[0] == "codex":
            return 1, "quota exceeded"
        if args[:3] == ["git", "status", "--porcelain"]:
            return 0, " M core/partial.py\n"
        raise AssertionError(args)

    monkeypatch.setattr(runner, "run_command", fake_run)
    code, output = runner.run_codex_task(task_content, str(tmp_path))
    assert code == 1
    assert "PRIMARY_LEFT_WORKTREE_DIRTY" in output
    assert [call[0] for call in calls] == ["codex", "git"]
'''
if 'test_run_codex_task_does_not_handoff_dirty_primary_worktree' in s:
    raise SystemExit('BLOCKED: dirty handoff test already present')
tests.write_text(s + append, encoding='utf-8')
PY

python3 -m pytest -q tests/test_runner_codegen_router.py tests/test_execution_fabric.py tests/test_model_registry.py tests/test_model_selector.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py
python3 -m py_compile core/runner_codegen_router.py scripts/runner_poll_github_tasks.py tests/test_runner_codegen_router.py tests/test_runner_poll_github_tasks.py
git diff --check
python3 -m pytest -q

git add scripts/runner_poll_github_tasks.py tests/test_runner_poll_github_tasks.py
git commit -m "P0 guard OpenHands handoff from dirty Codex worktree"
NEW_HEAD="$(git rev-parse HEAD)"
git push --force-with-lease="refs/heads/$TARGET_BRANCH:$EXPECTED_TARGET_HEAD" origin "HEAD:refs/heads/$TARGET_BRANCH"

cd "$REPO"
git worktree remove "$WT"
test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$EXPECTED_MAIN"
test -z "$(git status --porcelain --untracked-files=all)"
echo "RESULT=DONE"
echo "HEAD=$NEW_HEAD"

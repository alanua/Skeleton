#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
REPO_FULL = 'alanua/Skeleton'
ISSUE = '2832'
TARGET_SHA = '89843be0c8e67e3a048fd473bdf293b464da7590'
BRANCH = 'runner/validation-secret-isolation-v1'
WORKTREE_ROOT = Path('/home/agent/agent-dev/worktrees/skeleton')

OLD_FUNCTION = '''def _validation_command_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return sanitize_codegen_child_environment(source)
'''

NEW_FUNCTION = '''def _validation_command_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return sanitize_codegen_child_environment(
        source,
        authority_environment={},
    )
'''

TEST_NAME = 'test_validation_command_environment_does_not_bind_codegen_fallback_authority'
TEST_APPEND = r'''


def test_validation_command_environment_does_not_bind_codegen_fallback_authority() -> None:
    source = {
        "PATH": "/usr/bin",
        "SAFE_SETTING": "kept",
        "SKELETON_OPENROUTER_FALLBACK_API_KEY": "synthetic-fallback-key",
        "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/synthetic/model",
        "SKELETON_OPENHANDS_OPENROUTER_REQUIRED": "1",
        "OPENROUTER_API_KEY": "synthetic-openrouter-key",
        "BWS_ACCESS_TOKEN": "synthetic-bws-token",
        "CREDENTIALS_DIRECTORY": "/synthetic/credentials",
        "LLM_API_KEY": "synthetic-llm-key",
        "LLM_MODEL": "synthetic/model",
    }

    sanitized = runner._validation_command_environment(source)

    assert sanitized == {"PATH": "/usr/bin", "SAFE_SETTING": "kept"}
'''

SECRET_ENV_NAMES = (
    'SKELETON_OPENROUTER_FALLBACK_API_KEY',
    'SKELETON_OPENROUTER_FALLBACK_MODEL',
    'SKELETON_OPENHANDS_OPENROUTER_REQUIRED',
    'OPENROUTER_API_KEY',
    'BWS_ACCESS_TOKEN',
    'CREDENTIALS_DIRECTORY',
    'LLM_API_KEY',
    'LLM_MODEL',
    'LLM_BASE_URL',
    'MAX_BUDGET_PER_TASK',
    'MAX_ITERATIONS',
    'LLM_NUM_RETRIES',
)


class BootstrapError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 2400, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise BootstrapError(f'command_failed:{Path(argv[0]).name}')
    return proc


def safe_test_env() -> dict[str, str]:
    environment = dict(os.environ)
    for name in SECRET_ENV_NAMES:
        environment.pop(name, None)
    return environment


def failed_nodes(output: str) -> str:
    nodes: list[str] = []
    for line in output.splitlines():
        if line.startswith('FAILED '):
            node = line[len('FAILED '):].split(' - ', 1)[0].strip()
            if node and node not in nodes:
                nodes.append(node)
        if len(nodes) >= 8:
            break
    return ';'.join(nodes) or 'NONE_REPORTED'


def publish(status: str, reason: str, **fields: str) -> str:
    lines = [
        '### #2832 deterministic protected validation-isolation receipt',
        '',
        '```text',
        f'STATUS={status}',
        f'REASON={reason}',
        f'BASE={TARGET_SHA}',
    ]
    for key, value in fields.items():
        safe = str(value).replace('\n', ' ')[:500]
        lines.append(f'{key}={safe}')
    lines.append('```')
    body = '\n'.join(lines)
    proc = run(['gh', 'issue', 'comment', ISSUE, '--repo', REPO_FULL, '--body', body], check=False, timeout=60)
    return proc.stdout.strip() if proc.returncode == 0 else 'NOT_PUBLISHED'


def main() -> int:
    if socket.gethostname() != 'hetzner-agent-runner-1' or os.geteuid() == 0:
        print('RESULT=BLOCKED:identity')
        return 0
    if os.environ.get('USER') != 'agent':
        print('RESULT=BLOCKED:user')
        return 0
    if not (REPO / '.git').is_dir():
        print('RESULT=BLOCKED:repo_missing')
        return 0

    origin = run(['git', '-C', str(REPO), 'remote', 'get-url', 'origin']).stdout.strip()
    if not origin.endswith('alanua/Skeleton.git'):
        print('RESULT=BLOCKED:origin_mismatch')
        return 0
    if run(['git', '-C', str(REPO), 'status', '--porcelain', '--untracked-files=all']).stdout.strip():
        print('RESULT=BLOCKED:canonical_dirty')
        return 0
    run(['git', '-C', str(REPO), 'fetch', '--quiet', 'origin', 'main'])
    current_main = run(['git', '-C', str(REPO), 'rev-parse', 'origin/main']).stdout.strip()
    if current_main != TARGET_SHA:
        ref = publish('BLOCKED', 'main_moved', CURRENT_MAIN=current_main)
        print('RESULT=BLOCKED:main_moved')
        print('RECEIPT_REF=' + ref)
        return 0

    if run(['git', '-C', str(REPO), 'show-ref', '--verify', f'refs/heads/{BRANCH}'], check=False).returncode == 0:
        ref = publish('BLOCKED', 'local_branch_exists')
        print('RESULT=BLOCKED:local_branch_exists')
        print('RECEIPT_REF=' + ref)
        return 0
    if run(['git', '-C', str(REPO), 'ls-remote', '--exit-code', '--heads', 'origin', BRANCH], check=False).returncode == 0:
        ref = publish('BLOCKED', 'remote_branch_exists')
        print('RESULT=BLOCKED:remote_branch_exists')
        print('RECEIPT_REF=' + ref)
        return 0

    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    wt = WORKTREE_ROOT / ('bootstrap-2832-' + next(tempfile._get_candidate_names()))
    added = False
    pushed = False
    try:
        run(['git', '-C', str(REPO), 'worktree', 'add', '--quiet', '-b', BRANCH, str(wt), TARGET_SHA])
        added = True

        runner_path = wt / 'scripts/runner_poll_github_tasks.py'
        source = runner_path.read_text(encoding='utf-8')
        if source.count(OLD_FUNCTION) != 1:
            raise BootstrapError('runner_patch_preimage_mismatch')
        runner_path.write_text(source.replace(OLD_FUNCTION, NEW_FUNCTION, 1), encoding='utf-8')

        test_path = wt / 'tests/test_runner_poll_github_tasks.py'
        tests = test_path.read_text(encoding='utf-8')
        if TEST_NAME in tests:
            raise BootstrapError('regression_test_already_present')
        test_path.write_text(tests.rstrip() + TEST_APPEND + '\n', encoding='utf-8')

        changed = sorted(run(['git', 'diff', '--name-only'], cwd=wt).stdout.splitlines())
        expected = ['scripts/runner_poll_github_tasks.py', 'tests/test_runner_poll_github_tasks.py']
        if changed != expected:
            raise BootstrapError('changed_files_outside_allowlist')

        test_env = safe_test_env()
        focused = run(
            ['python3', '-m', 'pytest', '-q', 'tests/test_runner_poll_github_tasks.py', 'tests/test_runner_child_environment_openrouter.py'],
            cwd=wt,
            env=test_env,
            check=False,
        )
        if focused.returncode != 0:
            ref = publish('BLOCKED', 'focused_tests_failed', FAILED_TESTS=failed_nodes(focused.stdout))
            print('RESULT=BLOCKED:focused_tests_failed')
            print('RECEIPT_REF=' + ref)
            return 0

        full = run(['python3', '-m', 'pytest', '-q'], cwd=wt, env=test_env, check=False)
        if full.returncode != 0:
            ref = publish('BLOCKED', 'full_tests_failed', FAILED_TESTS=failed_nodes(full.stdout))
            print('RESULT=BLOCKED:full_tests_failed')
            print('RECEIPT_REF=' + ref)
            return 0

        if run(['python3', '-m', 'py_compile', 'scripts/runner_poll_github_tasks.py'], cwd=wt, env=test_env, check=False).returncode != 0:
            raise BootstrapError('py_compile_failed')
        if run(['git', 'diff', '--check'], cwd=wt, check=False).returncode != 0:
            raise BootstrapError('diff_check_failed')

        run(['git', 'add', '--', 'scripts/runner_poll_github_tasks.py', 'tests/test_runner_poll_github_tasks.py'], cwd=wt)
        run(['git', 'commit', '-m', 'Isolate validation subprocesses from codegen secret authority (#2832)'], cwd=wt)
        head = run(['git', 'rev-parse', 'HEAD'], cwd=wt).stdout.strip()
        run(['git', 'push', '--set-upstream', 'origin', BRANCH], cwd=wt, timeout=180)
        pushed = True

        body = '\n'.join([
            'Fixes #2832 on exact main.',
            '',
            '- Validation subprocesses still use the canonical sanitizer.',
            '- Validation explicitly supplies no codegen authority, so no recovery/fallback/Bitwarden binding can be installed.',
            '- Codegen execution behavior is unchanged.',
            '- Adds a regression proving provider/fallback/credential env names are stripped while safe values survive.',
            '',
            f'Base: `{TARGET_SHA}`',
            f'Head: `{head}`',
            '',
            'Validation: focused Runner + OpenRouter wrapper tests PASS; full pytest PASS; py_compile PASS; git diff --check PASS.',
            '',
            '`scripts/runner_poll_github_tasks.py` is protected; exact-head operator approval is required before merge.',
        ])
        pr = run([
            'gh', 'pr', 'create', '--repo', REPO_FULL, '--base', 'main', '--head', BRANCH,
            '--draft', '--title', 'P0 isolate Runner validation from codegen secret authority', '--body', body,
        ], cwd=wt, timeout=90).stdout.strip()
        ref = publish(
            'PR_READY',
            'validation_secret_boundary_fixed',
            HEAD_SHA=head,
            CHANGED_FILES='scripts/runner_poll_github_tasks.py;tests/test_runner_poll_github_tasks.py',
            FOCUSED_TESTS='PASS',
            FULL_TESTS='PASS',
            PY_COMPILE='PASS',
            DIFF_CHECK='PASS',
            PR=pr,
        )
        print('RESULT=PR_READY')
        print('HEAD_SHA=' + head)
        print('PR_URL=' + pr)
        print('RECEIPT_REF=' + ref)
        return 0
    except BootstrapError as exc:
        ref = publish('BLOCKED', str(exc))
        print('RESULT=BLOCKED:' + str(exc))
        print('RECEIPT_REF=' + ref)
        return 0
    except Exception as exc:
        ref = publish('BLOCKED', type(exc).__name__)
        print('RESULT=BLOCKED:' + type(exc).__name__)
        print('RECEIPT_REF=' + ref)
        return 0
    finally:
        if added:
            run(['git', '-C', str(REPO), 'worktree', 'remove', '--force', str(wt)], check=False, timeout=60)
        if not pushed:
            run(['git', '-C', str(REPO), 'branch', '-D', BRANCH], check=False, timeout=30)


if __name__ == '__main__':
    raise SystemExit(main())

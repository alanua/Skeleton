#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
REPO_FULL = 'alanua/Skeleton'
ISSUE = '2808'
TARGET_SHA = '45c155a14afdf4d34dbb07539cdff02807c3dcdb'
BRANCH = 'runner/repair-zero-depth-runnow-current-main-v3'
WORKTREE_ROOT = Path('/home/agent/agent-dev/worktrees/skeleton')

OLD_FUNCTION = '''def _autonomous_queue_eligible_snapshot() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    ready_issues = get_ready_issues()
    running_issues = get_running_issues()
    if ready_issues or running_issues:
        return ready_issues, running_issues, [], []

    run_now_candidates = [
        issue
        for issue in get_run_now_queue_intake_candidate_issues()
        if LABEL_RUN_NOW in _issue_label_names(issue)
    ]
    candidate_issues = (
        run_now_candidates
        if run_now_candidates
        else get_queue_replenisher_candidate_issues()
    )
    if any(
        LABEL_RUNNING in _issue_label_names(issue)
        and LABEL_AGENT_TASK in _issue_label_names(issue)
        and not (_issue_label_names(issue) & TERMINAL_RUNNER_LABELS)
        for issue in candidate_issues
    ):
        return ready_issues, candidate_issues, candidate_issues, []
    if run_now_candidates:
        eligible = select_run_now_queue_intake_targets(ready_issues, candidate_issues)
    else:
        eligible = select_runner_queue_replenishment_targets(
            ready_issues, candidate_issues
        )
    return ready_issues, running_issues, candidate_issues, eligible
'''

NEW_FUNCTION = '''def _autonomous_queue_eligible_snapshot() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    ready_issues = get_ready_issues()
    running_issues = get_running_issues()
    if ready_issues or running_issues:
        return ready_issues, running_issues, [], []

    run_now_candidates = [
        issue
        for issue in get_run_now_queue_intake_candidate_issues()
        if LABEL_RUN_NOW in _issue_label_names(issue)
    ]
    if run_now_candidates:
        if any(
            LABEL_RUNNING in _issue_label_names(issue)
            and LABEL_AGENT_TASK in _issue_label_names(issue)
            and not (_issue_label_names(issue) & TERMINAL_RUNNER_LABELS)
            for issue in run_now_candidates
        ):
            return ready_issues, run_now_candidates, run_now_candidates, []
        run_now_eligible = select_run_now_queue_intake_targets(
            ready_issues, run_now_candidates
        )
        if run_now_eligible:
            return ready_issues, running_issues, run_now_candidates, run_now_eligible

    candidate_issues = get_queue_replenisher_candidate_issues()
    if any(
        LABEL_RUNNING in _issue_label_names(issue)
        and LABEL_AGENT_TASK in _issue_label_names(issue)
        and not (_issue_label_names(issue) & TERMINAL_RUNNER_LABELS)
        for issue in candidate_issues
    ):
        return ready_issues, candidate_issues, candidate_issues, []
    eligible = select_runner_queue_replenishment_targets(
        ready_issues, candidate_issues
    )
    return ready_issues, running_issues, candidate_issues, eligible
'''

TEST_MARKER = 'def test_malformed_or_terminal_run_now_does_not_block_valid_candidate() -> None:\n'
NEW_TEST = '''def test_ineligible_run_now_pool_falls_back_to_general_replenisher_candidates() -> None:
    waiting_run_now = _queue_candidate_issue(
        2517,
        allowed_files=("docs/waiting-run-now-fallback.md",),
        idempotency_key="waiting-run-now-fallback",
        labels=(
            runner.LABEL_AGENT_TASK,
            runner.LABEL_RUN_NOW,
            runner.LABEL_WAITING_DEPENDENCY,
        ),
    )
    general_candidate = _queue_candidate_issue(
        2518,
        allowed_files=("docs/general-fallback.md",),
        idempotency_key="general-fallback",
        labels=(runner.LABEL_AGENT_TASK,),
    )

    with mock.patch.object(runner, "get_ready_issues", return_value=[]), mock.patch.object(
        runner, "get_running_issues", return_value=[]
    ), mock.patch.object(
        runner,
        "get_run_now_queue_intake_candidate_issues",
        return_value=[waiting_run_now],
    ), mock.patch.object(
        runner,
        "get_queue_replenisher_candidate_issues",
        return_value=[general_candidate],
    ):
        ready, running, candidates, eligible = runner._autonomous_queue_eligible_snapshot()

    assert ready == []
    assert running == []
    assert [issue["number"] for issue in candidates] == [2518]
    assert [issue["number"] for issue in eligible] == [2518]


'''

class BootstrapError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path | None = None, timeout: int = 1800, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise BootstrapError(f'command_failed:{Path(argv[0]).name}')
    return proc


def publish(status: str, reason: str, **fields: str) -> str:
    lines = [
        '### #2808 deterministic current-main bootstrap receipt',
        '',
        '```text',
        f'STATUS={status}',
        f'REASON={reason}',
        f'BASE={TARGET_SHA}',
    ]
    for key, value in fields.items():
        safe = str(value).replace('\n', ' ')[:300]
        lines.append(f'{key}={safe}')
    lines.append('```')
    body = '\n'.join(lines)
    proc = run(['gh', 'issue', 'comment', ISSUE, '--repo', REPO_FULL, '--body', body], check=False, timeout=60)
    return proc.stdout.strip() if proc.returncode == 0 else 'NOT_PUBLISHED'


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
    origin_main = run(['git', '-C', str(REPO), 'rev-parse', 'origin/main']).stdout.strip()
    if origin_main != TARGET_SHA:
        publish('BLOCKED', 'main_moved', CURRENT_MAIN=origin_main)
        print('RESULT=BLOCKED:main_moved')
        return 0

    if run(['git', '-C', str(REPO), 'show-ref', '--verify', f'refs/heads/{BRANCH}'], check=False).returncode == 0:
        publish('BLOCKED', 'local_branch_exists')
        print('RESULT=BLOCKED:local_branch_exists')
        return 0
    if run(['git', '-C', str(REPO), 'ls-remote', '--exit-code', '--heads', 'origin', BRANCH], check=False).returncode == 0:
        publish('BLOCKED', 'remote_branch_exists')
        print('RESULT=BLOCKED:remote_branch_exists')
        return 0

    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    wt = WORKTREE_ROOT / next(tempfile._get_candidate_names())
    added = False
    try:
        run(['git', '-C', str(REPO), 'worktree', 'add', '--quiet', '-b', BRANCH, str(wt), TARGET_SHA])
        added = True

        runner_path = wt / 'scripts/runner_poll_github_tasks.py'
        text = runner_path.read_text(encoding='utf-8')
        if text.count(OLD_FUNCTION) != 1:
            raise BootstrapError('runner_patch_preimage_mismatch')
        runner_path.write_text(text.replace(OLD_FUNCTION, NEW_FUNCTION, 1), encoding='utf-8')

        test_path = wt / 'tests/test_runner_poll_github_tasks.py'
        tests = test_path.read_text(encoding='utf-8')
        if 'test_ineligible_run_now_pool_falls_back_to_general_replenisher_candidates' in tests:
            raise BootstrapError('test_already_present')
        if tests.count(TEST_MARKER) != 1:
            raise BootstrapError('test_marker_mismatch')
        test_path.write_text(tests.replace(TEST_MARKER, NEW_TEST + TEST_MARKER, 1), encoding='utf-8')

        changed = run(['git', 'diff', '--name-only'], cwd=wt).stdout.splitlines()
        if sorted(changed) != ['scripts/runner_poll_github_tasks.py', 'tests/test_runner_poll_github_tasks.py']:
            raise BootstrapError('changed_files_outside_allowlist')

        focused = run(['python3', '-m', 'pytest', '-q', 'tests/test_runner_poll_github_tasks.py'], cwd=wt, check=False)
        if focused.returncode != 0:
            ref = publish('BLOCKED', 'focused_tests_failed', FAILED_TESTS=failed_nodes(focused.stdout))
            print('RESULT=BLOCKED:focused_tests_failed')
            print('RECEIPT_REF=' + ref)
            return 0

        full = run(['python3', '-m', 'pytest', '-q'], cwd=wt, check=False)
        if full.returncode != 0:
            ref = publish('BLOCKED', 'full_tests_failed', FAILED_TESTS=failed_nodes(full.stdout))
            print('RESULT=BLOCKED:full_tests_failed')
            print('RECEIPT_REF=' + ref)
            return 0

        if run(['python3', '-m', 'py_compile', 'scripts/runner_poll_github_tasks.py'], cwd=wt, check=False).returncode != 0:
            raise BootstrapError('py_compile_failed')
        if run(['git', 'diff', '--check'], cwd=wt, check=False).returncode != 0:
            raise BootstrapError('diff_check_failed')

        run(['git', 'add', 'scripts/runner_poll_github_tasks.py', 'tests/test_runner_poll_github_tasks.py'], cwd=wt)
        run(['git', 'commit', '-m', 'Fix zero-depth RUN_NOW replenishment starvation (#2808)'], cwd=wt)
        head = run(['git', 'rev-parse', 'HEAD'], cwd=wt).stdout.strip()
        run(['git', 'push', '--set-upstream', 'origin', BRANCH], cwd=wt, timeout=180)

        pr_body = '\n'.join([
            'Fixes the current #2808 zero-depth starvation root cause on exact main.',
            '',
            '- RUN_NOW keeps priority when it has an eligible candidate.',
            '- An all-ineligible RUN_NOW pool no longer hides normal PUBLIC_SAFE agent tasks.',
            '- Adds a focused regression for RUN_NOW-ineligible -> general replenisher fallback.',
            '- No runtime/provider/device mutation.',
            '',
            f'Base: `{TARGET_SHA}`',
            f'Head: `{head}`',
            '',
            'Validation: focused Runner poller pytest PASS; full pytest PASS; py_compile PASS; git diff --check PASS.',
            '',
            '`scripts/runner_poll_github_tasks.py` is protected; merge requires exact-head operator approval.',
        ])
        pr = run([
            'gh', 'pr', 'create', '--repo', REPO_FULL, '--base', 'main', '--head', BRANCH,
            '--draft', '--title', 'P0 fix zero-depth RUN_NOW replenishment starvation', '--body', pr_body,
        ], cwd=wt, timeout=90).stdout.strip()
        ref = publish(
            'PR_READY',
            'deterministic_patch_validated',
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


if __name__ == '__main__':
    raise SystemExit(main())

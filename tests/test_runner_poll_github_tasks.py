from __future__ import annotations

import csv
import json
import os
import re
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest

from scripts import runner_poll_github_tasks as runner
from scripts import telegram_callback_poller as callback_poller


ORIGINAL_GRAPHIFY_RESOLVE_USER_SCRIPT = runner._graphify_resolve_user_script
ORIGINAL_GRAPHIFY_RESOLVE_GRAPHIFY_CLI = runner._graphify_resolve_graphify_cli

HEAD_SHA = "a" * 40
PR_URL = "https://github.com/alanua/Skeleton/pull/123"
DONE_REPORT = f"""DONE: Codex completed successfully and produced file changes.

Changed files:
- scripts/runner_poll_github_tasks.py
- docs/TELEGRAM_APPROVAL_BUTTONS.md

Pytest output:
```
99 passed
```

Commit: {HEAD_SHA}
Draft PR: {PR_URL}"""
CALLBACK_HMAC_SECRET = "runner-callback-hmac-test-secret"
CALLBACK_DIGEST = runner.hmac.new(
    CALLBACK_HMAC_SECRET.encode("utf-8"),
    f"tpr1:approve:p123:{HEAD_SHA[:8]}".encode("ascii"),
    runner.hashlib.sha256,
).hexdigest()[:12]


def _merge_issue_body(
    *,
    pr_number: int = 123,
    head_sha: str = HEAD_SHA,
    action: str = "squash",
    approval_source: str = "signed_telegram_callback",
) -> str:
    return "\n".join(
        (
            f"Mode: {runner.TELEGRAM_APPROVED_PR_MERGE_MODE}",
            f"Repository: {runner.REPO}",
            f"Pull Request: {pr_number}",
            f"Approved Head SHA: {head_sha}",
            f"Merge Action: {action}",
            f"Approval Source: {approval_source}",
            f"Callback Digest: {CALLBACK_DIGEST}",
        )
    )


def _merge_comments() -> list[dict[str, str]]:
    return [
        {
            "body": (
                "Operator event record (Telegram callback stage 1)\n"
                f"Repository: {runner.REPO}\n"
                "Pull request: #123\n"
                "Action: telegram_approve\n"
                f"Head marker: {HEAD_SHA[:8]}\n"
                f"Callback digest: {CALLBACK_DIGEST}\n"
                "Result: recorded\n"
                "Verified approval record: signed_telegram_callback\n"
                f"Verified head SHA: {HEAD_SHA}\n"
            )
        }
    ]


def _merge_pr_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "headRefOid": HEAD_SHA,
        "comments": _merge_comments(),
    }
    state.update(updates)
    return state


def _inspect_pr_issue_body(
    *,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    repository: str | None = runner.REPO,
) -> str:
    lines = [
        f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
        f"Maintenance Task ID: {runner.INSPECT_PR_MERGEABILITY}",
        f"Repository: {repository}",
    ]
    if pr_number is not None:
        lines.append(f"Pull Request: {pr_number}")
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    return "\n".join(lines)


def _inspect_pr_state(**updates: object) -> dict[str, object]:
    pr: dict[str, object] = {
        "number": 123,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "base": {
            "ref": "main",
            "sha": "b" * 40,
            "repo": {"full_name": runner.REPO},
        },
        "head": {"ref": "runner/issue-123", "sha": HEAD_SHA},
    }
    pr.update(updates.pop("pr", {}))
    state: dict[str, object] = {
        "pr": pr,
        "files": [{"filename": "scripts/runner_poll_github_tasks.py"}],
        "compare": {"status": "ahead", "ahead_by": 1, "behind_by": 0},
        "combined_status": {
            "state": "success",
            "statuses": [{"context": "pytest", "state": "success"}],
        },
        "check_runs": [],
    }
    state.update(updates)
    return state


def _telegram_response() -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value = response
    return response


def _json_response(payload: object) -> mock.MagicMock:
    response = _telegram_response()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def _request_payload(urlopen: mock.MagicMock) -> dict[str, list[str]]:
    request = urlopen.call_args.args[0]
    return urllib.parse.parse_qs(request.data.decode("utf-8"))


def _plain_done_message(issue_number: int = 129) -> str:
    return runner.build_telegram_message(issue_number, "DONE", DONE_REPORT)


def _project_tree_with(
    project_id: str,
    *,
    repo: str,
    runner_enabled: bool,
    planning_only: bool,
    codex_issue_worktree: bool,
    live_cross_repo: bool,
) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"][project_id] = {
        "repo": repo,
        "checkout_path": f"/home/agent/agent-dev/worktrees/{project_id}/main",
        "worktree_root": f"/home/agent/agent-dev/worktrees/{project_id}",
        "public": True,
        "runner_enabled": runner_enabled,
        "execution_modes": {
            "planning_only": planning_only,
            "codex_issue_worktree": codex_issue_worktree,
            "live_cross_repo": live_cross_repo,
        },
        "requires_explicit_approval_for_mode_change": True,
        "future_parallel_worktrees": False,
        "runtime_approval_required": True,
        "worktree_name_prefix": project_id.replace("_", "-"),
        "description": "Test project.",
    }
    return project_tree


def _project_tree_with_lavalamp_checkout(
    checkout_path: Path,
    worktree_root: Path,
    *,
    planning_only: bool = False,
    codex_issue_worktree: bool = True,
) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"]["lavalamp"]["checkout_path"] = str(checkout_path)
    project_tree["projects"]["lavalamp"]["worktree_root"] = str(worktree_root)
    project_tree["projects"]["lavalamp"]["execution_modes"] = {
        "planning_only": planning_only,
        "codex_issue_worktree": codex_issue_worktree,
        "live_cross_repo": False,
    }
    return project_tree


def test_blocked_output_classifier_detects_runner_blockers() -> None:
    cases = {
        "BLOCKED: cannot continue": "BLOCKED",
        "Blocked: waiting for access": "BLOCKED",
        "missing capability: docker": "missing capability",
        "wrong worktree selected": "wrong worktree",
        "not target repo": "not target repo",
        "writer unavailable": "writer unavailable",
        "cancelled by operator": "cancelled",
        "no build files were present": "no build files",
        "PlatformIO not available": "PlatformIO not available",
        "no firmware target exists": "no firmware",
        "assigned worktree is not target": "assigned worktree is not target",
    }

    for output, expected_marker in cases.items():
        assert runner.blocked_output_marker(output) == expected_marker


def test_blocked_output_classifier_ignores_echoed_transcript_markers() -> None:
    output = """Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py

Test count: 416 passed, 3 skipped
Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Goal text mentions BLOCKED and runner:blocked in echoed instructions.
exec
LABEL_BLOCKED = "runner:blocked"
"""

    assert runner.final_codex_answer(output).startswith("Changed files:")
    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_ignores_issue_438_transcript_tail() -> None:
    output = """Implemented the issue #421 two-file runner classifier patch.

Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py

Test results:
- 126 passed
Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Task text mentions BLOCKED and runner:blocked in the echoed transcript.
"""

    final_answer = runner.final_codex_answer(output)

    assert final_answer.startswith("Implemented the issue #421")
    assert "Reading additional input from stdin" not in final_answer
    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_uses_final_done_deliverable_over_echoed_prompt() -> None:
    output = """Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Task instructions:
- A real final deliverable beginning with BLOCKED must still be classified as blocked.
- Do not weaken safety for true blocked reports.
--------
assistant
DONE: Codex completed successfully and produced file changes.

Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py
- docs/RUNNER_MAINTENANCE_TASKS.md

Pytest output:
```
python3 -m pytest -q tests/test_runner_poll_github_tasks.py
115 passed

python3 -m pytest -q
503 passed, 3 skipped
```

No packages were installed during tests and no generic package-install capability was added.
"""

    assert runner.final_codex_answer(output).startswith("DONE:")
    assert runner.blocked_output_marker(output) is None


def test_codex_task_result_uses_done_final_report_over_echoed_prompt() -> None:
    blocked = "BLOCK" + "ED"
    output = f"""DONE: Codex completed successfully and produced file changes.

Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py

Pytest output:
```
2 passed
```

Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Task instructions mention {blocked} as an allowed return status.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("DONE")


def test_codex_task_result_trusts_leading_done_before_later_status_echo() -> None:
    blocked = "BLOCK" + "ED"
    output = f"""DONE: Codex completed successfully with no file changes.

Copied acceptance criteria:
{blocked}: use this only when the task cannot be completed.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("DONE")


def test_codex_task_result_accepts_result_done_with_stop_state_evidence() -> None:
    output = """RESULT: DONE

Changed files:
- scripts/runner_poll_github_tasks.py
- docs/RUNNER_QUEUE_STATUS.md

Report notes:
The maintenance docs mention runner-stopped, BLOCKED, and NEEDS_OPERATOR as
evidence text for recovery handling. Those words are not the final result.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("DONE")


def test_codex_task_result_ignores_plain_decision_words_in_success_output() -> None:
    output = """Worker completed the requested parser repair.

Decision words referenced by the task:
DONE
BLOCKED
NEEDS_OPERATOR

Those words are plain text examples, not the worker result.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert runner.final_codex_answer(output).startswith("Worker completed")
    assert runner.blocked_output_marker(output) is None
    assert result == runner.CodexTaskResult("DONE")


def test_codex_task_result_uses_assistant_result_over_prompt_echo() -> None:
    output = """Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Return RESULT: NEEDS_OPERATOR if the task cannot proceed.
--------
assistant
RESULT: DONE

Docs mention runner-stopped and BLOCKED as evidence text.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("DONE")


def test_codex_task_result_blocks_structured_needs_operator_result() -> None:
    output = """Changed files:
- none

The task cannot proceed because the source worktree is missing.

RESULT: NEEDS_OPERATOR
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("BLOCKED", "NEEDS_OPERATOR")


def test_codex_task_result_blocks_failure_final_report() -> None:
    blocked = "BLOCK" + "ED"
    output = f"""{blocked}: missing capability

Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Echoed task text later says DONE is also possible.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("BLOCKED", "BLOCKED")


def test_codex_task_result_ignores_prompt_echo_before_assistant_done() -> None:
    blocked = "BLOCK" + "ED"
    output = f"""Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Return {blocked} if the task cannot be completed.
--------
assistant
DONE: Codex completed successfully with no file changes.
"""

    result = runner.classify_codex_task_result(output, 0)

    assert result == runner.CodexTaskResult("DONE")


def test_codex_task_result_preserves_nonzero_exit_failure() -> None:
    output = "DONE: Codex completed successfully with no file changes."

    result = runner.classify_codex_task_result(output, 2)

    assert result == runner.CodexTaskResult("BLOCKED", "exit code 2")



def test_final_codex_answer_prefers_useful_prefix_before_transcript_tail() -> None:
    marker = runner._BLOCKED_OUTPUT_MARKERS[0]
    output = (
        "DONE: useful work completed\n\n"
        "Changed files:\n"
        "- scripts/runner_poll_github_tasks.py\n\n"
        "Reading additional input from stdin...\n"
        "OpenAI Codex v0.125.0\n"
        "--------\n"
        "user\n"
        f"Echoed task text includes {marker} but it is not the final answer.\n"
    )

    final_answer = runner.final_codex_answer(output)

    assert final_answer.startswith("DONE: useful work completed")
    assert "Reading additional input from stdin" not in final_answer
    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_ignores_echoed_prompt_inside_codex_output_block() -> None:
    output = """DONE: Codex completed successfully with no file changes.

Codex output:
```
Task instructions:
- A real final deliverable beginning with BLOCKED must still be classified as blocked.
- BLOCKED: example text from the original prompt.
```
"""

    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_keeps_real_final_marker_detection() -> None:
    output = """BLOCKED: missing capability

Reading additional input from stdin...
OpenAI Codex v0.125.0
"""

    assert runner.blocked_output_marker(output) == "BLOCKED"


def test_runner_report_status_blocks_file_change_done_without_draft_pr() -> None:
    report = DONE_REPORT.replace(f"\nDraft PR: {PR_URL}", "")

    assert runner.runner_report_status(report) == "BLOCKED"


def test_runner_report_status_blocks_file_change_done_with_placeholder_pr_url() -> None:
    report = DONE_REPORT.replace(PR_URL, "{PR_URL}")

    assert runner.extract_pr_url(report) is None
    assert runner.runner_report_status(report) == "BLOCKED"


def test_blocked_final_report_replaces_placeholder_pr_url() -> None:
    report = runner.blocked_final_report(DONE_REPORT.replace(PR_URL, "{PR_URL}"))

    assert "{PR_URL}" not in report
    assert "Draft PR: none" in report


def test_runner_report_status_allows_no_change_done_without_draft_pr() -> None:
    report = "DONE: Codex completed successfully with no file changes."

    assert runner.runner_report_status(report) == "DONE"


def test_runner_report_status_ignores_blocked_words_in_success_report_text() -> None:
    report = """DONE: Codex completed successfully with no file changes.

Docs updated:
- docs/RUNNER_MAINTENANCE_TASKS.md

The docs describe runner-stopped, BLOCKED, and NEEDS_OPERATOR states as
maintenance evidence text. They are not the final delivery status.
"""

    assert runner.runner_report_status(report) == "DONE"


def test_runner_report_status_blocks_done_report_with_publish_failure() -> None:
    report = f"""DONE: Codex completed successfully and produced file changes.

Changed files:
- scripts/runner_poll_github_tasks.py

Pytest output:
```
1 passed
```

BLOCKED: gh pr create failed.
Draft PR: {PR_URL}
"""

    assert runner.runner_report_status(report) == "BLOCKED"


def test_runner_report_status_blocks_operator_required_action() -> None:
    report = """DONE: Codex completed successfully with no file changes.

Changed files:
- docs/example.md

NEEDS_OPERATOR: approve publish retry.
"""

    assert runner.runner_report_status(report) == "BLOCKED"


def test_runner_report_status_allows_local_worktree_quote_of_old_blocked() -> None:
    report = f"""{runner._LOCAL_WORKTREE_DONE_PREFIX}

{runner._LOCAL_WORKTREE_BOUNDED_FINALIZATION_EVIDENCE}
Selected Project: demo
Selected Repository: alanua/Demo
Issue worktree: `/tmp/worktree`
Target-repo output: not created.

Local worktree changed files:
- docs/example.md

Local worktree git diff: none

Codex output:
```
Earlier issue state:
BLOCKED: waiting on operator.

DONE: local changes completed.
```
"""

    assert runner.runner_report_status(report) == "DONE"


def test_runner_report_status_blocks_local_worktree_without_bounded_evidence() -> None:
    report = f"""{runner._LOCAL_WORKTREE_DONE_PREFIX}

Selected Project: demo
Selected Repository: alanua/Demo
Issue worktree: `/tmp/worktree`
Target-repo output: not created.

Local worktree changed files:
- docs/example.md

Local worktree git diff: none

Codex output:
```
DONE: local changes completed.
```
"""

    assert runner.runner_report_status(report) == "BLOCKED"


def test_worktree_path_uses_env_root_when_set(tmp_path: Path) -> None:
    configured_root = tmp_path / "runner-worktrees"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(configured_root)}, clear=True
    ):
        assert runner.issue_worktree_path(139) == configured_root / "issue-139"


def test_worktree_path_falls_back_to_default_root() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        assert runner.worktree_root() == runner.DEFAULT_WORKTREE_ROOT
        assert runner.issue_worktree_path(139) == runner.DEFAULT_WORKTREE_ROOT / "issue-139"


def test_worktree_path_includes_issue_number(tmp_path: Path) -> None:
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ):
        assert runner.issue_worktree_path(912).name == "issue-912"


def test_post_issue_comment_replaces_placeholder_pr_url() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run:
        runner.post_issue_comment(9, "DONE\nDraft PR: {PR_URL}\nPR: {PR_URL}")

    command = run.call_args.args[0]
    body = command[command.index("--body") + 1]
    assert "{PR_URL}" not in body
    assert "Draft PR: none" in body
    assert "PR: none" in body


def test_target_repository_worktree_paths_are_deterministic(tmp_path: Path) -> None:
    skeleton_root = tmp_path / "skeleton"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(skeleton_root)}, clear=True
    ):
        bauclock_path = runner.target_repository_issue_worktree_path(
            "alanua/bauclock", 912
        )

        assert runner.target_repository_worktree_root("alanua/Skeleton") == skeleton_root
        assert runner.target_repository_worktree_root("alanua/bauclock") == (
            Path("/home/agent/agent-dev/worktrees/bauclock")
        )
        assert runner.target_repository_worktree_root("alanua/Lavalamp") == (
            Path("/home/agent/agent-dev/worktrees/lavalamp")
        )
        assert runner.target_repository_checkout_path("alanua/bauclock") == (
            Path("/home/agent/agent-dev/worktrees/bauclock/main")
        )
        assert runner.target_repository_checkout_path("alanua/Lavalamp") == (
            Path("/home/agent/agent-dev/repos/Lavalamp")
        )
        assert bauclock_path == (
            Path("/home/agent/agent-dev/worktrees/bauclock") / "issue-912"
        )
        assert bauclock_path == runner.target_repository_issue_worktree_path(
            "alanua/bauclock", 912
        )


def test_unknown_target_repository_worktree_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        runner.target_repository_worktree_root("alanua/unknown")


def test_target_repository_worktree_path_rejects_other_repository_root(
    tmp_path: Path,
) -> None:
    skeleton_root = tmp_path / "skeleton"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(skeleton_root)}, clear=True
    ), pytest.raises(ValueError, match="outside configured root"):
        runner.ensure_safe_target_repository_worktree_path(
            "alanua/bauclock", Path("/home/agent/agent-dev/worktrees/skeleton/issue-912")
        )


def test_target_repository_registered_worktree_root_outside_runner_bases_is_rejected() -> None:
    project_tree = _project_tree_with_lavalamp_checkout(
        _safe_checkout_path("lavalamp-safe-main"),
        runner.RUNNER_PROJECT_CHECKOUT_BASE / "other" / "lavalamp",
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), pytest.raises(ValueError, match="outside allowed Runner bases"):
        runner.target_repository_worktree_root("alanua/Lavalamp")


def test_target_repository_registered_checkout_outside_runner_bases_is_rejected() -> None:
    project_tree = _project_tree_with_lavalamp_checkout(
        runner.RUNNER_PROJECT_CHECKOUT_BASE / "other" / "lavalamp-main",
        _safe_checkout_path("lavalamp-safe-root"),
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), pytest.raises(ValueError, match="outside allowed Runner bases"):
        runner.target_repository_checkout_path("alanua/Lavalamp")


def test_unsafe_worktree_paths_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runner-worktrees"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(root)}, clear=True
    ):
        for unsafe_path in (root, tmp_path / "issue-139"):
            try:
                runner.ensure_safe_worktree_path(unsafe_path)
            except ValueError:
                continue
            raise AssertionError(f"unsafe worktree path was accepted: {unsafe_path}")


def test_prepare_issue_worktree_clones_workspace_with_writable_gitdir(
    tmp_path: Path,
) -> None:
    worktree_root = tmp_path / "worktrees"
    coordinator = tmp_path / "coordinator"
    coordinator.mkdir()

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(worktree_root)}, clear=True
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=(
            (0, "https://github.com/alanua/Skeleton.git"),
            (0, "fetched"),
            (0, "cloned"),
            (0, ""),
            (0, "fetched clone"),
            (0, "checked out"),
        ),
    ) as run_command:
        code, output, path = runner.prepare_issue_worktree(139, coordinator)

    assert code == 0
    assert "git clone --local --no-hardlinks --no-checkout" in output
    assert "git remote set-url origin <coordinator-origin>" in output
    assert "git checkout -B runner/issue-139 origin/main" in output
    assert path == (worktree_root / "issue-139").resolve()
    assert run_command.call_args_list == [
        mock.call(["git", "remote", "get-url", "origin"], cwd=coordinator),
        mock.call(["git", "fetch", "origin"], cwd=coordinator),
        mock.call(
            [
                "git",
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                str(coordinator.resolve()),
                str(path),
            ],
            cwd=coordinator,
        ),
        mock.call(
            [
                "git",
                "remote",
                "set-url",
                "origin",
                "https://github.com/alanua/Skeleton.git",
            ],
            cwd=path,
        ),
        mock.call(["git", "fetch", "origin"], cwd=path),
        mock.call(
            ["git", "checkout", "-B", "runner/issue-139", "origin/main"], cwd=path
        ),
    ]


def test_stale_dirty_worktree_blocks_instead_of_deleting(tmp_path: Path) -> None:
    worktree_path = tmp_path / "worktrees" / "issue-139"
    worktree_path.mkdir(parents=True)

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path / "worktrees")}, clear=True
    ), mock.patch.object(
        runner, "run_command", return_value=(0, " M scripts/runner_poll_github_tasks.py")
    ) as run_command:
        code, output, path = runner.prepare_issue_worktree(139, tmp_path / "coordinator")

    assert code != 0
    assert path == worktree_path.resolve()
    assert "dirty" in output
    assert "cleanup" in output
    run_command.assert_called_once_with(["git", "status", "--short"], cwd=path)


def test_cleanup_issue_worktree_refuses_path_outside_configured_root(
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "worktrees"
    outside_path = tmp_path / "issue-139"

    with mock.patch.object(
        runner, "issue_worktree_path", return_value=outside_path
    ), mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(configured_root)}, clear=True
    ), mock.patch.object(runner, "run_command") as run_command:
        code, output = runner.cleanup_issue_worktree(139, tmp_path / "coordinator")

    assert code != 0
    assert "outside configured root" in output
    run_command.assert_not_called()


def test_process_issue_runs_codex_in_prepared_issue_worktree(tmp_path: Path) -> None:
    coordinator = tmp_path / "coordinator"
    issue_path = tmp_path / "worktrees" / "issue-139"
    issue = {
        "number": 139,
        "title": "Worktree stage",
        "body": "Expected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "prepare_issue_worktree", return_value=(0, "ready", issue_path)
    ) as prepare, mock.patch.object(
        runner, "ensure_clean_worktree", side_effect=AssertionError("coordinator checked")
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ) as finalize, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree"
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    prepare.assert_called_once_with(139, str(coordinator))
    run_codex.assert_called_once_with(
        "Do it", str(issue_path), runner.RunnerTask(content="Do it")
    )
    finalize.assert_called_once_with(issue, str(issue_path), "codex output")


def _shadow_code_issue_body() -> str:
    return "\n".join(
        (
            f"Base SHA: {'b' * 40}",
            "Allowed Files:",
            "- tests/test_runner_poll_github_tasks.py",
            "Approval Reference: operator-approval-1683",
            "Idempotency Key: issue-1683-poller-shadow",
            "Validation Timeout Seconds: 900",
            "Privacy Boundary: PUBLIC_SAFE_REPOSITORY_ONLY",
            "Expected Output: draft PR",
            "",
            "```task",
            "Do it without leaking PRIVATE_TOKEN=/home/operator/private",
            "```",
        )
    )


def _shadow_maintenance_issue_body() -> str:
    return "\n".join(
        (
            f"Base SHA: {'b' * 40}",
            "Allowed Files: scripts/runner_poll_github_tasks.py",
            "Approval Reference: operator-approval-1683",
            "Idempotency Key: issue-1683-maintenance-shadow",
            "Validation Timeout Seconds: 900",
            "Privacy Boundary: PUBLIC_SAFE_REPOSITORY_ONLY",
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.CHECK_SKELETON_FRESHNESS}",
        )
    )


def _run_code_generation_process_issue_for_shadow(
    tmp_path: Path,
    *,
    shadow_mode: str,
    evaluator_exception: bool = False,
) -> dict[str, object]:
    coordinator = tmp_path / "coordinator"
    issue_path = tmp_path / "worktrees" / "issue-1683"
    issue = {
        "number": 1683,
        "title": "Shadow parity",
        "body": _shadow_code_issue_body(),
        "comments": [],
    }
    final_report = "DONE: Codex completed successfully with no file changes."
    evaluator_patch = (
        mock.patch.object(
            runner,
            "evaluate_shadow_from_normalized_metadata",
            side_effect=RuntimeError("shadow failed"),
        )
        if evaluator_exception
        else mock.patch.object(
            runner,
            "evaluate_shadow_from_normalized_metadata",
            wraps=runner.evaluate_shadow_from_normalized_metadata,
        )
    )

    with mock.patch.dict(
        os.environ, {runner.RUNNER_SHADOW_MODE_ENV: shadow_mode}, clear=False
    ), evaluator_patch, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_worktree", return_value=(0, "ready", issue_path)
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success", return_value=final_report
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as post_comment, mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(0, "")
    ), mock.patch.object(
        runner, "record_runner_task_picked_up", return_value=None
    ), mock.patch.object(
        runner, "record_runner_executor_result", return_value=None
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    return {
        "set_label": set_label.call_args_list,
        "post_comment": post_comment.call_args_list,
        "notify": notify.call_args_list,
        "run_codex": run_codex.call_args_list,
        "shadow_receipt": runner.LAST_RUNNER_SHADOW_RECEIPT,
    }


def _run_maintenance_process_issue_for_shadow(
    tmp_path: Path,
    *,
    shadow_mode: str,
    evaluator_exception: bool = False,
) -> dict[str, object]:
    coordinator = tmp_path / "coordinator"
    issue = {
        "number": 1684,
        "title": "Maintenance shadow parity",
        "body": _shadow_maintenance_issue_body(),
        "comments": [],
    }
    evaluator_patch = (
        mock.patch.object(
            runner,
            "evaluate_shadow_from_normalized_metadata",
            side_effect=RuntimeError("shadow failed"),
        )
        if evaluator_exception
        else mock.patch.object(
            runner,
            "evaluate_shadow_from_normalized_metadata",
            wraps=runner.evaluate_shadow_from_normalized_metadata,
        )
    )

    with mock.patch.dict(
        os.environ, {runner.RUNNER_SHADOW_MODE_ENV: shadow_mode}, clear=False
    ), evaluator_patch, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner,
        "dispatch_runtime_maintenance_task",
        return_value=f"DONE: {runner.CHECK_SKELETON_FRESHNESS}",
    ) as dispatch, mock.patch.object(
        runner, "post_issue_comment"
    ) as post_comment, mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(
        runner, "record_runner_task_picked_up", return_value=None
    ), mock.patch.object(
        runner, "record_runner_executor_result", return_value=None
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    return {
        "set_label": set_label.call_args_list,
        "post_comment": post_comment.call_args_list,
        "notify": notify.call_args_list,
        "dispatch": dispatch.call_args_list,
        "shadow_receipt": runner.LAST_RUNNER_SHADOW_RECEIPT,
    }


@pytest.mark.parametrize(
    ("configured", "enabled"),
    (
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("   ", False),
        ("enabled", False),
        ("maybe", False),
        ("2", False),
    ),
)
def test_shadow_mode_env_uses_explicit_truthy_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    enabled: bool,
) -> None:
    monkeypatch.setenv(runner.RUNNER_SHADOW_MODE_ENV, configured)

    assert runner.runner_shadow_mode_enabled() is enabled


def test_shadow_evaluator_exception_leaves_legacy_code_generation_dispatch_byte_for_byte(
    tmp_path: Path,
) -> None:
    normal = _run_code_generation_process_issue_for_shadow(tmp_path, shadow_mode="0")
    exception = _run_code_generation_process_issue_for_shadow(
        tmp_path, shadow_mode="1", evaluator_exception=True
    )

    assert exception["set_label"] == normal["set_label"]
    assert exception["post_comment"] == normal["post_comment"]
    assert exception["notify"] == normal["notify"]
    assert exception["run_codex"] == normal["run_codex"]
    assert exception["shadow_receipt"] == {
        "schema": "skeleton.runner_shadow_receipt.v1",
        "shadow_status": "blocked",
        "semantic_route": None,
        "reason_codes": ["SHADOW_EVALUATOR_EXCEPTION"],
        "task_envelope_hash": None,
    }


def test_shadow_evaluator_exception_leaves_legacy_maintenance_dispatch_byte_for_byte(
    tmp_path: Path,
) -> None:
    normal = _run_maintenance_process_issue_for_shadow(tmp_path, shadow_mode="0")
    exception = _run_maintenance_process_issue_for_shadow(
        tmp_path, shadow_mode="1", evaluator_exception=True
    )

    assert exception["set_label"] == normal["set_label"]
    assert exception["post_comment"] == normal["post_comment"]
    assert exception["notify"] == normal["notify"]
    assert exception["dispatch"] == normal["dispatch"]


def test_shadow_mode_enabled_and_disabled_preserve_legacy_maintenance_behavior(
    tmp_path: Path,
) -> None:
    disabled = _run_maintenance_process_issue_for_shadow(tmp_path, shadow_mode="0")
    enabled = _run_maintenance_process_issue_for_shadow(tmp_path, shadow_mode="1")

    assert enabled["set_label"] == disabled["set_label"]
    assert enabled["post_comment"] == disabled["post_comment"]
    assert enabled["notify"] == disabled["notify"]
    assert enabled["dispatch"] == disabled["dispatch"]
    receipt = enabled["shadow_receipt"]
    assert isinstance(receipt, dict)
    assert set(receipt) == {
        "schema",
        "shadow_status",
        "semantic_route",
        "reason_codes",
        "task_envelope_hash",
    }


@pytest.mark.parametrize(
    "environment",
    (
        {},
        {runner.RUNNER_SHADOW_MODE_ENV: ""},
        {runner.RUNNER_SHADOW_MODE_ENV: "   "},
        {runner.RUNNER_SHADOW_MODE_ENV: "maybe"},
    ),
)
def test_absent_blank_and_unknown_shadow_env_skip_work_and_keep_legacy_dispatch(
    tmp_path: Path,
    environment: dict[str, str],
) -> None:
    coordinator = tmp_path / "coordinator"
    issue = {
        "number": 1684,
        "title": "Maintenance shadow disabled by default",
        "body": _shadow_maintenance_issue_body(),
        "comments": [],
    }

    with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
        runner,
        "normalized_runner_shadow_metadata",
        side_effect=AssertionError("shadow normalization must not run"),
    ) as normalize, mock.patch.object(
        runner,
        "evaluate_shadow_from_normalized_metadata",
        side_effect=AssertionError("shadow evaluator must not run"),
    ) as evaluate, mock.patch(
        "core.runner_shadow_integration.build_shadow_executor_registry",
        side_effect=AssertionError("shadow registry must not be built"),
    ) as registry, mock.patch(
        "core.runner_shadow_integration.RunnerGate",
        side_effect=AssertionError("RunnerGate must not be evaluated"),
    ) as runner_gate, mock.patch(
        "core.runner_gate.validate_action_request",
        side_effect=AssertionError("ActionGate must not be evaluated"),
    ) as action_gate, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner,
        "dispatch_runtime_maintenance_task",
        return_value=f"DONE: {runner.CHECK_SKELETON_FRESHNESS}",
    ) as dispatch, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "record_runner_task_picked_up", return_value=None
    ), mock.patch.object(
        runner, "record_runner_executor_result", return_value=None
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    assert runner.runner_shadow_mode_enabled() is False
    normalize.assert_not_called()
    evaluate.assert_not_called()
    registry.assert_not_called()
    runner_gate.assert_not_called()
    action_gate.assert_not_called()
    dispatch.assert_called_once_with(
        runner.CHECK_SKELETON_FRESHNESS,
        str(coordinator),
        _shadow_maintenance_issue_body(),
    )
    assert runner.LAST_RUNNER_SHADOW_RECEIPT == {
        "schema": "skeleton.runner_shadow_receipt.v1",
        "shadow_status": "not_applicable",
        "semantic_route": None,
        "reason_codes": ["SHADOW_MODE_DISABLED"],
        "task_envelope_hash": None,
    }


@pytest.mark.parametrize(
    "body",
    (
        "\n".join(
            (
                "Expected Output: draft PR",
                "",
                "```task",
                "base_sha: " + "b" * 40,
                "allowed_files:",
                "  - tests/test_runner_poll_github_tasks.py",
                "approval_reference: operator-approval-1683",
                "idempotency_key: typed-envelope-1683",
                "validation_timeout_seconds: 900",
                "```",
            )
        ),
        "\n".join(
            (
                "Expected Output: draft PR",
                "",
                "```task",
                "base_sha: " + "b" * 40,
                "allowed_files:",
                "  - tests/test_runner_poll_github_tasks.py",
                "approval_reference: operator-approval-1683",
                "idempotency_key: typed-envelope-1683",
                "validation_timeout_seconds: 900",
                "privacy_boundary: ''",
                "```",
            )
        ),
    ),
)
def test_missing_or_blank_shadow_privacy_boundary_blocks_without_default(
    body: str,
) -> None:
    with mock.patch.dict(
        os.environ, {runner.RUNNER_SHADOW_MODE_ENV: "1"}, clear=False
    ):
        receipt = runner.evaluate_runner_shadow_hook(
            issue_number=1683,
            issue_body=body,
            route=runner.ROUTE_CODE_GENERATION,
            maintenance_task_id=None,
            runner_task=runner.RunnerTask(content=runner.extract_task_block(body) or ""),
            merge_request=None,
            trusted_approval_references=("operator-approval-1683",),
        )

    assert receipt is not None
    public = receipt.to_public_mapping()
    assert public["shadow_status"] == "blocked"
    assert public["semantic_route"] == "code_edit"
    assert public["reason_codes"] == ["INVALID_PRIVACY_BOUNDARY"]


def test_typed_task_block_fields_feed_shadow_hash_without_receipt_leakage() -> None:
    body = "\n".join(
        (
            "Expected Output: draft PR",
            "",
            "```task",
            "base_sha: " + "b" * 40,
            "allowed_files:",
            "  - tests/test_runner_poll_github_tasks.py",
            "approval_reference: operator-approval-1683",
            "idempotency_key: typed-envelope-1683",
            "validation_timeout_seconds: 900",
            "privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY",
            "private_note: PRIVATE_TOKEN=/home/operator/private",
            "```",
        )
    )

    with mock.patch.dict(
        os.environ, {runner.RUNNER_SHADOW_MODE_ENV: "1"}, clear=False
    ):
        receipt = runner.evaluate_runner_shadow_hook(
            issue_number=1683,
            issue_body=body,
            route=runner.ROUTE_CODE_GENERATION,
            maintenance_task_id=None,
            runner_task=runner.RunnerTask(content=runner.extract_task_block(body) or ""),
            merge_request=None,
            trusted_approval_references=("operator-approval-1683",),
        )

    assert receipt is not None
    public = receipt.to_public_mapping()
    assert public["shadow_status"] == "allowed"
    assert public["semantic_route"] == "code_edit"
    assert public["task_envelope_hash"] is not None
    assert "PRIVATE_TOKEN" not in repr(public)
    assert "/home/operator" not in repr(public)


def test_poll_once_processes_issues_single_lane() -> None:
    issues = [{"number": 139}, {"number": 140}]
    with mock.patch.object(
        runner, "get_ready_issues", return_value=issues
    ), mock.patch.object(runner, "process_issue") as process_issue:
        count = runner.poll_once(workdir="/coordinator")

    assert count == 2
    assert process_issue.call_args_list == [
        mock.call(issues[0], workdir="/coordinator"),
        mock.call(issues[1], workdir="/coordinator"),
    ]


def test_runner_task_defaults_to_default_lane() -> None:
    task, reason = runner.extract_runner_task("```task\nDo it\n```")

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        lane=runner.DEFAULT_RUNNER_LANE,
    )


def test_runner_task_accepts_allowlisted_lane_name() -> None:
    task, reason = runner.extract_runner_task("Runner Lane: lane-1\n\n```task\nDo it\n```")

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        lane=runner.RunnerLane("lane-1"),
        has_lane_metadata=True,
    )


def test_runner_task_accepts_allowlisted_target_repository() -> None:
    task, reason = runner.extract_runner_task(
        "Target Repository: alanua/bauclock\n\n```task\nDo it\n```"
    )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="bauclock",
        target_repository="alanua/bauclock",
        has_target_repository_metadata=True,
    )
    assert runner.ALLOWED_TARGET_REPOSITORIES == frozenset(
        (
            "alanua/Skeleton",
            "alanua/bauclock",
            "alanua/Lavalamp",
            "alanua/LumenFlow",
        )
    )


def test_runner_task_resolves_target_repository_aliases_by_priority() -> None:
    task, reason = runner.extract_runner_task(
        "Target Repository: alanua/bauclock\n"
        "Selected Repository: alanua/Lavalamp\n"
        "Repo: alanua/Skeleton\n\n"
        "```task\nDo it\n```"
    )

    assert reason is None
    assert task is not None
    assert task.target_project == "bauclock"
    assert task.target_repository == "alanua/bauclock"
    assert task.has_target_repository_metadata is True

    task, reason = runner.extract_runner_task(
        "Selected Repository: alanua/Lavalamp\n"
        "Repo: alanua/bauclock\n\n"
        "```task\nDo it\n```"
    )

    assert reason is None
    assert task is not None
    assert task.target_project == "lavalamp"
    assert task.target_repository == "alanua/Lavalamp"
    assert task.has_target_repository_metadata is True

    task, reason = runner.extract_runner_task(
        "Repo: alanua/bauclock\n\n```task\nDo it\n```"
    )

    assert reason is None
    assert task is not None
    assert task.target_project == "bauclock"
    assert task.target_repository == "alanua/bauclock"
    assert task.has_target_repository_metadata is True


def test_runner_task_accepts_allowlisted_target_project() -> None:
    task, reason = runner.extract_runner_task(
        "Target Project: bauclock\n\n```task\nDo it\n```"
    )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="bauclock",
        has_target_project_metadata=True,
        target_repository="alanua/bauclock",
    )


def test_codex_task_prompt_includes_selected_project_context() -> None:
    prompt = runner.build_codex_task_prompt(
        "Return selected project.",
        "/tmp/worktree",
        runner.RunnerTask(
            content="Return selected project.",
            target_project="bauclock",
            has_target_project_metadata=True,
            target_repository="alanua/bauclock",
        ),
    )

    assert "Selected Project: bauclock" in prompt
    assert "Selected Repository: alanua/bauclock" in prompt


def test_runner_task_accepts_matching_target_project_and_repository() -> None:
    task, reason = runner.extract_runner_task(
        "Target Project: lavalamp\n"
        "Target Repository: alanua/Lavalamp\n\n"
        "```task\nDo it\n```"
    )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="lavalamp",
        has_target_project_metadata=True,
        target_repository="alanua/Lavalamp",
        has_target_repository_metadata=True,
    )


def test_runner_task_ignores_lane_text_inside_task_fence() -> None:
    task, reason = runner.extract_runner_task("```task\nLane: deploy\nKeep it as prose.\n```")

    assert reason is None
    assert task == runner.RunnerTask(
        content="Lane: deploy\nKeep it as prose.",
        lane=runner.DEFAULT_RUNNER_LANE,
    )


def test_runner_task_reports_missing_closing_task_fence() -> None:
    task, reason = runner.extract_runner_task("```task\nDo it\n")

    assert task is None
    assert reason is not None
    assert "missing_closing_task_fence" in reason


def test_task_fence_block_reason_detects_later_truncated_task_fence() -> None:
    reason = runner.task_fence_block_reason("```task\nDo it\n```\n\n```task\nMore\n")

    assert reason is not None
    assert "missing_closing_task_fence" in reason


def test_process_issue_blocks_non_allowlisted_runner_lane_before_claim() -> None:
    issue = {
        "number": 141,
        "title": "Lane metadata",
        "body": "Runner Lane: deploy\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Runner lane `deploy` is not allowlisted" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_missing_closing_task_fence_before_claim() -> None:
    issue = {
        "number": 151,
        "title": "Truncated task",
        "body": "```task\nDo it\n",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "missing_closing_task_fence" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_non_allowlisted_target_repository_before_claim() -> None:
    issue = {
        "number": 142,
        "title": "Target repository metadata",
        "body": "Target Repository: alanua/unknown\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Target repository `alanua/unknown` is not allowlisted" in (
        block.call_args.args[1]
    )
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_unknown_target_project_before_claim() -> None:
    issue = {
        "number": 144,
        "title": "Target project metadata",
        "body": "Target Project: unknown\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Target project `unknown` is not allowlisted" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_mismatched_target_project_and_repository_before_claim() -> None:
    issue = {
        "number": 145,
        "title": "Target project mismatch",
        "body": (
            "Target Project: bauclock\n"
            "Target Repository: alanua/Lavalamp\n\n"
            "```task\nDo it\n```"
        ),
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "resolve to different PROJECT_TREE entries" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_mismatched_target_project_and_repository_alias_before_claim() -> None:
    issue = {
        "number": 150,
        "title": "Target project alias mismatch",
        "body": (
            "Target Project: skeleton\n"
            "Selected Repository: alanua/bauclock\n\n"
            "```task\nDo it\n```"
        ),
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "resolve to different PROJECT_TREE entries" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_runs_target_project_bauclock_in_local_worktree(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "bauclock" / "issue-146"
    issue = {
        "number": 146,
        "title": "Target project bauclock",
        "body": "Target Project: bauclock\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(
        runner, "verify_target_repository_checkout", return_value=None
    ), mock.patch.object(
        runner,
        "prepare_target_repository_issue_worktree",
        return_value=(0, "ready", issue_path),
    ) as prepare_target, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success"
    ) as finalize_success, mock.patch.object(
        runner, "finalize_local_worktree_success", return_value="DONE local report"
    ) as finalize_local, mock.patch.object(
        runner, "cleanup_target_repository_issue_worktree", return_value=(0, "")
    ) as cleanup_target, mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ):
        runner.process_issue(issue)

    expected_task = runner.RunnerTask(
        content="Do it",
        target_project="bauclock",
        has_target_project_metadata=True,
        target_repository="alanua/bauclock",
    )
    prepare_branch.assert_not_called()
    prepare_target.assert_called_once_with("alanua/bauclock", 146)
    run_codex.assert_called_once_with("Do it", str(issue_path), expected_task)
    finalize_success.assert_not_called()
    finalize_local.assert_called_once_with(str(issue_path), "codex output", expected_task)
    cleanup_target.assert_called_once_with("alanua/bauclock", 146)
    assert comment.call_args.args[1] == "DONE local report\nTarget Project: bauclock"


def test_process_issue_runs_target_project_lavalamp_when_project_tree_enables_worktree(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "lavalamp" / "issue-147"
    issue = {
        "number": 147,
        "title": "Target project lavalamp",
        "body": "Target Project: lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "verify_target_repository_checkout", return_value=None
    ), mock.patch.object(
        runner,
        "prepare_target_repository_issue_worktree",
        return_value=(0, "ready", issue_path),
    ) as prepare_target, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_local_worktree_success", return_value="DONE local report"
    ), mock.patch.object(
        runner, "cleanup_target_repository_issue_worktree", return_value=(0, "")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ):
        runner.process_issue(issue)

    expected_task = runner.RunnerTask(
        content="Do it",
        target_project="lavalamp",
        has_target_project_metadata=True,
        target_repository="alanua/Lavalamp",
    )
    prepare_target.assert_called_once_with("alanua/Lavalamp", 147)
    run_codex.assert_called_once_with("Do it", str(issue_path), expected_task)


def test_process_issue_blocks_target_project_lavalamp_when_project_tree_disables_worktree() -> None:
    issue = {
        "number": 147,
        "title": "Target project lavalamp",
        "body": "Target Project: lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with_lavalamp_checkout(
        _safe_checkout_path("lavalamp-disabled"),
        _safe_checkout_path("lavalamp-disabled-root"),
        planning_only=True,
        codex_issue_worktree=False,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "planning-only" in block.call_args.args[1]
    assert block.call_args.kwargs["runner_task"] == runner.RunnerTask(
        content="Do it",
        target_project="lavalamp",
        has_target_project_metadata=True,
        target_repository="alanua/Lavalamp",
    )
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_missing_target_checkout_before_codex() -> None:
    checkout_path = _safe_checkout_path("lavalamp-missing-main")
    worktree_root = _safe_checkout_path("lavalamp-missing-worktrees")
    issue = {
        "number": 151,
        "title": "Missing target checkout",
        "body": "Target Repository: alanua/Lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with_lavalamp_checkout(checkout_path, worktree_root)

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "block_issue"
    ) as block, mock.patch.object(
        runner, "prepare_target_repository_issue_worktree"
    ) as prepare_target, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    assert "reason=checkout_path_missing" in block.call_args.args[1]
    assert "checkout_path=" not in block.call_args.args[1]
    assert str(checkout_path) not in block.call_args.args[1]
    assert "worktree_root=" not in block.call_args.args[1]
    assert str(worktree_root) not in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_target.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_non_git_target_checkout_before_codex() -> None:
    checkout_path = _safe_checkout_path("lavalamp-non-git-main")
    worktree_root = _safe_checkout_path("lavalamp-non-git-worktrees")
    issue = {
        "number": 152,
        "title": "Non-git target checkout",
        "body": "Target Repository: alanua/Lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with_lavalamp_checkout(checkout_path, worktree_root)

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "block_issue"
    ) as block, mock.patch.object(
        runner, "prepare_target_repository_issue_worktree"
    ) as prepare_target, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        path_exists.side_effect = lambda path: path == checkout_path
        runner.process_issue(issue)

    assert "reason=checkout_git_missing" in block.call_args.args[1]
    assert "checkout_path=" not in block.call_args.args[1]
    assert str(checkout_path) not in block.call_args.args[1]
    assert "worktree_root=" not in block.call_args.args[1]
    assert str(worktree_root) not in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_target.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_unsafe_target_worktree_root_before_claim() -> None:
    checkout_path = _safe_checkout_path("lavalamp-unsafe-root-main")
    unsafe_root = runner.RUNNER_PROJECT_CHECKOUT_BASE / "other" / "lavalamp-root"
    issue = {
        "number": 153,
        "title": "Unsafe target worktree root",
        "body": "Target Repository: alanua/Lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with_lavalamp_checkout(
        checkout_path,
        unsafe_root,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "block_issue"
    ) as block, mock.patch.object(
        runner, "prepare_target_repository_issue_worktree"
    ) as prepare_target, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    assert "reason=registered_target_path_invalid" in block.call_args.args[1]
    assert "worktree_root" not in block.call_args.args[1]
    assert str(unsafe_root) not in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_target.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_unsafe_target_checkout_path_before_claim() -> None:
    unsafe_checkout = runner.RUNNER_PROJECT_CHECKOUT_BASE / "other" / "lavalamp-main"
    issue = {
        "number": 154,
        "title": "Unsafe target checkout path",
        "body": "Target Repository: alanua/Lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with_lavalamp_checkout(
        unsafe_checkout,
        _safe_checkout_path("lavalamp-safe-worktrees"),
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "block_issue"
    ) as block, mock.patch.object(
        runner, "prepare_target_repository_issue_worktree"
    ) as prepare_target, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    assert "reason=registered_target_path_invalid" in block.call_args.args[1]
    assert "checkout_path" not in block.call_args.args[1]
    assert str(unsafe_checkout) not in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_target.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_runs_target_project_skeleton_normally(tmp_path: Path) -> None:
    coordinator = tmp_path / "repo"
    issue_path = tmp_path / "worktree"
    coordinator.mkdir()
    issue = {
        "number": 148,
        "title": "Target project skeleton",
        "body": "Target Project: skeleton\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", issue_path)
    ) as prepare, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(0, "")
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    prepare.assert_called_once_with(148, str(coordinator))
    run_codex.assert_called_once_with(
        "Do it",
        str(issue_path),
        runner.RunnerTask(
            content="Do it",
            target_project="skeleton",
            has_target_project_metadata=True,
            target_repository="alanua/Skeleton",
        ),
    )
    assert comment.call_args.args[1] == "DONE report\nTarget Project: skeleton"


def test_process_issue_runs_target_repository_lavalamp_in_registered_worktree(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "lavalamp" / "issue-143"
    issue = {
        "number": 143,
        "title": "Target repository routing",
        "body": "Target Repository: alanua/Lavalamp\nExpected Output: done\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(
        runner, "verify_target_repository_checkout", return_value=None
    ), mock.patch.object(
        runner,
        "prepare_target_repository_issue_worktree",
        return_value=(0, "ready", issue_path),
    ) as prepare_target, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_local_worktree_success", return_value="DONE local report"
    ), mock.patch.object(
        runner, "cleanup_target_repository_issue_worktree", return_value=(0, "")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ):
        runner.process_issue(issue)

    prepare_branch.assert_not_called()
    prepare_target.assert_called_once_with("alanua/Lavalamp", 143)
    run_codex.assert_called_once()


def test_process_issue_blocks_runner_disabled_project_before_codex() -> None:
    issue = {
        "number": 149,
        "title": "Disabled target project",
        "body": "Target Project: disabled_public\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with(
        "disabled_public",
        repo="alanua/Disabled",
        runner_enabled=False,
        planning_only=False,
        codex_issue_worktree=True,
        live_cross_repo=False,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Runner is disabled" in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_runs_non_skeleton_codex_worktree_mode_locally(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "codex-other" / "issue-150"
    issue = {
        "number": 150,
        "title": "Non-skeleton codex worktree",
        "body": "Target Project: codex_other\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with(
        "codex_other",
        repo="alanua/CodexOther",
        runner_enabled=True,
        planning_only=False,
        codex_issue_worktree=True,
        live_cross_repo=False,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(
        runner, "verify_target_repository_checkout", return_value=None
    ), mock.patch.object(
        runner,
        "prepare_target_repository_issue_worktree",
        return_value=(0, "ready", issue_path),
    ) as prepare_target, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_local_worktree_success", return_value="DONE local report"
    ) as finalize_local, mock.patch.object(
        runner, "cleanup_target_repository_issue_worktree", return_value=(0, "")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ):
        runner.process_issue(issue)

    prepare_branch.assert_not_called()
    prepare_target.assert_called_once_with("alanua/CodexOther", 150)
    run_codex.assert_called_once()
    finalize_local.assert_called_once()


def test_process_issue_blocks_live_cross_repo_mode_before_codex() -> None:
    issue = {
        "number": 151,
        "title": "Live cross repo",
        "body": "Target Project: live_other\nExpected Output: done\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with(
        "live_other",
        repo="alanua/LiveOther",
        runner_enabled=True,
        planning_only=False,
        codex_issue_worktree=False,
        live_cross_repo=True,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "requires a separate PR" in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_extracts_bounded_telegram_approved_merge_request() -> None:
    mode, request, reason = runner.extract_telegram_approved_pr_merge_request(
        _merge_issue_body()
    )

    assert mode is True
    assert reason is None
    assert request == runner.TelegramApprovedPrMergeRequest(
        pr_number=123,
        approved_head_sha=HEAD_SHA,
        callback_digest=CALLBACK_DIGEST,
    )


def test_callback_merge_request_smoke_uses_signed_callback_approval_record() -> None:
    callback_data = f"tpr1:approve:p123:{HEAD_SHA[:8]}:{CALLBACK_DIGEST}"
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        callback_poller.urllib.request,
        "urlopen",
        side_effect=(
            _json_response({"number": 123, "head": {"sha": HEAD_SHA}}),
            _json_response({"id": 88}),
            _json_response({"number": 910}),
        ),
    ) as urlopen:
        callback_result = callback_poller.handle_callback_query(
            {"id": "callback-query-1", "data": callback_data}
        )

    merge_issue_request = urlopen.call_args_list[-1].args[0]
    body = json.loads(merge_issue_request.data.decode("utf-8"))["body"]

    mode, request, reason = runner.extract_telegram_approved_pr_merge_request(body)

    assert mode is True
    assert reason is None
    assert request == runner.TelegramApprovedPrMergeRequest(
        pr_number=123,
        approved_head_sha=HEAD_SHA,
        callback_digest=CALLBACK_DIGEST,
    )

    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner,
        "get_pr_merge_state",
        return_value=_merge_pr_state(
            comments=[{"body": str(callback_result["comment"])}]
        ),
    ), mock.patch.object(runner, "run_command", return_value=(0, "merged")) as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert callback_result["runner_merge_request"] == "requested"
    assert report.startswith("DONE:")
    run.assert_called_once()


@pytest.mark.parametrize(
    ("body", "reason"),
    (
        (_merge_issue_body(action="merge"), "action must be squash"),
        (
            _merge_issue_body(approval_source="issue_body"),
            "source is not allowlisted",
        ),
        (_merge_issue_body(head_sha="not-a-sha"), "head SHA is malformed"),
    ),
)
def test_blocks_malformed_telegram_approved_merge_issue(
    body: str, reason: str
) -> None:
    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue({"number": 141, "title": "Merge", "body": body})

    assert reason in block.call_args.args[1]
    run_codex.assert_not_called()


def test_telegram_approved_merge_issue_bypasses_codex_and_posts_merge_report() -> None:
    issue = {"number": 141, "title": "Merge", "body": _merge_issue_body()}
    with mock.patch.object(runner, "set_issue_label") as set_label, mock.patch.object(
        runner, "process_telegram_approved_pr_merge_issue"
    ) as process_merge, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert set_label.call_args_list == [
        mock.call(141, runner.LABEL_READY, runner.LABEL_RUNNING)
    ]
    process_merge.assert_called_once_with(
        141,
        runner.TelegramApprovedPrMergeRequest(
            pr_number=123,
            approved_head_sha=HEAD_SHA,
            callback_digest=CALLBACK_DIGEST,
        ),
        retry_decision=mock.ANY,
    )
    run_codex.assert_not_called()


def test_executes_only_head_matched_squash_merge_for_approved_pr() -> None:
    request = runner.TelegramApprovedPrMergeRequest(123, HEAD_SHA, CALLBACK_DIGEST)
    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner, "get_pr_merge_state", return_value=_merge_pr_state()
    ), mock.patch.object(runner, "run_command", return_value=(0, "merged")) as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert report.startswith("DONE:")
    run.assert_called_once_with(
        [
            "gh",
            "pr",
            "merge",
            "123",
            "--repo",
            runner.REPO,
            "--squash",
            "--match-head-commit",
            HEAD_SHA,
        ]
    )


def test_blocks_telegram_approved_merge_when_callback_digest_is_not_signed() -> None:
    request = runner.TelegramApprovedPrMergeRequest(123, HEAD_SHA, "0123456789ab")
    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner, "get_pr_merge_state", return_value=_merge_pr_state()
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert report == "BLOCKED: Telegram approve callback HMAC signature is invalid."
    run.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"state": "CLOSED"}, "not open"),
        ({"isDraft": True}, "still draft"),
        ({"mergeable": "CONFLICTING"}, "not mergeable"),
        ({"headRefOid": "b" * 40}, "head does not match"),
        ({"comments": []}, "Signed Telegram approve audit"),
        (
            {
                "comments": [
                    {
                        "body": _merge_comments()[0]["body"].replace(
                            f"Verified head SHA: {HEAD_SHA}",
                            f"Verified head SHA: {'b' * 40}",
                        )
                    }
                ]
            },
            "Signed Telegram approve audit",
        ),
    ),
)
def test_blocks_telegram_approved_merge_when_verification_fails(
    updates: dict[str, object], reason: str
) -> None:
    request = runner.TelegramApprovedPrMergeRequest(123, HEAD_SHA, CALLBACK_DIGEST)
    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner, "get_pr_merge_state", return_value=_merge_pr_state(**updates)
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert report.startswith("BLOCKED:")
    assert reason in report
    run.assert_not_called()


def test_simple_done_notification_without_pr_url_keeps_plain_message() -> None:
    response = _telegram_response()
    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-bot-placeholder",
            "SKELETON_TG_CHAT": "telegram-chat-placeholder",
        },
        clear=True,
    ), mock.patch.object(
        runner.urllib.request, "urlopen", return_value=response
    ) as urlopen:
        runner.send_telegram_notification(
            runner.build_telegram_message(9, "DONE", "DONE report")
        )

    assert _request_payload(urlopen) == {
        "chat_id": ["telegram-chat-placeholder"],
        "text": ["Проєкт: Skeleton\nЗадача: #9\nСтатус: DONE"],
        "disable_web_page_preview": ["true"],
    }


def test_same_repo_routine_notifications_stay_simple_ukrainian() -> None:
    done_message = runner.build_telegram_message(9, "DONE", DONE_REPORT)
    blocked_message = runner.build_telegram_message(9, "BLOCKED")

    assert done_message == "Проєкт: Skeleton\nЗадача: #9\nСтатус: DONE"
    assert blocked_message == "Проєкт: Skeleton\nЗадача: #9\nСтатус: BLOCKED"
    for message in (done_message, blocked_message):
        assert "Repository:" not in message
        assert "Issue:" not in message
        assert "PR:" not in message
        assert "Репозиторій:" not in message
        assert "Задача: #9" in message


def test_telegram_message_omits_placeholder_pr_url() -> None:
    message = runner.build_telegram_message(
        9, "BLOCKED", DONE_REPORT.replace(PR_URL, "{PR_URL}")
    )

    assert "{PR_URL}" not in message
    assert "PR:" not in message


def test_done_pr_report_builds_card_payload_from_runner_binding() -> None:
    card = {
        "text": "PR card",
        "buttons": [
            {
                "action": "details",
                "label": "Details",
                "callback_payload": {"action": "details"},
            }
        ],
    }
    with mock.patch.object(
        runner, "build_pr_ready_card_payload", return_value=card
    ) as build_card:
        localized_card = runner.build_done_pr_ready_card_payload(DONE_REPORT)

    assert localized_card is not None
    assert localized_card["text"] == (
        "Проєкт: Skeleton\n"
        "Статус: очікує схвалення\n"
        "Коментар: Перевір у ChatGPT перед схваленням."
    )
    assert localized_card["buttons"][0]["label"] == "Деталі"

    build_card.assert_called_once_with(
        repo=runner.REPO,
        pr_number=123,
        head_sha=HEAD_SHA,
        changed_files=(
            "scripts/runner_poll_github_tasks.py",
            "docs/TELEGRAM_APPROVAL_BUTTONS.md",
        ),
        test_summary=runner.TELEGRAM_CARD_TEST_SUMMARY,
        risk_summary=runner.TELEGRAM_CARD_RISK_SUMMARY,
        pr_url=PR_URL,
    )


def test_done_pr_card_hides_technical_details_from_operator_text() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    text = str(card["text"])
    assert text == (
        "Проєкт: Skeleton\n"
        "Статус: очікує схвалення\n"
        "Коментар: Перевір у ChatGPT перед схваленням."
    )
    assert HEAD_SHA not in text
    assert "PR:" not in text
    assert "target_repo" not in text
    assert "Репозиторій:" not in text
    assert "Base:" not in text
    assert "Head:" not in text
    assert "SHA" not in text
    assert "Route:" not in text
    assert "scripts/runner_poll_github_tasks.py" not in text
    assert "docs/TELEGRAM_APPROVAL_BUTTONS.md" not in text
    assert "Skeleton task completed" not in text
    assert "Recommended action" not in text
    assert "Рекомендація: спочатку переглянути в ChatGPT або відкрити PR." not in text
    assert "Ця кнопка нічого не деплоїть" not in text


def test_done_pr_card_shows_default_target_repo() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT, source_issue_number=129)
    assert card is not None

    text = str(card["text"])
    assert "Проєкт: Skeleton" in text
    assert "Репозиторій:" not in text
    assert "Задача: #129" in text


def test_done_pr_card_shows_target_repo_without_misleading_repository_line() -> None:
    card = runner.build_done_pr_ready_card_payload(
        DONE_REPORT,
        target_repository="alanua/bauclock",
    )
    assert card is not None

    text = str(card["text"])
    assert "Проєкт: bauclock" in text
    assert "Репозиторій: alanua/bauclock" in text
    assert "Repository: alanua/Skeleton" not in text
    assert "target_repo" not in text
    assert "Рекомендація: спочатку переглянути в ChatGPT або відкрити PR." not in text
    assert "Ця кнопка нічого не деплоїть і не запускає на сервері." not in text


def test_cross_project_done_pr_card_shows_project_target_repo_and_issue() -> None:
    card = runner.build_done_pr_ready_card_payload(
        DONE_REPORT,
        target_repository="alanua/LumenFlow",
        source_issue_number=999,
    )
    assert card is not None

    text = str(card["text"])
    assert "Проєкт: LumenFlow" in text
    assert "Репозиторій: alanua/LumenFlow" in text
    assert "Задача: #999" in text
    assert "project:" not in text
    assert "target_repo:" not in text
    assert "issue:" not in text
    assert "Repository: alanua/Skeleton" not in text


def test_target_repo_pr_card_uses_details_only_buttons() -> None:
    card = runner.build_done_pr_ready_card_payload(
        DONE_REPORT,
        target_repository="alanua/Lavalamp",
    )
    assert card is not None
    reply_markup = runner.card_payload_to_inline_keyboard(card)

    buttons = [row[0] for row in reply_markup["inline_keyboard"]]
    assert [button["text"] for button in buttons] == ["Деталі", "Відкрити PR"]
    assert buttons[0]["callback_data"].startswith("tpr2:details:l:p123:nosha:")
    assert len(buttons[0]["callback_data"].encode("utf-8")) <= runner.TELEGRAM_CALLBACK_DATA_LIMIT


def test_lumenflow_target_repo_pr_card_uses_repo_callback_key() -> None:
    card = runner.build_done_pr_ready_card_payload(
        DONE_REPORT,
        target_repository="alanua/LumenFlow",
    )
    assert card is not None
    reply_markup = runner.card_payload_to_inline_keyboard(card)

    details_button = reply_markup["inline_keyboard"][0][0]
    assert details_button["callback_data"].startswith("tpr2:details:f:p123:nosha:")
    assert len(details_button["callback_data"].encode("utf-8")) <= (
        runner.TELEGRAM_CALLBACK_DATA_LIMIT
    )


def test_done_pr_card_keeps_technical_details_in_payload() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    assert card["head_sha"] == HEAD_SHA
    assert card["changed_files"] == [
        "docs/TELEGRAM_APPROVAL_BUTTONS.md",
        "scripts/runner_poll_github_tasks.py",
    ]
    assert card["test_summary"] == runner.TELEGRAM_CARD_TEST_SUMMARY
    assert card["risk_summary"] == runner.TELEGRAM_CARD_RISK_SUMMARY


def test_inline_keyboard_has_pr_review_buttons_when_binding_is_reliable() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    reply_markup = runner.card_payload_to_inline_keyboard(card)
    buttons = [row[0] for row in reply_markup["inline_keyboard"]]
    assert [button["text"] for button in buttons] == [
        "Схвалити",
        "Відхилити",
        "Деталі",
        "Відкрити PR",
    ]
    assert [button["action"] for button in card["buttons"]] == [
        "approve",
        "reject",
        "details",
        "open_pr",
    ]
    assert buttons[-1]["url"] == PR_URL
    assert all(
        len(button["callback_data"].encode("utf-8"))
        <= runner.TELEGRAM_CALLBACK_DATA_LIMIT
        for button in buttons
        if "callback_data" in button
    )


def test_callback_data_carries_action_pr_number_and_head_marker() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    reply_markup = runner.card_payload_to_inline_keyboard(card)
    callback_values = [
        row[0]["callback_data"]
        for row in reply_markup["inline_keyboard"]
        if "callback_data" in row[0]
    ]

    assert callback_values
    assert all(value.startswith("tpr1:") for value in callback_values)
    assert any(value.startswith("tpr1:approve:p123:aaaaaaaa:") for value in callback_values)
    assert any(value.startswith("tpr1:reject:p123:aaaaaaaa:") for value in callback_values)
    assert all(":p123:aaaaaaaa:" in value for value in callback_values)


def test_approve_reject_buttons_require_reliable_sha_and_changed_files() -> None:
    report = f"DONE: ok\n\nDraft PR: {PR_URL}"

    card = runner.build_done_pr_ready_card_payload(report)
    assert card is not None
    reply_markup = runner.card_payload_to_inline_keyboard(card)
    buttons = [row[0] for row in reply_markup["inline_keyboard"]]

    assert [button["text"] for button in buttons] == ["Деталі", "Відкрити PR"]
    assert str(card["text"]) == (
        "Проєкт: Skeleton\n"
        "Статус: готово до перегляду\n"
        "Коментар: Відкрий PR, якщо потрібні деталі."
    )


def test_send_telegram_notification_posts_reply_markup_for_card() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None
    reply_markup = runner.card_payload_to_inline_keyboard(card)
    response = _telegram_response()

    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-bot-placeholder",
            "SKELETON_TG_CHAT": "telegram-chat-placeholder",
        },
        clear=True,
    ), mock.patch.object(
        runner.urllib.request, "urlopen", return_value=response
    ) as urlopen:
        runner.send_telegram_notification(str(card["text"]), reply_markup)

    payload = _request_payload(urlopen)
    assert json.loads(payload["reply_markup"][0]) == reply_markup
    assert payload["text"] == [card["text"]]


def test_send_telegram_notification_without_env_makes_no_network_call() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
        runner.urllib.request, "urlopen"
    ) as urlopen:
        runner.send_telegram_notification(
            str(card["text"]), runner.card_payload_to_inline_keyboard(card)
        )

    urlopen.assert_not_called()


def test_done_pr_card_success_sends_reply_markup() -> None:
    card = {"text": "PR ready card", "buttons": []}
    reply_markup = {"inline_keyboard": []}

    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "build_done_pr_ready_card_payload", return_value=card
    ), mock.patch.object(
        runner, "card_payload_to_inline_keyboard", return_value=reply_markup
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    send.assert_called_once_with("PR ready card", reply_markup)


def test_done_pr_card_uses_target_repository_from_issue_body() -> None:
    issue = {
        "number": 129,
        "body": "Target Repository: alanua/bauclock\nExpected Output: done\n\n```task\nDo it\n```",
        "state": "open",
        "closed": False,
        "labels": [{"name": runner.LABEL_DONE}],
    }

    with mock.patch.object(
        runner, "get_notification_issue", return_value=issue
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    assert send.call_count == 1
    text = send.call_args.args[0]
    reply_markup = send.call_args.args[1]
    assert "Проєкт: bauclock" in text
    assert "Репозиторій: alanua/bauclock" in text
    assert "Задача: #129" in text
    assert "Repository: alanua/Skeleton" not in text
    assert "target_repo" not in text
    assert [row[0]["text"] for row in reply_markup["inline_keyboard"]] == [
        "Деталі",
        "Відкрити PR",
    ]


def test_cross_project_blocked_status_uses_target_repository_from_issue_body() -> None:
    issue = {
        "number": 999,
        "body": (
            "Target Project: lumenflow\n"
            "Target Repository: alanua/LumenFlow\n\n"
            "```task\nDo it\n```"
        ),
        "state": "open",
        "closed": False,
        "labels": [{"name": runner.LABEL_BLOCKED}],
    }

    with mock.patch.object(
        runner, "get_notification_issue", return_value=issue
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(999, "BLOCKED")

    send.assert_called_once_with(
        "Проєкт: LumenFlow\n"
        "Репозиторій: alanua/LumenFlow\n"
        "Задача: #999\n"
        "Статус: BLOCKED"
    )


def test_done_pr_card_build_failure_falls_back_to_plain_done() -> None:
    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner,
        "build_done_pr_ready_card_payload",
        side_effect=RuntimeError("telegram-bot-token-must-not-leak"),
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    send.assert_called_once_with(_plain_done_message())
    assert "telegram-bot-token-must-not-leak" not in send.call_args.args[0]


def test_done_pr_reply_markup_send_failure_falls_back_to_plain_done() -> None:
    card = {"text": "PR ready card", "buttons": []}
    reply_markup = {"inline_keyboard": []}

    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "build_done_pr_ready_card_payload", return_value=card
    ), mock.patch.object(
        runner, "card_payload_to_inline_keyboard", return_value=reply_markup
    ), mock.patch.object(
        runner,
        "send_telegram_notification",
        side_effect=(RuntimeError("reply_markup send failed"), None),
    ) as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    assert send.call_args_list == [
        mock.call("PR ready card", reply_markup),
        mock.call(_plain_done_message()),
    ]


def test_pr_card_build_does_not_execute_merge_or_reject_side_effects() -> None:
    card = {"text": "PR ready card", "buttons": []}
    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "build_done_pr_ready_card_payload", return_value=card
    ), mock.patch.object(runner, "run_command") as run_command, mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    run_command.assert_not_called()
    send.assert_called_once()


def _maintenance_issue(
    task_id: str | None, task_body: str = "", metadata: str = ""
) -> dict[str, object]:
    lines = ["Mode: RUNTIME_MAINTENANCE_TASK"]
    if task_id is not None:
        lines.append(f"Maintenance Task ID: {task_id}")
    if metadata:
        lines.extend(metadata.splitlines())
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return {"number": 145, "title": "Runner maintenance", "body": "\n".join(lines)}


def _protected_exact_source_repair_body(
    *,
    target_repository: str = runner.REPO,
    source_issue: int | str = 1840,
    output_branch: str = "runner/issue-1840",
    source_ref: str = "runner/issue-1840",
    expected_source_sha: str = HEAD_SHA,
    task_body: str = "",
    extra_metadata: tuple[str, ...] = (),
) -> str:
    metadata = [
        f"Target Repository: {target_repository}",
        f"Source Issue: {source_issue}",
        f"Output Branch: {output_branch}",
        f"Source Ref: {source_ref}",
        f"Expected Source SHA: {expected_source_sha}",
        *extra_metadata,
    ]
    body = _maintenance_issue(
        runner.REPAIR_PROTECTED_EXACT_SOURCE_WORKTREE,
        task_body=task_body,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _protected_exact_source_repair_commands(
    *,
    checkout_path: Path,
    worktree_path: Path,
    source_ref: str = "runner/issue-1840",
    expected_source_sha: str = HEAD_SHA,
    existing: bool = False,
    existing_branch: str = "runner/issue-1840",
    existing_head: str = HEAD_SHA,
    existing_status: str = "",
    fetch_code: int = 0,
    resolved_sha: str = HEAD_SHA,
    add_code: int = 0,
    remove_code: int = 0,
) -> object:
    if existing:
        worktree_path.mkdir(parents=True, exist_ok=True)
        (worktree_path / ".git").write_text("gitdir: /tmp/git-dir\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        *,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        if command == [
            "git",
            "fetch",
            "origin",
            f"{source_ref}:refs/remotes/origin/{source_ref}",
        ]:
            assert Path(cwd or "") == checkout_path
            assert timeout == runner.PROTECTED_SOURCE_FETCH_TIMEOUT_SECONDS
            return fetch_code, "fetch output must not leak"
        if command == [
            "git",
            "rev-parse",
            f"refs/remotes/origin/{source_ref}^{{commit}}",
        ]:
            assert Path(cwd or "") == checkout_path
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            return 0, f"{resolved_sha}\n"
        if command == ["git", "cat-file", "-e", f"{expected_source_sha}^{{commit}}"]:
            assert Path(cwd or "") == checkout_path
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            return 0, ""
        if command == ["git", "status", "--porcelain"]:
            assert Path(cwd or "") == worktree_path
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            return 0, existing_status
        if command == ["git", "branch", "--show-current"]:
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            if Path(cwd or "") == worktree_path and existing:
                return 0, f"{existing_branch}\n"
            if Path(cwd or "") == worktree_path:
                return 0, "runner/issue-1840\n"
        if command == ["git", "rev-parse", "HEAD"]:
            assert Path(cwd or "") == worktree_path
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            return 0, f"{existing_head if existing else expected_source_sha}\n"
        if command == ["git", "worktree", "remove", "--force", str(worktree_path)]:
            assert Path(cwd or "") == checkout_path
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            if remove_code == 0:
                runner.shutil.rmtree(worktree_path)
            return remove_code, "remove output must not leak"
        if command == [
            "git",
            "worktree",
            "add",
            "-B",
            "runner/issue-1840",
            str(worktree_path),
            expected_source_sha,
        ]:
            assert Path(cwd or "") == checkout_path
            assert timeout == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
            worktree_path.mkdir(parents=True, exist_ok=True)
            (worktree_path / ".git").write_text("gitdir: /tmp/git-dir\n", encoding="utf-8")
            return add_code, "add output must not leak"
        return 2, f"unexpected command: {command!r}"

    return run


def _successful_maintenance_command(
    command: list[str], cwd: str | None = None
) -> tuple[int, str]:
    del cwd
    if command[:5] == ["sudo", "-n", "systemctl", "show", "--property=Result"]:
        return 0, "success\n"
    return 0, ""


def _private_memory_config(tmp_path: Path, db_path: Path | None = None) -> Path:
    config_path = tmp_path / "synthetic_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": "skeleton.private_memory.config.v0",
                "database": {"path": str(db_path or tmp_path / "memory.sqlite")},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _assert_private_memory_runner_report_is_public_safe(
    report: str, tmp_path: Path
) -> None:
    lowered = report.lower()
    assert str(tmp_path) not in report
    assert "synthetic_config.json" not in report
    assert "memory.sqlite" not in report
    assert "select " not in lowered
    assert "create table" not in lowered
    assert "secret" not in lowered
    assert "token" not in lowered
    assert "credential" not in lowered
    assert "drive" not in lowered


def _issue_publish_inspection_body(
    *,
    repository: str = runner.REPO,
    source_issue: int | str = 123,
    expected_branch: str | None = "runner/issue-123",
    allowed_files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    task_id: str = runner.INSPECT_ISSUE_WORKTREE_FOR_PUBLISH,
    pr_title: str | None = None,
    task_body: str = "",
) -> str:
    metadata = []
    if repository is not None:
        metadata.append(f"Repository: {repository}")
    metadata.append(f"Source Issue: {source_issue}")
    if expected_branch is not None:
        metadata.append(f"Expected Branch: {expected_branch}")
    if pr_title is not None:
        metadata.append(f"PR Title: {pr_title}")
    metadata.append("Allowed Files:")
    metadata.extend(f"- {path}" for path in allowed_files)
    body = _maintenance_issue(
        task_id,
        task_body,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _publish_existing_issue_worktree_body(
    *,
    target_repository: str = runner.REPO,
    source_issue: int | str = 123,
    base_branch: str = "main",
    output_branch: str = "runner/issue-123",
    allowed_files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    draft_pr: str = "true",
    pr_title: str | None = None,
    publish_override: object | None = None,
) -> str:
    metadata = [
        f"Target Repository: {target_repository}",
        f"Source Issue: {source_issue}",
        f"Base Branch: {base_branch}",
        f"Output Branch: {output_branch}",
        f"Draft PR: {draft_pr}",
    ]
    if pr_title is not None:
        metadata.append(f"PR Title: {pr_title}")
    if publish_override is not None:
        if isinstance(publish_override, str):
            metadata.append(f"Publish Override: {publish_override}")
        else:
            metadata.append(
                "Publish Override: "
                f"{json.dumps(publish_override, separators=(',', ':'), sort_keys=True)}"
            )
    metadata.append("Allowed Files:")
    metadata.extend(f"- {path}" for path in allowed_files)
    body = _maintenance_issue(
        runner.PUBLISH_EXISTING_ISSUE_WORKTREE,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _publish_issue_worktree_to_existing_pr_body(
    *,
    repository: str = runner.REPO,
    source_issue: int | str = 1640,
    expected_source_branch: str = "runner/issue-1640",
    pr_number: int | str = 1638,
    expected_pr_head_sha: str = HEAD_SHA,
    expected_pr_head_branch: str = "runner/issue-1638",
    allowed_files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    operator_approval: str = runner.PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR,
    extra_metadata: tuple[str, ...] = (),
) -> str:
    metadata = [
        f"Repository: {repository}",
        f"Source Issue: {source_issue}",
        f"Expected Source Branch: {expected_source_branch}",
        f"Pull Request: {pr_number}",
        f"Expected PR Head SHA: {expected_pr_head_sha}",
        f"Expected PR Head Branch: {expected_pr_head_branch}",
        f"Operator Approval: {operator_approval}",
        *extra_metadata,
        "Allowed Files:",
    ]
    metadata.extend(f"- {path}" for path in allowed_files)
    body = _maintenance_issue(
        runner.PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _overlay_registered_worktree_body(
    *,
    packet_id: str = "home_edge_1640_to_pr_1638",
    operator_approval: str = runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR,
    extra_metadata: tuple[str, ...] = (),
) -> str:
    metadata = [
        f"Recovery Packet: {packet_id}",
        f"Operator Approval: {operator_approval}",
        *extra_metadata,
    ]
    body = _maintenance_issue(
        runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _publish_container_validation_worktree_body(
    *,
    repository: str = runner.REPO,
    source_issue: int | str = 1667,
    expected_source_branch: str = "runner/issue-1667",
    base_branch: str = "main",
    output_branch: str = "runner/issue-1667",
    draft_pr: str = "true",
    operator_approval: str = runner.PUBLISH_CONTAINER_VALIDATION_WORKTREE,
    extra_metadata: tuple[str, ...] = (),
    task_body: str = "",
) -> str:
    metadata = [
        f"Repository: {repository}",
        f"Source Issue: {source_issue}",
        f"Expected Source Branch: {expected_source_branch}",
        f"Base Branch: {base_branch}",
        f"Output Branch: {output_branch}",
        f"Draft PR: {draft_pr}",
        f"Operator Approval: {operator_approval}",
        *extra_metadata,
    ]
    body = _maintenance_issue(
        runner.PUBLISH_CONTAINER_VALIDATION_WORKTREE,
        task_body=task_body,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _project_tree_with_skeleton_worktree_root(worktree_root: Path) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"]["skeleton"]["worktree_root"] = str(worktree_root)
    return project_tree


def _prepare_container_validation_worktree(root: Path) -> Path:
    worktree_path = root / runner.CONTAINER_VALIDATION_WORKTREE_ID
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: /tmp/git-dir\n", encoding="utf-8")
    for relative_path in runner.CONTAINER_VALIDATION_PUBLISH_FILES:
        path = worktree_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative_path}\n", encoding="utf-8")
    return worktree_path


def _container_validation_pr_state(
    *,
    state: str = "OPEN",
    is_draft: bool = True,
    base_ref: str = "main",
    head_ref: str = "runner/issue-1667",
    head_sha: str = "c" * 40,
    head_repository: str = runner.REPO,
    url: str = "https://github.com/alanua/Skeleton/pull/1667",
) -> dict[str, object]:
    owner, name = head_repository.split("/", 1)
    return {
        "number": 1667,
        "state": state,
        "isDraft": is_draft,
        "baseRefName": base_ref,
        "headRefName": head_ref,
        "headRefOid": head_sha,
        "headRepository": {
            "nameWithOwner": head_repository,
            "owner": {"login": owner},
            "name": name,
        },
        "headRepositoryOwner": {"login": owner},
        "url": url,
    }


def _container_validation_publish_commands(
    *,
    worktree_path: Path,
    branch: str = "runner/issue-1667",
    remote_url: str = "https://github.com/alanua/Skeleton.git",
    changed_files: tuple[str, ...] = runner.CONTAINER_VALIDATION_PUBLISH_FILES,
    untracked_files: tuple[str, ...] = (),
    base_diff_code: int = 1,
    diff_check_code: int = 0,
    add_code: int = 0,
    commit_code: int = 0,
    post_commit_head: str = "c" * 40,
    push_code: int = 0,
    existing_prs: list[dict[str, object]] | None = None,
    pr_list_code: int = 0,
    pr_create_code: int = 0,
    pr_create_url: str = PR_URL,
) -> object:
    def run(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        assert Path(cwd or "") == worktree_path
        if command == ["git", "branch", "--show-current"]:
            return 0, f"{branch}\n"
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, f"{remote_url}\n"
        if command == ["git", "diff", "--name-only", "HEAD", "--"]:
            return 0, "\n".join(changed_files) + ("\n" if changed_files else "")
        if command == ["git", "ls-files", "--others", "--exclude-standard"]:
            return 0, "\n".join(untracked_files) + ("\n" if untracked_files else "")
        if command == [
            "git",
            "diff",
            "--quiet",
            "main...HEAD",
            "--",
            *runner.CONTAINER_VALIDATION_PUBLISH_FILES,
        ]:
            return base_diff_code, "branch diff output must not leak"
        if command == [
            "git",
            "diff",
            "--check",
            "--",
            *runner.CONTAINER_VALIDATION_PUBLISH_FILES,
        ]:
            return diff_check_code, "diff check output must not leak"
        if command == ["git", "add", "--", *runner.CONTAINER_VALIDATION_PUBLISH_FILES]:
            return add_code, "add failed output must not leak"
        if command == [
            "git",
            "commit",
            "-m",
            "Publish container package validation workflow",
        ]:
            return commit_code, "commit failed output must not leak"
        if command == ["git", "rev-parse", "HEAD"]:
            return 0, f"{post_commit_head}\n"
        if command == [
            "git",
            "push",
            "origin",
            "refs/heads/runner/issue-1667:refs/heads/runner/issue-1667",
        ]:
            return push_code, "push failed output must not leak"
        if command[:7] == [
            "gh",
            "pr",
            "list",
            "--repo",
            runner.REPO,
            "--head",
            "runner/issue-1667",
        ]:
            return pr_list_code, json.dumps(existing_prs or [])
        if command[:7] == [
            "gh",
            "pr",
            "create",
            "--repo",
            runner.REPO,
            "--base",
            "main",
        ]:
            return pr_create_code, f"{pr_create_url}\n"
        return 2, f"unexpected command: {command!r}"

    return run


def _valid_publish_existing_override(
    *,
    target_repository: str = runner.REPO,
    source_issue: int = 123,
    base_branch: str = "main",
    output_branch: str = "runner/issue-123",
    allowed_files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    draft_pr: bool = True,
    action: str = runner.PUBLISH_EXISTING_ISSUE_WORKTREE,
) -> dict[str, object]:
    return {
        "action": action,
        "target_repository": target_repository,
        "source_issue": source_issue,
        "output_branch": output_branch,
        "base_branch": base_branch,
        "allowed_files": list(allowed_files),
        "draft_pr": draft_pr,
    }


def _publish_target_project_issue_worktree_body(
    *,
    target_project: str = "lumenflow",
    target_repository: str = "alanua/LumenFlow",
    source_issue: int | str = 123,
    base_branch: str = "main",
    output_branch: str = "runner/issue-123",
    allowed_files: tuple[str, ...] = ("README.md",),
    draft_pr: str = "true",
    extra_metadata: tuple[str, ...] = (),
) -> str:
    metadata = [
        f"Target Project: {target_project}",
        f"Target Repository: {target_repository}",
        f"Source Issue: {source_issue}",
        f"Base Branch: {base_branch}",
        f"Output Branch: {output_branch}",
        f"Draft PR: {draft_pr}",
        *extra_metadata,
        "Allowed Files:",
    ]
    metadata.extend(f"- {path}" for path in allowed_files)
    body = _maintenance_issue(
        runner.PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _issue_publish_commands(
    *,
    worktree_path: Path,
    repository: str = runner.REPO,
    branch: str = "runner/issue-123",
    remote_url: str = "https://github.com/alanua/Skeleton.git",
    changed_files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    untracked_files: tuple[str, ...] = (),
    validated_publish_files: tuple[str, ...] | None = None,
    existing_pr_url: str = "",
    existing_pr_code: int = 0,
    remote_branch_exists: bool = False,
    ls_remote_code: int = 0,
    branch_diff_code: int = 1,
    diff_check_code: int = 0,
    add_code: int = 0,
    commit_code: int = 0,
    pre_commit_head: str = "0000000000000000000000000000000000000000",
    post_commit_head: str = "1111111111111111111111111111111111111111",
    push_code: int = 0,
    pr_create_code: int = 0,
    pr_create_url: str = PR_URL,
    commit_message: str = "Publish issue #123 worktree",
    raw_suffix: str = "",
) -> object:
    rev_parse_count = 0
    expected_publish_files = (
        changed_files if validated_publish_files is None else validated_publish_files
    )

    def run(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        nonlocal rev_parse_count
        assert Path(cwd or "") == worktree_path
        if command == ["git", "branch", "--show-current"]:
            return 0, f"{branch}\n{raw_suffix}"
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, f"{remote_url}\n"
        if command == ["git", "diff", "--name-only", "HEAD", "--"]:
            return 0, "\n".join(changed_files) + ("\n" if changed_files else "")
        if command == ["git", "ls-files", "--others", "--exclude-standard"]:
            return 0, "\n".join(untracked_files) + ("\n" if untracked_files else "")
        if command[:7] == [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
        ]:
            return existing_pr_code, f"{existing_pr_url}\n" if existing_pr_url else ""
        if command == ["git", "ls-remote", "--heads", "origin", branch]:
            output = (
                f"{post_commit_head}\trefs/heads/{branch}\n"
                if remote_branch_exists
                else ""
            )
            return ls_remote_code, output
        if command == ["git", "diff", "--check", "--", *expected_publish_files]:
            return diff_check_code, "diff check output must not leak"
        if command == ["git", "rev-parse", "HEAD"]:
            rev_parse_count += 1
            if rev_parse_count == 1:
                return 0, f"{pre_commit_head}\n"
            return 0, f"{post_commit_head}\n"
        if command == ["git", "add", "--", *expected_publish_files]:
            return add_code, "add failed output must not leak"
        if command == [
            "git",
            "commit",
            "-m",
            commit_message,
        ]:
            return commit_code, "commit failed output must not leak"
        if command == ["git", "diff", "--quiet", "main...HEAD", "--"]:
            return branch_diff_code, "branch diff output must not leak"
        if command == [
            "git",
            "push",
            "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
        ]:
            return push_code, "push failed output must not leak"
        if command[:7] == [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            "main",
        ]:
            return pr_create_code, f"{pr_create_url}\n"
        return 2, "unexpected command output must not leak"

    return run


def _existing_pr_publish_state(
    *,
    number: int = 1638,
    state: str = "OPEN",
    is_draft: bool = True,
    base_ref: str = "main",
    head_ref: str = "runner/issue-1638",
    head_sha: str = HEAD_SHA,
    head_repository: str = runner.REPO,
    url: str = "https://github.com/alanua/Skeleton/pull/1638",
    files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
) -> dict[str, object]:
    owner, name = head_repository.split("/", 1)
    return {
        "number": number,
        "state": state,
        "isDraft": is_draft,
        "baseRefName": base_ref,
        "headRefName": head_ref,
        "headRefOid": head_sha,
        "headRepository": {
            "nameWithOwner": head_repository,
            "owner": {"login": owner},
            "name": name,
        },
        "headRepositoryOwner": {"login": owner},
        "url": url,
        "files": [{"path": path} for path in files],
    }


def _existing_pr_publish_commands(
    *,
    worktree_path: Path,
    branch: str = "runner/issue-1640",
    remote_url: str = "https://github.com/alanua/Skeleton.git",
    pre_pr_state: dict[str, object] | None = None,
    post_pr_state: dict[str, object] | None = None,
    changed_files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    untracked_files: tuple[str, ...] = (),
    validated_publish_files: tuple[str, ...] | None = None,
    ancestor_code: int = 0,
    diff_check_code: int = 0,
    add_code: int = 0,
    commit_code: int = 0,
    post_commit_head: str = "b" * 40,
    push_code: int = 0,
    pr_view_code: int = 0,
) -> object:
    pr_view_count = 0
    expected_publish_files = (
        changed_files if validated_publish_files is None else validated_publish_files
    )
    pre_state = pre_pr_state or _existing_pr_publish_state()
    post_state = post_pr_state or _existing_pr_publish_state(head_sha=post_commit_head)

    def run(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        nonlocal pr_view_count
        assert Path(cwd or "") == worktree_path
        if command[:4] == ["gh", "pr", "view", "1638"]:
            pr_view_count += 1
            if pr_view_code != 0:
                return pr_view_code, "pr view failed output must not leak"
            return 0, json.dumps(pre_state if pr_view_count == 1 else post_state)
        if command == ["git", "branch", "--show-current"]:
            return 0, f"{branch}\n"
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, f"{remote_url}\n"
        if command == ["git", "merge-base", "--is-ancestor", HEAD_SHA, "HEAD"]:
            return ancestor_code, ""
        if command == ["git", "diff", "--name-only", "HEAD", "--"]:
            return 0, "\n".join(changed_files) + ("\n" if changed_files else "")
        if command == ["git", "ls-files", "--others", "--exclude-standard"]:
            return 0, "\n".join(untracked_files) + ("\n" if untracked_files else "")
        if command == ["git", "diff", "--check", "--", *expected_publish_files]:
            return diff_check_code, "diff check failed output must not leak"
        if command == ["git", "add", "--", *expected_publish_files]:
            return add_code, "add failed output must not leak"
        if command == [
            "git",
            "commit",
            "-m",
            "Publish issue #1640 worktree to existing PR",
        ]:
            return commit_code, "commit failed output must not leak"
        if command == ["git", "rev-parse", "HEAD"]:
            return 0, f"{post_commit_head}\n"
        if command == [
            "git",
            "push",
            "origin",
            f"--force-with-lease=runner/issue-1638:{HEAD_SHA}",
            "HEAD:refs/heads/runner/issue-1638",
        ]:
            return push_code, "push failed output must not leak"
        return 2, f"unexpected command: {command!r}"

    return run


def _prepare_overlay_source_worktree(root: Path, packet_id: str) -> Path:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = root / f"issue-{packet.source_issue}"
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: /tmp/git-dir\n", encoding="utf-8")
    for relative_path in packet.allowed_files:
        path = worktree_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{packet_id}:{relative_path}\n", encoding="utf-8")
    return worktree_path


def _overlay_pr_state(
    packet_id: str,
    *,
    head_sha: str | None = None,
    base_sha: str = "a" * 40,
    files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    number: int | None = None,
    state: str = "OPEN",
    is_draft: bool = True,
    base_ref: str = "main",
    head_ref: str | None = None,
    head_repository: str = runner.REPO,
) -> dict[str, object]:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    pr_state = _existing_pr_publish_state(
        number=number if number is not None else packet.pr_number,
        state=state,
        is_draft=is_draft,
        base_ref=base_ref,
        head_ref=head_ref or packet.target_branch,
        head_sha=head_sha or packet.target_head_sha,
        head_repository=head_repository,
        url=f"https://github.com/alanua/Skeleton/pull/{packet.pr_number}",
        files=files,
    )
    pr_state["baseRefOid"] = base_sha
    return pr_state


def _overlay_rest_pr_payload(packet_id: str, *, head_sha: str | None = None) -> dict[str, object]:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    return {
        "number": packet.pr_number,
        "state": "open",
        "draft": True,
        "html_url": f"https://github.com/alanua/Skeleton/pull/{packet.pr_number}",
        "base": {"ref": "main", "sha": "a" * 40},
        "head": {
            "ref": packet.target_branch,
            "sha": head_sha or packet.target_head_sha,
            "repo": {
                "full_name": runner.REPO,
                "name": "Skeleton",
                "owner": {"login": "alanua"},
            },
        },
    }


def _overlay_rest_file_payload(files: tuple[str, ...]) -> list[dict[str, object]]:
    return [{"filename": path} for path in files]


class _OverlayRestResponse:
    def __init__(
        self,
        payload: object,
        url: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> None:
        self.payload = payload
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.raw = raw

    def __enter__(self) -> "_OverlayRestResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status

    def read(self, _size: int = -1) -> bytes:
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload).encode("utf-8")


class _OverlayRestOpener:
    def __init__(
        self, responses: dict[str, _OverlayRestResponse | list[_OverlayRestResponse]]
    ) -> None:
        self.responses = responses
        self.requests: list[object] = []
        self.timeouts: list[object] = []

    def open(self, request: object, *, timeout: object) -> _OverlayRestResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        url = request.full_url
        response_or_responses = self.responses[url]
        if isinstance(response_or_responses, list):
            response = response_or_responses.pop(0)
        else:
            response = response_or_responses
        return _OverlayRestResponse(
            response.payload,
            response.url,
            status=response.status,
            headers=response.headers,
            raw=response.raw,
        )


def _overlay_rest_opener(
    packet_id: str,
    *,
    files: tuple[str, ...] = ("scripts/runner_poll_github_tasks.py",),
    head_sha: str | None = None,
    responses: dict[str, _OverlayRestResponse] | None = None,
) -> _OverlayRestOpener:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    pr_url = f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}"
    files_url = f"{pr_url}/files?per_page=100&page=1"
    return _OverlayRestOpener(
        responses
        or {
            pr_url: _OverlayRestResponse(_overlay_rest_pr_payload(packet_id, head_sha=head_sha), pr_url),
            files_url: _OverlayRestResponse(_overlay_rest_file_payload(files), files_url),
        }
    )


def _overlay_registered_worktree_commands(
    *,
    worktree_path: Path,
    packet_id: str = "home_edge_1640_to_pr_1638",
    branch: str | None = None,
    remote_url: str = "https://github.com/alanua/Skeleton.git",
    changed_files: tuple[str, ...] | None = None,
    staged_only_files: tuple[str, ...] = (),
    untracked_files: tuple[str, ...] = (),
    diff_check_code: int = 0,
    untracked_diff_check_code: int = 0,
    untracked_diff_check_output: str = "",
    base_cat_file_code: int = 0,
    base_fetch_code: int = 0,
    fetched_base_sha: str | None = None,
    cat_file_code: int = 0,
    fetch_code: int = 0,
    fetched_sha: str | None = None,
    read_tree_code: int = 0,
    write_tree_code: int = 0,
    commit_tree_code: int = 0,
    new_commit_sha: str = "d" * 40,
    parent_sha: str | None = None,
    commit_diff_files: tuple[str, ...] | None = None,
    pr_diff_files: tuple[str, ...] | None = None,
    push_code: int = 0,
    pre_pr_state: dict[str, object] | None = None,
    post_pr_state: dict[str, object] | None = None,
    pr_view_code: int = 0,
) -> object:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    pr_view_count = 0
    source_changed_files = changed_files if changed_files is not None else (packet.allowed_files[0],)
    allowed_untracked_files = tuple(
        path
        for path in untracked_files
        if not runner._is_ignored_issue_publish_untracked_path(path)
        and path in packet.allowed_files
    )
    checked_files = tuple(dict.fromkeys([*source_changed_files, *allowed_untracked_files]))
    before_state = pre_pr_state or _overlay_pr_state(packet_id)
    base_sha = str(before_state.get("baseRefOid") or "")
    pre_file_paths = tuple(
        item["path"] for item in before_state["files"] if isinstance(item, dict)
    )
    after_state = post_pr_state or _overlay_pr_state(
        packet_id,
        head_sha=new_commit_sha,
        files=tuple(sorted(set(pre_file_paths) | set(checked_files))),
    )
    blob_shas = iter([f"{index + 1:040x}" for index in range(100)])

    def run(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        nonlocal pr_view_count
        assert Path(cwd or "") == worktree_path
        if command[:4] == ["gh", "pr", "view", str(packet.pr_number)]:
            pr_view_count += 1
            if pr_view_code != 0:
                return pr_view_code, "pr view output must not leak"
            return 0, json.dumps(before_state if pr_view_count == 1 else after_state)
        if command == ["git", "branch", "--show-current"]:
            return 0, f"{branch or packet.source_branch}\n"
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, f"{remote_url}\n"
        if command == ["git", "diff", "--name-only", "HEAD", "--"]:
            return 0, "\n".join(source_changed_files) + "\n"
        if command == ["git", "ls-files", "--others", "--exclude-standard"]:
            return 0, "\n".join(untracked_files) + ("\n" if untracked_files else "")
        if command[:4] == ["git", "diff", "--quiet", "--"] and len(command) == 5:
            return (0 if command[4] in staged_only_files else 1), ""
        if command[:4] == ["git", "ls-files", "--stage", "--"] and len(command) == 5:
            return 0, f"100644 {'b' * 40} 0\t{command[4]}\n"
        if command == ["git", "read-tree", "HEAD"]:
            return read_tree_code, "validation read tree output must not leak"
        if command == [
            "git",
            "diff",
            "--check",
            "--cached",
            "HEAD",
            "--",
            *checked_files,
        ]:
            return diff_check_code, "diff check output must not leak"
        if (
            command[:5] == ["git", "diff", "--check", "--no-index", "--"]
            and len(command) == 7
            and command[5] == os.devnull
            and command[6] in allowed_untracked_files
        ):
            return untracked_diff_check_code, untracked_diff_check_output
        if command == ["git", "cat-file", "-e", f"{base_sha}^{{commit}}"]:
            return base_cat_file_code, ""
        if command == ["git", "fetch", "origin", "main:refs/remotes/origin/main"]:
            return base_fetch_code, "base fetch output must not leak"
        if command == ["git", "rev-parse", "refs/remotes/origin/main"]:
            return 0, f"{fetched_base_sha or base_sha}\n"
        if command == ["git", "cat-file", "-e", f"{packet.target_head_sha}^{{commit}}"]:
            return cat_file_code, ""
        if command == [
            "git",
            "fetch",
            "origin",
            f"{packet.target_branch}:refs/remotes/origin/{packet.target_branch}",
        ]:
            return fetch_code, "fetch output must not leak"
        if command == ["git", "rev-parse", f"refs/remotes/origin/{packet.target_branch}"]:
            return 0, f"{fetched_sha or packet.target_head_sha}\n"
        if command == ["git", "read-tree", packet.target_head_sha]:
            return read_tree_code, "read tree output must not leak"
        if command[:3] == ["git", "hash-object", "-w"]:
            return 0, f"{next(blob_shas)}\n"
        if command[:2] == ["git", "update-index"]:
            return 0, ""
        if command == ["git", "write-tree"]:
            return write_tree_code, f"{'e' * 40}\n"
        if command[:2] == ["git", "commit-tree"]:
            return commit_tree_code, f"{new_commit_sha}\n"
        if command == ["git", "rev-parse", f"{new_commit_sha}^"]:
            return 0, f"{parent_sha or packet.target_head_sha}\n"
        if command == ["git", "diff", "--name-only", packet.target_head_sha, new_commit_sha, "--"]:
            files = commit_diff_files or checked_files
            return 0, "\n".join(files) + "\n"
        if command == ["git", "diff", "--name-only", base_sha, new_commit_sha, "--"]:
            files = (
                pr_diff_files
                if pr_diff_files is not None
                else tuple(sorted(set(pre_file_paths) | set(checked_files)))
            )
            return 0, "\n".join(files) + "\n"
        if command == [
            "git",
            "push",
            "origin",
            f"--force-with-lease={packet.target_branch}:{packet.target_head_sha}",
            f"{new_commit_sha}:refs/heads/{packet.target_branch}",
        ]:
            return push_code, "push output must not leak"
        return 2, f"unexpected command: {command!r}"

    return run


def _prepare_issue_publish_worktree(root: Path, issue_number: int = 123) -> Path:
    worktree_path = root / f"issue-{issue_number}"
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: /tmp/git-dir\n", encoding="utf-8")
    return worktree_path


def _target_project_tree(
    worktree_root: Path, *, runner_enabled: bool = True
) -> dict[str, object]:
    workspace_root = worktree_root.parent
    return {
        "version": "1.0.0",
        "default_project": "skeleton",
        "projects": {
            "skeleton": {
                "repo": runner.REPO,
                "checkout_path": str(workspace_root / "repos" / "Skeleton"),
                "worktree_root": str(workspace_root / "worktrees" / "skeleton"),
                "public": True,
                "runner_enabled": True,
                "execution_modes": {"codex_issue_worktree": True},
                "future_parallel_worktrees": True,
                "runtime_approval_required": False,
                "worktree_name_prefix": "skeleton",
            },
            "lumenflow": {
                "repo": "alanua/LumenFlow",
                "checkout_path": str(workspace_root / "repos" / "LumenFlow"),
                "worktree_root": str(worktree_root),
                "public": True,
                "runner_enabled": runner_enabled,
                "execution_modes": {"codex_issue_worktree": True},
                "future_parallel_worktrees": True,
                "runtime_approval_required": True,
                "worktree_name_prefix": "lumenflow",
            },
        },
    }


def test_maintenance_task_bypasses_codex() -> None:
    report = (
        "DONE: Runner host maintenance task completed.\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "success_criteria=met"
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ) as dispatch, mock.patch.object(
        runner, "prepare_issue_worktree"
    ) as prepare_worktree, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(
            _maintenance_issue(
                runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, "Task: use Codex"
            )
        )

    dispatch.assert_called_once()
    prepare_worktree.assert_not_called()
    run_codex.assert_not_called()
    assert set_label.call_args_list == [
        mock.call(145, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(145, runner.LABEL_RUNNING, runner.LABEL_DONE),
    ]


def test_duplicate_blocker_blocks_before_executor_invocation() -> None:
    body = "Expected Output: done\n\n```task\nDo it\n```\n"
    condition = runner.retry_condition_for_issue(
        body,
        runner.ROUTE_CODE_GENERATION,
        None,
        "executor_invocation",
    )
    first = runner.append_retry_fields(
        "BLOCKED: Codex output reported a blocked deliverable.",
        runner.evaluate_retry_policy(condition, []),
    )
    prior = runner.parse_prior_blocked_reports([first])
    second = runner.append_retry_fields(
        "BLOCKED: Codex output reported a blocked deliverable.",
        runner.evaluate_retry_policy(condition, prior),
    )
    issue = {
        "number": 246,
        "title": "Duplicate blocker",
        "body": body,
        "comments": [{"author": {"login": "alanua"}, "body": first}, {"author": {"login": "alanua"}, "body": second}],
    }

    with mock.patch.object(runner, "set_issue_label") as set_label, mock.patch.object(
        runner, "post_issue_comment"
    ) as post, mock.patch.object(runner, "notify_task_finished") as notify, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    prepare.assert_not_called()
    run_codex.assert_not_called()
    set_label.assert_called_once_with(246, runner.LABEL_READY, runner.LABEL_BLOCKED)
    report = post.call_args.args[1]
    assert "NEEDS_OPERATOR: Runner retry policy blocked repeated execution." in report
    assert "reason=repeated_blocker" in report
    assert "next_required_action=DIAGNOSE" in report
    notify.assert_called_once_with(246, "NEEDS_OPERATOR", mock.ANY)


def test_runtime_maintenance_always_routes_runtime_only_and_never_invokes_codex() -> None:
    issue = _maintenance_issue(runner.CHECK_PROJECT_CHECKOUT)
    report = (
        "BLOCKED: Runner host maintenance task did not complete.\n"
        "maintenance_task_id=check_project_checkout\n"
        "reason=checkout_missing\n"
        "success_criteria=not_met"
    )

    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "dispatch_runtime_maintenance_task", return_value=report), mock.patch.object(
        runner, "post_issue_comment"
    ) as post, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    run_codex.assert_not_called()
    assert "route=runtime_only" in post.call_args.args[1]


def test_publish_existing_worktree_routes_publish_only_and_never_invokes_codex() -> None:
    issue = _maintenance_issue(runner.PUBLISH_EXISTING_ISSUE_WORKTREE)
    report = (
        "NEEDS_OPERATOR: Runner host maintenance task needs operator action.\n"
        "maintenance_task_id=publish_existing_issue_worktree\n"
        "reason=missing_operator_approval\n"
        "success_criteria=not_met"
    )

    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "dispatch_runtime_maintenance_task", return_value=report), mock.patch.object(
        runner, "post_issue_comment"
    ) as post, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    run_codex.assert_not_called()
    assert "route=publish_only" in post.call_args.args[1]


def test_code_task_with_empty_expected_output_fails_closed_before_codex() -> None:
    issue = {
        "number": 247,
        "title": "Missing expected output",
        "body": "Expected Output: TBD\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "placeholder_expected_output" in block.call_args.args[1]
    assert block.call_args.kwargs["retry_decision"].retry_attempt == 1
    set_label.assert_not_called()
    run_codex.assert_not_called()


@pytest.mark.parametrize(
    ("body_prefix", "reason"),
    (
        ("", "missing_expected_output"),
        ("Expected Output: {expected_output}\n\n", "placeholder_expected_output"),
        ("Expected Output:\n\n", "empty_expected_output"),
    ),
)
def test_code_task_rejects_missing_empty_or_placeholder_expected_output_before_codex(
    body_prefix: str, reason: str
) -> None:
    issue = {
        "number": 248,
        "title": "Expected output guard",
        "body": f"Selected Project: skeleton\n{body_prefix}```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert reason in block.call_args.args[1]
    prepare.assert_not_called()
    run_codex.assert_not_called()


def test_code_task_accepts_fenced_yaml_expected_output_before_codex(
    tmp_path: Path,
) -> None:
    coordinator = tmp_path / "coordinator"
    issue_path = tmp_path / "worktrees" / "issue-249"
    issue = {
        "number": 249,
        "title": "Yaml expected output",
        "body": "```yaml\nexpected_output:\n- report test totals\n```\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "ready", issue_path)
    ) as prepare, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(0, "")
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    prepare.assert_called_once_with(249, str(coordinator))
    run_codex.assert_called_once()


def test_unverifiable_prior_runner_history_needs_operator_before_codex() -> None:
    issue = {
        "number": 250,
        "title": "Unverifiable comments",
        "body": "Expected Output: done\n\n```task\nDo it\n```",
        "comments": 2,
    }

    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
        runner, "post_issue_comment"
    ) as post, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    report = post.call_args.args[1]
    assert "prior_runner_history_unverifiable" in report
    set_label.assert_called_once_with(250, runner.LABEL_READY, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(250, "NEEDS_OPERATOR", report)
    prepare.assert_not_called()
    run_codex.assert_not_called()


def test_maintenance_task_blocks_missing_closing_task_fence_before_execution() -> None:
    issue = _maintenance_issue(runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME)
    issue["body"] = str(issue["body"]) + "\n\n```task\nTask: use Codex\n"

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "dispatch_runtime_maintenance_task"
    ) as dispatch, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    assert "missing_closing_task_fence" in block.call_args.args[1]
    set_label.assert_not_called()
    dispatch.assert_not_called()
    run_codex.assert_not_called()


def test_missing_maintenance_task_id_is_blocked() -> None:
    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(_maintenance_issue(None))

    block.assert_called_once_with(
        145,
        "Runtime maintenance task id is missing. Use `Maintenance Task ID:`.",
    )
    run_codex.assert_not_called()


def test_unknown_maintenance_task_is_blocked() -> None:
    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(_maintenance_issue("restart_everything"))

    block.assert_called_once_with(
        145,
        "Runtime maintenance task id `restart_everything` is not allowlisted.",
    )
    run_codex.assert_not_called()


def _mempalace_runtime_smoke_body(*, extra: tuple[str, ...] = ()) -> str:
    return str(
        _maintenance_issue(
            runner.MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
            metadata="\n".join(extra),
        )["body"]
    )


def _mempalace_benchmark_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "skeleton.mempalace_synthetic_benchmark.v1",
        "namespace": "skeleton",
        "project_id": "mempalace_synthetic",
        "quality_threshold": 0.8,
        "quality_score": 1.0,
        "resource_report": {
            "aggregate_disk_bytes": 1000,
            "aggregate_ram_bytes": 2000,
            "aggregate_build_ms": 12,
        },
        "checks": [
            {"check": "namespace_isolation", "passed": True},
            {"check": "retrieval_synthetic-door-policy", "passed": True},
            {"check": "deletion_removes_retrieval_result", "passed": True},
            {"check": "clean_rebuild_manifest", "passed": True},
            {"check": "bounded_resources", "passed": True},
        ],
        "decision": "PASS",
        "stable_reasons": [
            "namespace_isolation_proven",
            "deletion_and_rebuild_pass",
            "source_attribution_present",
            "synthetic_quality_threshold_met",
            "bounded_resources_documented",
        ],
    }
    report.update(overrides)
    return report


def _mempalace_smoke_project_tree(checkout_path: Path) -> dict[str, object]:
    return {
        "projects": {
            "skeleton": {
                "repo": runner.REPO,
                "checkout_path": str(checkout_path),
                "public": True,
            }
        }
    }


def _mempalace_smoke_commands(
    checkout_path: Path,
    *,
    benchmark_exit: int = 0,
    benchmark_output: str | None = None,
    worktree_status: str = "",
    head_sha: str = HEAD_SHA,
    origin_main_sha: str = HEAD_SHA,
    benchmark_side_effect: object | None = None,
) -> mock.Mock:
    if benchmark_output is None:
        benchmark_output = json.dumps(_mempalace_benchmark_report(), sort_keys=True)

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        *,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        assert not any(
            part in {"pip", "systemctl", "curl", "wget", "gh", "sqlite3"}
            for part in command
        )
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "main\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, worktree_status
        if command == ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{origin_main_sha}\n"
        if command == ["python3", "scripts/mempalace_synthetic_benchmark.py"]:
            assert Path(cwd or "") == checkout_path
            assert timeout == runner.MEMPALACE_SYNTHETIC_BENCHMARK_TIMEOUT_SECONDS
            if isinstance(benchmark_side_effect, BaseException):
                raise benchmark_side_effect
            return benchmark_exit, benchmark_output or ""
        raise AssertionError(f"unexpected command: {command!r}")

    return mock.Mock(side_effect=run)


def test_mempalace_synthetic_runtime_smoke_happy_path_public_report(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "repos" / "Skeleton"
    checkout_path.mkdir(parents=True)
    (checkout_path / ".git").mkdir()
    run = _mempalace_smoke_commands(checkout_path)

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_mempalace_smoke_project_tree(checkout_path)
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(runner, "run_command", run):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
            str(runner.ROOT),
            _mempalace_runtime_smoke_body(),
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=mempalace_synthetic_runtime_smoke" in report
    assert "runtime_smoke_decision=PASS" in report
    assert "quality_score=1.0" in report
    assert "quality_threshold=0.8" in report
    assert "runtime_smoke_check_count=5" in report
    assert "disk_bytes=1000" in report
    assert "ram_bytes=2000" in report
    assert "build_ms=12" in report
    assert "live_private_ingestion=false" in report
    assert "canonical_write_enabled=false" in report
    assert "services_enabled=false" in report
    assert "ports_enabled=false" in report
    assert "network_provider_enabled=false" in report
    assert "model_credentials_used=false" in report
    assert "runtime_smoke_stable_reason=namespace_isolation_proven" in report
    assert str(checkout_path) not in report
    run.assert_any_call(
        ["python3", "scripts/mempalace_synthetic_benchmark.py"],
        cwd=checkout_path,
        timeout=runner.MEMPALACE_SYNTHETIC_BENCHMARK_TIMEOUT_SECONDS,
    )


def test_mempalace_synthetic_runtime_smoke_task_id_allowlisted() -> None:
    assert runner.MEMPALACE_SYNTHETIC_RUNTIME_SMOKE in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_mempalace_synthetic_runtime_smoke_rejects_issue_controlled_input(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "repos" / "Skeleton"
    run = _mempalace_smoke_commands(checkout_path)
    body = _mempalace_runtime_smoke_body(
        extra=(
            "Command: python3 -m pip install unsafe",
            "Path: /tmp/private.sqlite",
            "Fixture: private_customer_record",
        )
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_mempalace_smoke_project_tree(checkout_path)
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(runner, "run_command", run):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MEMPALACE_SYNTHETIC_RUNTIME_SMOKE, str(runner.ROOT), body
        )

    assert report.startswith("BLOCKED:")
    assert "reason=issue_controlled_input_not_allowed" in report
    assert run.call_count == 0


@pytest.mark.parametrize(
    ("worktree_status", "head_sha", "origin_main_sha", "expected_reason"),
    (
        (" M scripts/runner_poll_github_tasks.py\n", HEAD_SHA, HEAD_SHA, "checkout_dirty"),
        ("", "b" * 40, HEAD_SHA, "checkout_not_current_main"),
    ),
)
def test_mempalace_synthetic_runtime_smoke_blocks_checkout_before_benchmark(
    tmp_path: Path,
    worktree_status: str,
    head_sha: str,
    origin_main_sha: str,
    expected_reason: str,
) -> None:
    checkout_path = tmp_path / "repos" / "Skeleton"
    checkout_path.mkdir(parents=True)
    (checkout_path / ".git").mkdir()
    run = _mempalace_smoke_commands(
        checkout_path,
        worktree_status=worktree_status,
        head_sha=head_sha,
        origin_main_sha=origin_main_sha,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_mempalace_smoke_project_tree(checkout_path)
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(runner, "run_command", run):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
            str(runner.ROOT),
            _mempalace_runtime_smoke_body(),
        )

    assert report.startswith("BLOCKED:")
    assert f"reason={expected_reason}" in report
    assert not any(
        call.args[0] == ["python3", "scripts/mempalace_synthetic_benchmark.py"]
        for call in run.call_args_list
    )


@pytest.mark.parametrize(
    ("benchmark_exit", "benchmark_output", "side_effect", "expected_reason"),
    (
        (0, "", runner.subprocess.TimeoutExpired(cmd=["python3"], timeout=1), "benchmark_timeout"),
        (1, json.dumps(_mempalace_benchmark_report(), sort_keys=True), None, "benchmark_nonzero_exit"),
        (0, "{", None, "malformed_benchmark_json"),
        (0, json.dumps(_mempalace_benchmark_report()) + "\nextra", None, "benchmark_extra_output"),
        (
            0,
            json.dumps(_mempalace_benchmark_report(schema="wrong.schema"), sort_keys=True),
            None,
            "benchmark_schema_mismatch",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(namespace="other"), sort_keys=True),
            None,
            "benchmark_scope_mismatch",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(project_id="other"), sort_keys=True),
            None,
            "benchmark_scope_mismatch",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(decision="CAUTION"), sort_keys=True),
            None,
            "benchmark_decision_not_pass",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(decision="REJECT"), sort_keys=True),
            None,
            "benchmark_decision_not_pass",
        ),
        (
            0,
            json.dumps(
                _mempalace_benchmark_report(checks=[{"check": "namespace_isolation", "passed": False}]),
                sort_keys=True,
            ),
            None,
            "benchmark_failed_check",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(checks=[]), sort_keys=True),
            None,
            "benchmark_missing_checks",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(stable_reasons=[]), sort_keys=True),
            None,
            "benchmark_missing_stable_reason",
        ),
        (
            0,
            json.dumps(_mempalace_benchmark_report(stable_reasons=["secret"]), sort_keys=True),
            None,
            "private_like_benchmark_output",
        ),
    ),
)
def test_mempalace_synthetic_runtime_smoke_blocks_bad_benchmark_results(
    tmp_path: Path,
    benchmark_exit: int,
    benchmark_output: str,
    side_effect: object | None,
    expected_reason: str,
) -> None:
    checkout_path = tmp_path / "repos" / "Skeleton"
    checkout_path.mkdir(parents=True)
    (checkout_path / ".git").mkdir()
    run = _mempalace_smoke_commands(
        checkout_path,
        benchmark_exit=benchmark_exit,
        benchmark_output=benchmark_output,
        benchmark_side_effect=side_effect,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_mempalace_smoke_project_tree(checkout_path)
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(runner, "run_command", run):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
            str(runner.ROOT),
            _mempalace_runtime_smoke_body(),
        )

    assert report.startswith("BLOCKED:")
    assert f"reason={expected_reason}" in report


def test_private_memory_healthcheck_is_allowlisted_and_read_only_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "memory.sqlite"
    config_path = _private_memory_config(tmp_path, db_path)
    monkeypatch.setenv("SKELETON_PRIVATE_MEMORY_CONFIG", str(config_path))

    report = runner.dispatch_runtime_maintenance_task(
        runner.PRIVATE_MEMORY_HEALTHCHECK, str(runner.ROOT)
    )

    assert report.startswith("BLOCKED:")
    assert "maintenance_task_id=private_memory_healthcheck" in report
    assert "private_memory_write_requested=false" in report
    assert "private_memory_writable_when_requested=false" in report
    assert not db_path.exists()
    _assert_private_memory_runner_report_is_public_safe(report, tmp_path)


def test_private_memory_healthcheck_reports_sanitized_ready_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _private_memory_config(tmp_path)
    runner.write_public_heartbeat(
        "synthetic-runner-seed",
        source="synthetic-test",
        config_path=config_path,
    )
    monkeypatch.setenv("SKELETON_PRIVATE_MEMORY_CONFIG", str(config_path))

    report = runner.dispatch_runtime_maintenance_task(
        runner.PRIVATE_MEMORY_HEALTHCHECK, str(runner.ROOT)
    )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=private_memory_healthcheck" in report
    assert "private_memory_status=DONE" in report
    assert "private_memory_db_configured=true" in report
    assert "private_memory_db_openable=true" in report
    assert "private_memory_integrity_ok=true" in report
    assert "private_memory_schema_present=true" in report
    assert "private_memory_writable_when_requested=false" in report
    assert "private_memory_heartbeat_ok=true" in report
    _assert_private_memory_runner_report_is_public_safe(report, tmp_path)


def test_private_memory_healthcheck_write_mode_requires_task_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _private_memory_config(tmp_path)
    monkeypatch.setenv("SKELETON_PRIVATE_MEMORY_CONFIG", str(config_path))
    body = _maintenance_issue(
        runner.PRIVATE_MEMORY_HEALTHCHECK,
        "heartbeat_write=true",
    )["body"]

    report = runner.dispatch_runtime_maintenance_task(
        runner.PRIVATE_MEMORY_HEALTHCHECK, str(runner.ROOT), str(body)
    )

    assert report.startswith("DONE:")
    assert "private_memory_write_requested=true" in report
    assert "private_memory_writable_when_requested=true" in report
    assert "private_memory_heartbeat_ok=true" in report
    _assert_private_memory_runner_report_is_public_safe(report, tmp_path)


def test_private_memory_healthcheck_blocks_invalid_write_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _private_memory_config(tmp_path)
    monkeypatch.setenv("SKELETON_PRIVATE_MEMORY_CONFIG", str(config_path))
    body = _maintenance_issue(
        runner.PRIVATE_MEMORY_HEALTHCHECK,
        "heartbeat_write=please",
    )["body"]

    report = runner.dispatch_runtime_maintenance_task(
        runner.PRIVATE_MEMORY_HEALTHCHECK, str(runner.ROOT), str(body)
    )

    assert report.startswith("BLOCKED:")
    assert "reason=invalid_write_request_value" in report
    assert not (tmp_path / "memory.sqlite").exists()
    _assert_private_memory_runner_report_is_public_safe(report, tmp_path)


def test_private_memory_healthcheck_blocks_connector_privacy_violation() -> None:
    with mock.patch.object(
        runner,
        "healthcheck_private_memory",
        return_value={
            "schema": "skeleton.private_memory.healthcheck.v0",
            "status": "DONE",
            "db_configured": True,
            "db_openable": True,
            "integrity_ok": True,
            "schema_present": True,
            "table_count": 2,
            "writable_when_requested": False,
            "heartbeat_ok": True,
            "error_class": None,
            "next_operator_action": "file:unsafe-value",
        },
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.PRIVATE_MEMORY_HEALTHCHECK, str(runner.ROOT)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=privacy_violation" in report
    assert "file:unsafe-value" not in report


def test_hermes_private_memory_bridge_check_runs_full_sequence_in_order() -> None:
    calls: list[str] = []

    def orient(**kwargs: object) -> dict[str, object]:
        assert kwargs == {"env": runner.os.environ}
        calls.append("orient")
        return {"status": "BLOCKED"}

    def heartbeat(
        heartbeat_id: str, **kwargs: object
    ) -> dict[str, object]:
        calls.append(f"heartbeat:{heartbeat_id}:{kwargs.get('write_enabled', False)}")
        return {
            "status": "DONE" if kwargs.get("write_enabled") is True else "BLOCKED"
        }

    def note(note_id: str, state: str, **kwargs: object) -> dict[str, object]:
        calls.append(f"note:{note_id}:{state}:{kwargs.get('write_enabled', False)}")
        return {"status": "DONE"}

    with mock.patch.object(
        runner, "orient_hermes_private_memory", side_effect=orient
    ), mock.patch.object(
        runner, "write_hermes_private_memory_heartbeat", side_effect=heartbeat
    ), mock.patch.object(
        runner, "record_hermes_private_memory_note", side_effect=note
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.HERMES_PRIVATE_MEMORY_BRIDGE_CHECK, str(runner.ROOT)
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=hermes_private_memory_bridge_check" in report
    assert "hermes_bridge_status=DONE" in report
    assert "orient_status=BLOCKED" in report
    assert "blocked_write_status=BLOCKED" in report
    assert "gated_heartbeat_status=DONE" in report
    assert "gated_note_status=DONE" in report
    assert "public_safe_report_ok=true" in report
    assert "reason=maintenance_step_raised" not in report
    assert calls == [
        "orient",
        "heartbeat:synthetic-runner-hermes-bridge-blocked-write-v0:False",
        "heartbeat:synthetic-runner-hermes-bridge-heartbeat-v0:True",
        "note:synthetic-runner-hermes-bridge-note-v0:runner bridge check:True",
    ]


def test_hermes_private_memory_bridge_exception_returns_safe_aggregate_report(
    tmp_path: Path,
) -> None:
    unsafe_message = (
        f"leaked {tmp_path}/memory.sqlite SELECT private_memory_heartbeat token"
    )
    with mock.patch.object(
        runner,
        "orient_hermes_private_memory",
        side_effect=RuntimeError(unsafe_message),
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.HERMES_PRIVATE_MEMORY_BRIDGE_CHECK, str(runner.ROOT)
        )

    assert report.startswith("BLOCKED:")
    assert "maintenance_task_id=hermes_private_memory_bridge_check" in report
    assert "hermes_bridge_status=BLOCKED" in report
    assert "orient_status=BLOCKED" in report
    assert "blocked_write_status=BLOCKED" in report
    assert "gated_heartbeat_status=BLOCKED" in report
    assert "gated_note_status=BLOCKED" in report
    assert "public_safe_report_ok=true" in report
    assert "error_class=HermesBridgeException" in report
    assert "next_operator_action=safe_operator_review" in report
    assert "success_criteria=not_met" in report
    assert "reason=maintenance_step_raised" not in report
    assert unsafe_message not in report
    assert str(tmp_path) not in report
    assert "memory.sqlite" not in report
    assert "SELECT" not in report
    assert "private_memory_heartbeat" not in report
    assert "token" not in report.lower()


def _graphify_runtime_body(approval: str | None = runner.GRAPHIFY_RUNTIME_APPROVAL) -> str:
    metadata = ""
    if approval is not None:
        metadata = f"Operator Approval: {approval}"
    return str(
        _maintenance_issue(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            metadata=metadata,
        )["body"]
    )


def _graphify_managed_paths(tmp_path: Path) -> tuple[Path, ...]:
    return runner._graphify_managed_profile_paths(tmp_path)


def _graphify_platform_skill(tmp_path: Path, platform: str) -> Path:
    return tmp_path / runner.GRAPHIFY_UPSTREAM_PLATFORM_SKILL_RELATIVE_PATHS[platform][0]


def _graphify_platform_version_marker(tmp_path: Path, platform: str) -> Path:
    return _graphify_platform_skill(tmp_path, platform).parent / runner.GRAPHIFY_VERSION_MARKER_FILENAME


def _write_smoke_graph(
    command: list[str],
    node_count: int = 2,
    edge_count: int = 1,
    *,
    relationship_key: str = "edges",
) -> None:
    output_arg = next(part for part in command if part.startswith("GRAPHIFY_OUT="))
    output_dir = Path(output_arg.split("=", 1)[1])
    output_dir.mkdir(parents=True)
    (output_dir / "graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}),
        encoding="utf-8",
    )
    graph_dir = Path(command[-1]) / "graphify-out"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [{"id": f"node-{index}"} for index in range(node_count)],
                relationship_key: [
                    {"source": "node-0", "target": "node-1"} for _ in range(edge_count)
                ],
            }
        ),
        encoding="utf-8",
    )


def _same_graphify_command(command: list[str], template: tuple[str, ...]) -> bool:
    return Path(command[0]).name == template[0] and command[1:] == list(template[1:])


@pytest.mark.parametrize(
    ("graph", "expected"),
    (
        (
            {
                "nodes": [{"id": "node-0"}, {"id": "node-1"}],
                "edges": [{"source": "node-0", "target": "node-1"}],
            },
            (2, 1),
        ),
        (
            {
                "nodes": [{"id": "node-0"}, {"id": "node-1"}, {"id": "node-2"}],
                "links": [
                    {"source": "node-0", "target": "node-1"},
                    {"source": "node-1", "target": "node-2"},
                ],
            },
            (3, 2),
        ),
        ({"node_count": 4, "edge_count": 3}, (4, 3)),
    ),
)
def test_graphify_graph_json_counts_supports_edges_links_and_counts(
    tmp_path: Path,
    graph: dict[str, object],
    expected: tuple[int, int],
) -> None:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    assert runner._graphify_graph_json_counts(graph_path) == expected


@pytest.mark.parametrize(
    "graph_text",
    (
        "",
        "not json",
        "[]",
        "{}",
        json.dumps({"nodes": [], "edges": []}),
        json.dumps({"nodes": [{"id": "node-0"}]}),
        json.dumps({"node_count": 1}),
        json.dumps({"nodes": [{"id": "node-0"}], "links": "not-a-list-or-count"}),
    ),
)
def test_graphify_graph_json_counts_fails_closed_for_missing_malformed_or_empty_graph(
    tmp_path: Path,
    graph_text: str,
) -> None:
    assert runner._graphify_graph_json_counts(tmp_path / "missing-graph.json") is None

    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph_text, encoding="utf-8")

    counts = runner._graphify_graph_json_counts(graph_path)

    assert counts is None or counts[0] <= 0 or counts[1] <= 0


def _successful_graphify_runtime_command(
    command: list[str],
    cwd: str | Path | None = None,
    *,
    timeout: int | None = None,
) -> tuple[int, str]:
    del cwd
    assert timeout == runner.GRAPHIFY_RUNTIME_COMMAND_TIMEOUT_SECONDS
    if Path(command[0]).name == "uv" and command[1:] == ["--version"]:
        return 0, "uv 0.11.24\n"
    if _same_graphify_command(command, runner.GRAPHIFY_TOOL_INSTALL_COMMAND):
        return 0, ""
    if _same_graphify_command(command, runner.GRAPHIFY_VERSION_COMMAND):
        return 0, "graphify 0.8.44\n"
    if _same_graphify_command(command, runner.GRAPHIFY_INSTALL_HELP_COMMAND):
        return 0, "Usage: graphify install --platform {codex,hermes}\n"
    if _same_graphify_command(command, runner.GRAPHIFY_BUILD_HELP_COMMAND):
        return 0, "Usage: graphify [OPTIONS] FOLDER\n"
    if _same_graphify_command(
        command,
        runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND,
    ) or _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND):
        return 0, ""
    return 2, "unexpected command"


@pytest.fixture(autouse=True)
def _default_graphify_tool_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    uv = tmp_path / "user-bin" / "uv"
    graphify = tmp_path / "user-bin" / "graphify"
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_path_executable",
        lambda name: uv if name == "uv" else None,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        lambda name: graphify if name == "graphify" else uv,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_graphify_cli",
        lambda status_lines, uv_executable: (graphify, None),
    )


def test_graphify_platform_destination_allowlist_matches_pinned_upstream() -> None:
    pinned_upstream = {
        "aider": (Path(".aider") / "graphify" / "SKILL.md",),
        "amp": (Path(".config") / "agents" / "skills" / "graphify" / "SKILL.md",),
        "antigravity": (
            Path(".gemini") / "config" / "skills" / "graphify" / "SKILL.md",
        ),
        "antigravity-windows": (
            Path(".gemini") / "config" / "skills" / "graphify" / "SKILL.md",
        ),
        "claude": (Path(".claude") / "skills" / "graphify" / "SKILL.md",),
        "claw": (Path(".openclaw") / "skills" / "graphify" / "SKILL.md",),
        "codebuddy": (Path(".codebuddy") / "skills" / "graphify" / "SKILL.md",),
        "codex": (Path(".codex") / "skills" / "graphify" / "SKILL.md",),
        "copilot": (Path(".copilot") / "skills" / "graphify" / "SKILL.md",),
        "devin": (
            Path(".config") / "devin" / "skills" / "graphify" / "SKILL.md",
        ),
        "droid": (Path(".factory") / "skills" / "graphify" / "SKILL.md",),
        "hermes": (Path(".hermes") / "skills" / "graphify" / "SKILL.md",),
        "kilo": (Path(".config") / "kilo" / "skills" / "graphify" / "SKILL.md",),
        "kiro": (Path(".kiro") / "skills" / "graphify" / "SKILL.md",),
        "kimi": (Path(".kimi") / "skills" / "graphify" / "SKILL.md",),
        "opencode": (
            Path(".config") / "opencode" / "skills" / "graphify" / "SKILL.md",
        ),
        "pi": (Path(".pi") / "agent" / "skills" / "graphify" / "SKILL.md",),
        "trae": (Path(".trae") / "skills" / "graphify" / "SKILL.md",),
        "trae-cn": (Path(".trae-cn") / "skills" / "graphify" / "SKILL.md",),
        "windows": (Path(".claude") / "skills" / "graphify" / "SKILL.md",),
    }

    assert (
        runner.GRAPHIFY_UPSTREAM_COMMIT
        == "5d053721aba875156cf2a6ddd6953d8beee98147"
    )
    assert runner.GRAPHIFY_UPSTREAM_PLATFORM_SKILL_RELATIVE_PATHS == pinned_upstream
    assert runner.GRAPHIFY_CLAUDE_CONFIG_PLATFORM_NAMES == frozenset(
        ("claude", "windows")
    )


def test_graphify_runtime_requires_exact_operator_approval() -> None:
    with mock.patch.object(runner, "run_command") as run:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(approval=None),
        )

    assert report.startswith("BLOCKED:")
    assert "maintenance_task_id=install_graphify_runtime" in report
    assert "reason=missing_operator_approval" in report
    run.assert_not_called()


def test_graphify_runtime_uses_verified_0844_commands_and_retains_snapshot(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")
    snapshot_parent = tmp_path / "snapshots"

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        commands.append(command)
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    def smoke(command: list[str]) -> tuple[int, str]:
        commands.append(command)
        _write_smoke_graph(command, node_count=3, edge_count=2)
        return 0, "synthetic graph built\n"

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=snapshot_parent
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_run_graphify_smoke_command", side_effect=smoke
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("DONE:")
    assert "approval_status=verified" in report
    assert "recovery_snapshot_status=retained" in report
    assert "synthetic_smoke_timeout_seconds=45" in report
    assert "synthetic_graph_node_count=3" in report
    assert "synthetic_graph_edge_count=2" in report
    assert "managed_version_marker_count=" in report
    assert any(snapshot_parent.iterdir())
    assert any(
        _same_graphify_command(command, runner.GRAPHIFY_TOOL_INSTALL_COMMAND)
        for command in commands
    )
    assert any(
        _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND)
        for command in commands
    )
    assert any(
        _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND)
        for command in commands
    )
    smoke_commands = [command for command in commands if command[:2] == ["env", "-i"]]
    assert len(smoke_commands) == 1
    smoke_command = smoke_commands[0]
    assert Path(smoke_command[-2]).name == "graphify"
    assert Path(smoke_command[-2]).is_absolute()
    assert "GRAPHIFY_OUT=" in " ".join(smoke_command)
    assert all("OPENAI" not in part for part in smoke_command)
    assert all("ANTHROPIC" not in part for part in smoke_command)
    command_words = [" ".join(command) for command in commands]
    assert all("install-skills" not in command for command in command_words)
    assert all(" ingest " not in f" {command} " for command in command_words)
    assert all("--source" not in command for command in command_words)
    assert all("--extractor" not in command for command in command_words)
    assert all("--no-semantic" not in command for command in command_words)
    assert str(tmp_path) not in report
    assert "synthetic graph built" not in report


def test_graphify_runtime_accepts_node_link_links_from_corpus_output(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    snapshot_parent = tmp_path / "snapshots"

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        commands.append(command)
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    def smoke(command: list[str]) -> tuple[int, str]:
        commands.append(command)
        _write_smoke_graph(command, node_count=2, edge_count=3, relationship_key="links")
        return 0, '{"graph": "raw smoke graph content must not leak"}'

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=snapshot_parent
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_run_graphify_smoke_command", side_effect=smoke
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("DONE:")
    assert "synthetic_graph_node_count=2" in report
    assert "synthetic_graph_edge_count=3" in report
    assert "raw smoke graph content must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_reuses_exact_path_uv_without_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_uv = tmp_path / "system-bin" / "uv"
    commands: list[list[str]] = []
    status_lines: list[str] = []
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_path_executable",
        lambda name: path_uv if name == "uv" else None,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        lambda name: pytest.fail("user uv should not be checked"),
    )

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd, kwargs
        commands.append(command)
        if command == [str(path_uv), "--version"]:
            return 0, "uv 0.11.24\n"
        return 2, "unexpected command"

    with mock.patch.object(runner, "run_command", side_effect=run):
        uv_executable, reason = runner._graphify_resolve_verified_uv(status_lines)

    assert uv_executable == path_uv
    assert reason is None
    assert commands == [[str(path_uv), "--version"]]
    assert "step=verify_path_uv status=done" in status_lines


def test_graphify_runtime_preserves_wrong_path_uv_and_reuses_exact_user_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_uv = tmp_path / "system-bin" / "uv"
    user_uv = tmp_path / "user-bin" / "uv"
    commands: list[list[str]] = []
    status_lines: list[str] = []
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_path_executable",
        lambda name: path_uv if name == "uv" else None,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        lambda name: user_uv if name == "uv" else None,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_bootstrap_uv",
        lambda status_lines: pytest.fail("wrong PATH uv must not force bootstrap"),
    )

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd, kwargs
        commands.append(command)
        if command == [str(path_uv), "--version"]:
            return 0, "uv 0.11.23\n"
        if command == [str(user_uv), "--version"]:
            return 0, "uv 0.11.24\n"
        return 2, "unexpected command"

    with mock.patch.object(runner, "run_command", side_effect=run):
        uv_executable, reason = runner._graphify_resolve_verified_uv(status_lines)

    assert uv_executable == user_uv
    assert reason is None
    assert commands == [[str(path_uv), "--version"], [str(user_uv), "--version"]]
    assert "step=verify_path_uv status=failed" in status_lines
    assert "step=verify_user_uv status=done" in status_lines


def test_graphify_runtime_reuses_exact_user_uv_outside_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_uv = tmp_path / "user-bin" / "uv"
    commands: list[list[str]] = []
    status_lines: list[str] = []
    monkeypatch.setattr(runner, "_graphify_resolve_path_executable", lambda name: None)
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        lambda name: user_uv if name == "uv" else None,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_bootstrap_uv",
        lambda status_lines: pytest.fail("exact user uv should be reused"),
    )

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd, kwargs
        commands.append(command)
        if command == [str(user_uv), "--version"]:
            return 0, "uv 0.11.24\n"
        return 2, "unexpected command"

    with mock.patch.object(runner, "run_command", side_effect=run):
        uv_executable, reason = runner._graphify_resolve_verified_uv(status_lines)

    assert uv_executable == user_uv
    assert reason is None
    assert commands == [[str(user_uv), "--version"]]
    assert "step=verify_user_uv status=done" in status_lines


def test_graphify_runtime_blocks_wrong_user_uv_after_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_uv = tmp_path / "user-bin" / "uv"
    commands: list[list[str]] = []
    status_lines: list[str] = []
    monkeypatch.setattr(runner, "_graphify_resolve_path_executable", lambda name: None)
    monkeypatch.setattr(runner, "_graphify_resolve_user_script", lambda name: None)
    monkeypatch.setattr(
        runner,
        "_graphify_bootstrap_uv",
        lambda status_lines: (user_uv, None),
    )

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd, kwargs
        commands.append(command)
        if command == [str(user_uv), "--version"]:
            return 0, "uv 0.11.23\n"
        return 2, "unexpected command"

    with mock.patch.object(runner, "run_command", side_effect=run):
        uv_executable, reason = runner._graphify_resolve_verified_uv(status_lines)

    assert uv_executable is None
    assert reason == runner.GRAPHIFY_UV_VERSION_MISMATCH_REASON
    assert commands == [[str(user_uv), "--version"]]
    assert "step=verify_bootstrapped_user_uv status=failed" in status_lines


def test_graphify_runtime_reports_unresolved_path_uv_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_uv = tmp_path / "missing-bin" / "uv"
    status_lines: list[str] = []
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_path_executable",
        lambda name: path_uv if name == "uv" else None,
    )
    monkeypatch.setattr(runner, "_graphify_resolve_user_script", lambda name: None)
    monkeypatch.setattr(
        runner,
        "_graphify_bootstrap_uv",
        lambda status_lines: pytest.fail("unresolved PATH uv should block"),
    )

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del command, cwd, kwargs
        raise FileNotFoundError("raw executable detail must not leak")

    with mock.patch.object(runner, "run_command", side_effect=run):
        uv_executable, reason = runner._graphify_resolve_verified_uv(status_lines)

    assert uv_executable is None
    assert reason == "graphify_tool_command_unavailable"
    assert "step=verify_path_uv status=failed" in status_lines


def test_graphify_runtime_command_timeout_has_public_safe_reason() -> None:
    def run(*args: object, **kwargs: object) -> tuple[int, str]:
        del args, kwargs
        raise runner.subprocess.TimeoutExpired(cmd=["uv"], timeout=1)

    with mock.patch.object(runner, "run_command", side_effect=run):
        code, output, reason = runner._run_graphify_runtime_command(
            ["uv", "--version"],
            command_unavailable_reason="graphify_tool_command_unavailable",
        )

    assert code is None
    assert output == ""
    assert reason == runner.GRAPHIFY_COMMAND_TIMEOUT_REASON


def test_graphify_managed_paths_discover_existing_allowlisted_version_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_skill = _graphify_platform_skill(tmp_path, "claude")
    claude_skill.parent.mkdir(parents=True)
    claude_skill.write_text("existing claude skill\n", encoding="utf-8")
    opencode_skill = _graphify_platform_skill(tmp_path, "opencode")
    opencode_skill.parent.mkdir(parents=True)
    opencode_skill.write_text("existing opencode skill\n", encoding="utf-8")
    claude_config_dir = tmp_path / "custom-claude-config"
    claude_config_skill = claude_config_dir / "skills" / "graphify" / "SKILL.md"
    claude_config_skill.parent.mkdir(parents=True)
    claude_config_skill.write_text("existing custom claude skill\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config_dir))
    gemini_skill = tmp_path / ".gemini" / "skills" / "graphify" / "SKILL.md"
    gemini_skill.parent.mkdir(parents=True)
    gemini_skill.write_text("not refreshed through platform config\n", encoding="utf-8")
    unrelated_skill = tmp_path / ".unrelated" / "skills" / "graphify" / "SKILL.md"
    unrelated_skill.parent.mkdir(parents=True)
    unrelated_skill.write_text("not graphify managed\n", encoding="utf-8")

    managed_paths = runner._graphify_managed_profile_paths(tmp_path)

    assert _graphify_platform_version_marker(tmp_path, "claude") in managed_paths
    assert _graphify_platform_version_marker(tmp_path, "opencode") in managed_paths
    assert claude_config_skill.parent / runner.GRAPHIFY_VERSION_MARKER_FILENAME in managed_paths
    assert gemini_skill.parent / runner.GRAPHIFY_VERSION_MARKER_FILENAME not in managed_paths
    assert unrelated_skill.parent / runner.GRAPHIFY_VERSION_MARKER_FILENAME not in managed_paths
    assert all(".graphify-version" not in str(path) for path in managed_paths)
    assert all("graphify.version" not in str(path) for path in managed_paths)
    assert all(path.name != "VERSION" for path in managed_paths)
    assert all(path.name != "metadata.json" for path in managed_paths)


def test_graphify_runtime_preflight_blocks_before_profile_mutation(tmp_path: Path) -> None:
    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd
        if Path(command[0]).name == "uv" and command[1:] == ["--version"]:
            return 0, "uv 0.11.24\n"
        if _same_graphify_command(command, runner.GRAPHIFY_TOOL_INSTALL_COMMAND):
            return 0, ""
        if _same_graphify_command(command, runner.GRAPHIFY_VERSION_COMMAND):
            return 0, "graphify 0.8.44\n"
        if _same_graphify_command(command, runner.GRAPHIFY_INSTALL_HELP_COMMAND):
            return 0, "Usage: graphify install\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ) as run_command, mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    commands = [call.args[0] for call in run_command.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=graphify_cli_contract_unverified" in report
    assert not any(
        _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND)
        for command in commands
    )
    assert not any(
        _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND)
        for command in commands
    )
    backup.assert_not_called()


def test_graphify_runtime_reports_missing_uv_before_backup(tmp_path: Path) -> None:
    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del command, cwd
        raise FileNotFoundError("raw uv path must not leak")

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_tool_command_unavailable" in report
    assert "rollback_status=not_needed" in report
    assert "raw uv path must not leak" not in report
    backup.assert_not_called()


def test_graphify_runtime_does_not_run_bare_uv_when_resolution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_graphify_resolve_path_executable", lambda name: None)
    monkeypatch.setattr(runner, "_graphify_resolve_user_script", lambda name: None)
    monkeypatch.setattr(
        runner,
        "_graphify_bootstrap_uv",
        lambda status_lines: (None, "graphify_tool_command_unavailable"),
    )

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", return_value=(0, "bare uv would have succeeded")
    ) as run, mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_tool_command_unavailable" in report
    run.assert_not_called()
    backup.assert_not_called()


def test_graphify_runtime_reports_missing_pip_before_user_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python_executable = tmp_path / "python"
    python_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    python_executable.chmod(0o700)
    monkeypatch.setattr(runner, "_graphify_resolve_path_executable", lambda name: None)
    monkeypatch.setattr(runner, "_graphify_resolve_user_script", lambda name: None)
    monkeypatch.setattr(runner, "_graphify_python_executable", lambda: python_executable)
    monkeypatch.setattr(
        runner,
        "_run_graphify_python_command",
        lambda command, timeout, cwd=None: (1, "No module named pip", None),
    )

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", return_value=(0, "bare uv would have succeeded")
    ) as run, mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert f"reason={runner.GRAPHIFY_UV_PACKAGE_TOOLING_UNAVAILABLE_REASON}" in report
    assert "step=verify_python_package_tooling status=failed" in report
    assert "step=bootstrap_pinned_uv_tool" not in report
    run.assert_not_called()
    backup.assert_not_called()


def test_graphify_runtime_bootstrap_falls_back_only_after_externally_managed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python_executable = tmp_path / "python"
    python_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    python_executable.chmod(0o700)
    user_uv = tmp_path / "user-bin" / "uv"
    commands: list[list[str]] = []
    status_lines: list[str] = []
    monkeypatch.setattr(runner, "_graphify_python_executable", lambda: python_executable)
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        lambda name: user_uv if name == "uv" else None,
    )

    def run_python(
        command: list[str],
        timeout: int,
        cwd: str | Path | None = None,
    ) -> tuple[int, str, str | None]:
        del timeout, cwd
        commands.append(command)
        if command[-2:] == ["pip", "--version"]:
            return 0, "pip 24.0\n", None
        if command[:4] == [str(python_executable), "-m", "pip", "install"]:
            if runner.GRAPHIFY_PIP_BREAK_SYSTEM_PACKAGES_FLAG in command:
                return 0, "", None
            return (
                1,
                "error: externally-managed-environment\n"
                "hint: pass --break-system-packages to override\n",
                None,
            )
        return 2, "unexpected command", None

    monkeypatch.setattr(runner, "_run_graphify_python_command", run_python)

    uv_executable, reason = runner._graphify_bootstrap_uv(status_lines)

    assert uv_executable == user_uv
    assert reason is None
    assert commands == [
        [str(python_executable), "-m", "pip", "--version"],
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--user",
            "--disable-pip-version-check",
            "--no-input",
            f"uv=={runner.UV_PINNED_VERSION}",
        ],
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--user",
            "--disable-pip-version-check",
            "--no-input",
            runner.GRAPHIFY_PIP_BREAK_SYSTEM_PACKAGES_FLAG,
            f"uv=={runner.UV_PINNED_VERSION}",
        ],
    ]
    assert "step=bootstrap_pinned_uv_tool status=externally_managed" in status_lines
    assert "step=bootstrap_pinned_uv_tool_fallback status=done" in status_lines
    assert "step=bootstrap_pinned_uv_tool status=done" in status_lines


def test_graphify_runtime_bootstrap_stops_without_fallback_for_other_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python_executable = tmp_path / "python"
    python_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    python_executable.chmod(0o700)
    commands: list[list[str]] = []
    status_lines: list[str] = []
    monkeypatch.setattr(runner, "_graphify_python_executable", lambda: python_executable)
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        lambda name: pytest.fail("uv should not resolve after failed bootstrap"),
    )

    def run_python(
        command: list[str],
        timeout: int,
        cwd: str | Path | None = None,
    ) -> tuple[int, str, str | None]:
        del timeout, cwd
        commands.append(command)
        if command[-2:] == ["pip", "--version"]:
            return 0, "pip 24.0\n", None
        if command[:4] == [str(python_executable), "-m", "pip", "install"]:
            return 1, "Could not find a version that satisfies the requirement\n", None
        return 2, "unexpected command", None

    monkeypatch.setattr(runner, "_run_graphify_python_command", run_python)

    uv_executable, reason = runner._graphify_bootstrap_uv(status_lines)

    assert uv_executable is None
    assert reason == "graphify_uv_bootstrap_failed"
    assert commands == [
        [str(python_executable), "-m", "pip", "--version"],
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--user",
            "--disable-pip-version-check",
            "--no-input",
            f"uv=={runner.UV_PINNED_VERSION}",
        ],
    ]
    assert runner.GRAPHIFY_PIP_BREAK_SYSTEM_PACKAGES_FLAG not in commands[-1]
    assert "step=bootstrap_pinned_uv_tool status=failed" in status_lines
    assert not any("bootstrap_pinned_uv_tool_fallback" in line for line in status_lines)


def test_graphify_runtime_reports_missing_graphify_during_preflight(
    tmp_path: Path,
) -> None:
    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd
        if Path(command[0]).name == "uv" and command[1:] == ["--version"]:
            return 0, "uv 0.11.24\n"
        if _same_graphify_command(command, runner.GRAPHIFY_TOOL_INSTALL_COMMAND):
            return 0, ""
        raise FileNotFoundError("raw graphify path must not leak")

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_cli_command_unavailable" in report
    assert "rollback_status=not_needed" in report
    assert "raw graphify path must not leak" not in report
    backup.assert_not_called()


def test_graphify_user_script_resolution_rejects_unsafe_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_dir = tmp_path / "user-bin"
    scripts_dir.mkdir()
    out_of_bound_target = tmp_path / "outside-uv"
    out_of_bound_target.write_text("#!/bin/sh\n", encoding="utf-8")
    out_of_bound_target.chmod(0o700)
    (scripts_dir / "uv").symlink_to(out_of_bound_target)
    monkeypatch.setattr(runner, "_graphify_user_scripts_dir", lambda: scripts_dir)
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_user_script",
        ORIGINAL_GRAPHIFY_RESOLVE_USER_SCRIPT,
    )

    assert runner._graphify_resolve_user_script("uv") is None

    (scripts_dir / "uv").unlink()
    (scripts_dir / "uv").symlink_to(scripts_dir / "missing-uv")
    assert runner._graphify_resolve_user_script("uv") is None

    (scripts_dir / "uv").unlink()
    safe_target = scripts_dir / "uv-real"
    safe_target.write_text("#!/bin/sh\n", encoding="utf-8")
    safe_target.chmod(0o700)
    (scripts_dir / "uv").symlink_to(safe_target)
    assert runner._graphify_resolve_user_script("uv") == safe_target.resolve()


@pytest.mark.skipif(os.name == "nt", reason="uv symlink policy is Unix-specific")
def test_graphify_runtime_accepts_valid_uv_managed_graphify_symlink(
    tmp_path: Path,
) -> None:
    tool_bin = tmp_path / "tool-bin"
    tool_root = tmp_path / "tools"
    graphify_target = tool_root / "graphifyy" / "bin" / "graphify"
    tool_bin.mkdir()
    graphify_target.parent.mkdir(parents=True)
    graphify_target.write_text("#!/bin/sh\n", encoding="utf-8")
    graphify_target.chmod(0o700)
    (tool_bin / "graphify").symlink_to(graphify_target)

    assert (
        runner._graphify_resolve_uv_tool_graphify(tool_bin, tool_root)
        == graphify_target.resolve()
    )


@pytest.mark.skipif(os.name == "nt", reason="uv symlink policy is Unix-specific")
def test_graphify_runtime_rejects_uv_managed_graphify_symlink_escape(
    tmp_path: Path,
) -> None:
    tool_bin = tmp_path / "tool-bin"
    tool_root = tmp_path / "tools"
    escaped_target = tmp_path / "outside" / "graphify"
    tool_bin.mkdir()
    escaped_target.parent.mkdir()
    escaped_target.write_text("#!/bin/sh\n", encoding="utf-8")
    escaped_target.chmod(0o700)
    (tool_bin / "graphify").symlink_to(escaped_target)

    assert runner._graphify_resolve_uv_tool_graphify(tool_bin, tool_root) is None


@pytest.mark.skipif(os.name == "nt", reason="uv symlink policy is Unix-specific")
def test_graphify_runtime_rejects_broken_and_nonexecutable_graphify_candidate(
    tmp_path: Path,
) -> None:
    tool_bin = tmp_path / "tool-bin"
    tool_root = tmp_path / "tools"
    tool_bin.mkdir()
    tool_root.mkdir()
    candidate = tool_bin / "graphify"
    candidate.symlink_to(tool_root / "missing" / "graphify")
    assert runner._graphify_resolve_uv_tool_graphify(tool_bin, tool_root) is None

    candidate.unlink()
    target = tool_root / "graphifyy" / "bin" / "graphify"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o600)
    candidate.symlink_to(target)
    assert runner._graphify_resolve_uv_tool_graphify(tool_bin, tool_root) is None


@pytest.mark.parametrize(
    "output",
    (
        "",
        "~/tool-bin\n",
        "relative/tool/bin\n",
        "/tmp/tool-bin\n/tmp/tools\n",
        " /tmp/tool-bin\n",
        "/tmp/tool-bin \n",
    ),
)
def test_graphify_runtime_rejects_malformed_uv_tool_dir_output(output: str) -> None:
    assert runner._graphify_parse_uv_tool_dir_output(output) is None


@pytest.mark.skipif(os.name == "nt", reason="uv symlink policy is Unix-specific")
def test_graphify_runtime_uses_exact_verified_uv_for_tool_dir_queries(
    tmp_path: Path,
) -> None:
    uv_executable = tmp_path / "verified" / "uv"
    tool_bin = tmp_path / "tool-bin"
    tool_root = tmp_path / "tools"
    graphify_target = tool_root / "graphifyy" / "bin" / "graphify"
    commands: list[list[str]] = []
    status_lines: list[str] = []
    uv_executable.parent.mkdir()
    uv_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    uv_executable.chmod(0o700)
    tool_bin.mkdir()
    graphify_target.parent.mkdir(parents=True)
    graphify_target.write_text("#!/bin/sh\n", encoding="utf-8")
    graphify_target.chmod(0o700)
    (tool_bin / "graphify").symlink_to(graphify_target)

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd, kwargs
        commands.append(command)
        if command == [
            str(uv_executable),
            *runner.GRAPHIFY_UV_TOOL_BIN_DIR_COMMAND[1:],
        ]:
            return 0, f"{tool_bin}\n"
        if command == [
            str(uv_executable),
            *runner.GRAPHIFY_UV_TOOL_ROOT_DIR_COMMAND[1:],
        ]:
            return 0, f"{tool_root}\n"
        return 2, "unexpected command"

    with mock.patch.object(runner, "run_command", side_effect=run), mock.patch.object(
        runner,
        "_graphify_resolve_graphify_cli",
        ORIGINAL_GRAPHIFY_RESOLVE_GRAPHIFY_CLI,
    ):
        graphify_executable, reason = runner._graphify_resolve_graphify_cli(
            status_lines,
            uv_executable,
        )

    assert graphify_executable == graphify_target.resolve()
    assert reason is None
    assert commands == [
        [str(uv_executable), *runner.GRAPHIFY_UV_TOOL_BIN_DIR_COMMAND[1:]],
        [str(uv_executable), *runner.GRAPHIFY_UV_TOOL_ROOT_DIR_COMMAND[1:]],
    ]
    assert "step=resolve_graphify_tool_bin_dir status=done" in status_lines
    assert "step=resolve_graphify_tool_root_dir status=done" in status_lines


def test_graphify_runtime_blocks_before_profile_mutation_on_tool_dir_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv_executable = tmp_path / "user-bin" / "uv"
    uv_executable.parent.mkdir()
    uv_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    uv_executable.chmod(0o700)
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_path_executable",
        lambda name: uv_executable if name == "uv" else None,
    )
    monkeypatch.setattr(
        runner,
        "_graphify_resolve_graphify_cli",
        ORIGINAL_GRAPHIFY_RESOLVE_GRAPHIFY_CLI,
    )

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd, kwargs
        if command == [str(uv_executable), "--version"]:
            return 0, "uv 0.11.24\n"
        if _same_graphify_command(command, runner.GRAPHIFY_TOOL_INSTALL_COMMAND):
            return 0, ""
        if command == [
            str(uv_executable),
            *runner.GRAPHIFY_UV_TOOL_BIN_DIR_COMMAND[1:],
        ]:
            return 0, "relative/tool-bin\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_tool_command_unavailable" in report
    assert "step=resolve_graphify_tool_bin_dir status=failed" in report
    assert "rollback_status=not_needed" in report
    backup.assert_not_called()


@pytest.mark.parametrize(
    ("error", "reason"),
    (
        (PermissionError("raw permission detail must not leak"), "graphify_command_permission_denied"),
        (runner.subprocess.SubprocessError("raw subprocess detail must not leak"), "graphify_command_launch_failed"),
        (OSError("raw os detail must not leak"), "graphify_command_launch_failed"),
    ),
)
def test_graphify_runtime_reports_public_safe_launch_failures_before_backup(
    tmp_path: Path,
    error: Exception,
    reason: str,
) -> None:
    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del command, cwd
        raise error

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    assert "rollback_status=not_needed" in report
    assert "raw " not in report
    backup.assert_not_called()


def test_graphify_runtime_unexpected_prebackup_failure_is_public_safe(
    tmp_path: Path,
) -> None:
    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        del cwd
        if _same_graphify_command(command, runner.GRAPHIFY_TOOL_INSTALL_COMMAND):
            return 0, ""
        raise RuntimeError("raw unexpected detail must not leak")

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=_graphify_managed_paths(tmp_path)
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_backup_graphify_profiles"
    ) as backup:
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_runtime_unexpected_failure" in report
    assert "rollback_status=not_needed" in report
    assert "raw unexpected detail must not leak" not in report
    backup.assert_not_called()


def test_graphify_runtime_restores_after_codex_install_failure(tmp_path: Path) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("mutated\n", encoding="utf-8")
            return 1, "codex failure must not leak"
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=codex_skill_install_failed" in report
    assert "rollback_status=restored" in report
    assert (codex_skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert "codex failure must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restores_after_unexpected_codex_mutation_failure(
    tmp_path: Path,
) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("mutated\n", encoding="utf-8")
            raise RuntimeError("raw codex failure must not leak")
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_runtime_unexpected_failure" in report
    assert "rollback_status=restored" in report
    assert (codex_skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert "raw codex failure must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restores_after_hermes_install_failure(tmp_path: Path) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("mutated\n", encoding="utf-8")
            managed_paths[2].mkdir(parents=True)
            return 0, ""
        if _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND):
            (managed_paths[2] / "SKILL.md").write_text("partial\n", encoding="utf-8")
            return 1, "hermes failure must not leak"
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=hermes_skill_install_failed" in report
    assert "rollback_status=restored" in report
    assert (codex_skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert not managed_paths[2].exists()
    assert "hermes failure must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restores_after_unexpected_hermes_mutation_failure(
    tmp_path: Path,
) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("codex mutated\n", encoding="utf-8")
            return 0, ""
        if _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND):
            managed_paths[2].mkdir(parents=True)
            (managed_paths[2] / "SKILL.md").write_text("hermes partial\n", encoding="utf-8")
            raise RuntimeError("raw hermes failure must not leak")
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_runtime_unexpected_failure" in report
    assert "rollback_status=restored" in report
    assert (codex_skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert not managed_paths[2].exists()
    assert "raw hermes failure must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restores_third_platform_version_stamp_after_later_failure(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    codex_skill = _graphify_platform_skill(home, "codex")
    codex_skill.parent.mkdir(parents=True)
    codex_skill.write_text("original codex\n", encoding="utf-8")
    third_skill = _graphify_platform_skill(home, "opencode")
    third_skill.parent.mkdir(parents=True)
    third_skill.write_text("existing opencode\n", encoding="utf-8")
    third_marker = _graphify_platform_version_marker(home, "opencode")
    third_marker.write_text("graphify 0.8.43\n", encoding="utf-8")
    managed_paths = _graphify_managed_paths(home)

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            codex_skill.write_text("mutated codex\n", encoding="utf-8")
            third_marker.write_text("graphify 0.8.44\n", encoding="utf-8")
            return 0, ""
        if _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND):
            _graphify_platform_skill(home, "hermes").parent.mkdir(parents=True)
            _graphify_platform_skill(home, "hermes").write_text(
                "partial hermes\n",
                encoding="utf-8",
            )
            return 1, "hermes failure must not leak"
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=hermes_skill_install_failed" in report
    assert "rollback_status=restored" in report
    assert codex_skill.read_text(encoding="utf-8") == "original codex\n"
    assert third_marker.read_text(encoding="utf-8") == "graphify 0.8.43\n"
    assert not _graphify_platform_skill(home, "hermes").exists()
    assert "hermes failure must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restore_failure_reports_failed_safely(tmp_path: Path) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("mutated\n", encoding="utf-8")
            return 1, "raw install output must not leak"
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_remove_graphify_profile_path", return_value=False
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=codex_skill_install_failed" in report
    assert "rollback_status=failed" in report
    assert "raw install output must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restore_exception_reports_failed_safely(
    tmp_path: Path,
) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("mutated\n", encoding="utf-8")
            raise RuntimeError("raw mutation detail must not leak")
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner,
        "_restore_graphify_profiles",
        side_effect=RuntimeError("raw restore detail must not leak"),
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_runtime_unexpected_failure" in report
    assert "rollback_status=failed" in report
    assert "raw mutation detail must not leak" not in report
    assert "raw restore detail must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restores_when_smoke_graph_json_is_empty(tmp_path: Path) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("codex mutated\n", encoding="utf-8")
        if _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND):
            managed_paths[2].mkdir(parents=True)
            (managed_paths[2] / "SKILL.md").write_text("hermes partial\n", encoding="utf-8")
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    def smoke(command: list[str]) -> tuple[int, str]:
        _write_smoke_graph(command, node_count=1, edge_count=0)
        return 0, "smoke output must not leak"

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_run_graphify_smoke_command", side_effect=smoke
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=synthetic_graph_json_empty" in report
    assert "rollback_status=restored" in report
    assert (codex_skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert not managed_paths[2].exists()
    assert "smoke output must not leak" not in report
    assert str(tmp_path) not in report


def test_graphify_runtime_restores_after_smoke_subprocess_launch_failure(
    tmp_path: Path,
) -> None:
    managed_paths = _graphify_managed_paths(tmp_path / "home")
    codex_skill = managed_paths[0]
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    def run(
        command: list[str],
        cwd: str | Path | None = None,
        **kwargs: object,
    ) -> tuple[int, str]:
        if _same_graphify_command(command, runner.GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND):
            (codex_skill / "SKILL.md").write_text("codex mutated\n", encoding="utf-8")
        if _same_graphify_command(command, runner.GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND):
            managed_paths[2].mkdir(parents=True)
            (managed_paths[2] / "SKILL.md").write_text("hermes partial\n", encoding="utf-8")
        return _successful_graphify_runtime_command(command, cwd, **kwargs)

    with mock.patch.object(
        runner, "_graphify_managed_profile_paths", return_value=managed_paths
    ), mock.patch.object(
        runner, "_graphify_private_snapshot_parent", return_value=tmp_path / "snapshots"
    ), mock.patch.object(
        runner, "run_command", side_effect=run
    ), mock.patch.object(
        runner, "_run_graphify_smoke_command", return_value=(125, "")
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.INSTALL_GRAPHIFY_RUNTIME,
            str(runner.ROOT),
            _graphify_runtime_body(),
        )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_command_launch_failed" in report
    assert "rollback_status=restored" in report
    assert (codex_skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert not managed_paths[2].exists()
    assert str(tmp_path) not in report


def test_graphify_smoke_command_has_bounded_timeout() -> None:
    def timeout_run(*args: object, **kwargs: object) -> object:
        assert kwargs["timeout"] == runner.GRAPHIFY_SMOKE_TIMEOUT_SECONDS
        raise runner.subprocess.TimeoutExpired(cmd=["graphify"], timeout=1)

    with mock.patch.object(runner.subprocess, "run", side_effect=timeout_run):
        code, output = runner._run_graphify_smoke_command(["graphify", "synthetic"])

    assert code == 124
    assert output == ""


def test_issue_worktree_publish_inspection_is_allowlisted_and_bypasses_codex(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    issue = _maintenance_issue(
        runner.INSPECT_ISSUE_WORKTREE_FOR_PUBLISH,
        "git push origin runner/issue-123\ngh pr create",
        metadata="\n".join(
            (
                f"Repository: {runner.REPO}",
                "Source Issue: 123",
                "Expected Branch: runner/issue-123",
                "Allowed Files:",
                "- scripts/runner_poll_github_tasks.py",
            )
        ),
    )

    assert runner.INSPECT_ISSUE_WORKTREE_FOR_PUBLISH in runner.RUNTIME_MAINTENANCE_TASK_IDS
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path),
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "branch", "--show-current"],
        ["git", "remote", "get-url", "origin"],
        ["git", "diff", "--name-only", "HEAD", "--"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    run_codex.assert_not_called()
    assert set_label.call_args_list[-1] == mock.call(
        145, runner.LABEL_RUNNING, runner.LABEL_DONE
    )


def test_publish_existing_issue_worktree_is_allowlisted_and_creates_draft_pr(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            untracked_files=(".codex/session.json",),
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert runner.PUBLISH_EXISTING_ISSUE_WORKTREE in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert "maintenance_task_id=publish_existing_issue_worktree" in report
    assert "repository=alanua/Skeleton" in report
    assert "base_branch=main" in report
    assert "draft_pr=true" in report
    assert "existing_pr_lookup=existing_pr_not_found" in report
    assert ["git", "add", "--", "scripts/runner_poll_github_tasks.py"] in commands
    assert not any(".codex/session.json" in command for command in commands)
    assert commands[-1][-1] == "--draft"


def test_publish_existing_issue_worktree_reuses_existing_pr_when_lookup_succeeds(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_url=PR_URL,
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "existing_pr_lookup=existing_pr_found" in report
    assert f"existing_pr_url={PR_URL}" in report
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


def test_publish_existing_issue_worktree_lookup_unavailable_without_override_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("NEEDS_OPERATOR:")
    assert "existing_pr_lookup=existing_pr_lookup_unavailable" in report
    assert "reason=publish_override_missing" in report
    assert "reason=existing_pr_lookup_unavailable" in report
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


def test_publish_existing_issue_worktree_valid_override_publishes_when_remote_absent(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
            untracked_files=(
                ".codex/session.json",
                "tests/test_runner_poll_github_tasks.py",
            ),
            validated_publish_files=(
                "scripts/runner_poll_github_tasks.py",
                "tests/test_runner_poll_github_tasks.py",
            ),
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body(
                allowed_files=(
                    "scripts/runner_poll_github_tasks.py",
                    "tests/test_runner_poll_github_tasks.py",
                ),
                publish_override=_valid_publish_existing_override(
                    allowed_files=(
                        "scripts/runner_poll_github_tasks.py",
                        "tests/test_runner_poll_github_tasks.py",
                    )
                ),
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "existing_pr_lookup=existing_pr_lookup_unavailable" in report
    assert "reason=publish_override_valid" in report
    assert "publish_override_hash=" in report
    assert "step=verify_remote_branch_absent status=done" in report
    assert "draft_pr_url=" in report
    assert not any(".codex/session.json" in command for command in commands)
    assert [
        "git",
        "add",
        "--",
        "scripts/runner_poll_github_tasks.py",
        "tests/test_runner_poll_github_tasks.py",
    ] in commands
    assert commands[-1][-1] == "--draft"
    assert all(
        command[:2] != ["gh", "pr"] or "merge" not in command
        for command in commands
    )


def test_publish_existing_issue_worktree_valid_override_records_hash_on_failure(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
            remote_branch_exists=True,
        ),
    ):
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body(
                publish_override=_valid_publish_existing_override()
            )
        )

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=remote_branch_conflict" in report
    assert "publish_override_hash=" in report
    assert "approved_override_hash=" in report


def test_publish_issue_worktree_pr_accepts_fenced_yaml_allowed_files(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    body = _maintenance_issue(
        runner.PUBLISH_ISSUE_WORKTREE_PR,
        metadata=(
            f"Repository: {runner.REPO}\n"
            "Source Issue: 123\n"
            "Expected Branch: runner/issue-123\n"
            "```yaml\n"
            "allowed_files:\n"
            "- scripts/runner_poll_github_tasks.py\n"
            "```\n"
        ),
    )

    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path),
    ) as run:
        report = runner.publish_issue_worktree_pr(str(body["body"]))

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert ["git", "add", "--", "scripts/runner_poll_github_tasks.py"] in commands


@pytest.mark.parametrize(
    ("override_update", "reason"),
    (
        ({"target_repository": "alanua/Other"}, "publish_override_scope_mismatch"),
        ({"source_issue": 999}, "publish_override_scope_mismatch"),
        ({"output_branch": "runner/issue-999"}, "publish_override_scope_mismatch"),
        ({"base_branch": "develop"}, "publish_override_scope_mismatch"),
        (
            {"allowed_files": ["docs/RUNNER_MAINTENANCE_TASKS.md"]},
            "publish_override_scope_mismatch",
        ),
        ({"draft_pr": False}, "publish_override_scope_mismatch"),
        ({"extra": "field"}, "publish_override_malformed"),
    ),
)
def test_publish_existing_issue_worktree_wrong_override_scope_blocks(
    tmp_path: Path, override_update: dict[str, object], reason: str
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    override = _valid_publish_existing_override()
    override.update(override_update)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body(publish_override=override)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("NEEDS_OPERATOR:")
    assert f"reason={reason}" in report
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


def test_publish_existing_issue_worktree_malformed_override_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body(publish_override="{not-json")
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=publish_override_malformed" in report
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_existing_issue_worktree_remote_branch_conflict_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
            remote_branch_exists=True,
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body(
                publish_override=_valid_publish_existing_override()
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=publish_override_valid" in report
    assert "reason=remote_branch_conflict" in report
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


def test_publish_existing_issue_worktree_generic_approval_text_does_not_count(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    body = (
        _publish_existing_issue_worktree_body()
        + "\n```task\n+\nrunner:done\nTask complete\n```"
    )
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
        ),
    ) as run:
        report = runner.publish_existing_issue_worktree(body)

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=publish_override_missing" in report
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_existing_issue_worktree_public_report_is_sanitized(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    raw_error = (
        f"{tmp_path}/issue-123 token=secret gh stderr command output must not leak"
    )
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_code=1,
            existing_pr_url=raw_error,
        ),
    ):
        report = runner.publish_existing_issue_worktree(
            _publish_existing_issue_worktree_body()
        )

    assert str(tmp_path) not in report
    assert raw_error not in report
    assert "token=secret" not in report
    assert "command output" not in report


def test_publish_issue_worktree_to_existing_pr_updates_existing_draft_pr_only(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path, issue_number=1640)
    post_head = "b" * 40
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_existing_pr_publish_commands(
            worktree_path=worktree_path,
            post_pr_state=_existing_pr_publish_state(
                head_sha=post_head,
                files=(
                    "scripts/runner_poll_github_tasks.py",
                    "tests/test_runner_poll_github_tasks.py",
                ),
            ),
            untracked_files=(
                ".codex/session.json",
                "tests/test_runner_poll_github_tasks.py",
            ),
            validated_publish_files=(
                "scripts/runner_poll_github_tasks.py",
                "tests/test_runner_poll_github_tasks.py",
            ),
            post_commit_head=post_head,
        ),
    ) as run:
        report = runner.publish_issue_worktree_to_existing_pr(
            _publish_issue_worktree_to_existing_pr_body(
                allowed_files=(
                    "scripts/runner_poll_github_tasks.py",
                    "tests/test_runner_poll_github_tasks.py",
                )
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert runner.PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert "maintenance_task_id=publish_issue_worktree_to_existing_pr" in report
    assert "pull_request=1638" in report
    assert f"expected_pr_head_sha={HEAD_SHA}" in report
    assert f"pushed_head_sha={post_head}" in report
    assert "post_push_pr_changed_files_count=2" in report
    assert "new_pr_changed_files_count=1" in report
    assert "pr_url=https://github.com/alanua/Skeleton/pull/1638" in report
    assert [
        "git",
        "add",
        "--",
        "scripts/runner_poll_github_tasks.py",
        "tests/test_runner_poll_github_tasks.py",
    ] in commands
    assert [
        "git",
        "push",
        "origin",
        f"--force-with-lease=runner/issue-1638:{HEAD_SHA}",
        "HEAD:refs/heads/runner/issue-1638",
    ] in commands
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)
    assert not any(".codex/session.json" in command for command in commands)


def test_publish_issue_worktree_to_existing_pr_routes_publish_only() -> None:
    issue = _maintenance_issue(runner.PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR)
    report = (
        "NEEDS_OPERATOR: Runner host maintenance task needs operator action.\n"
        "maintenance_task_id=publish_issue_worktree_to_existing_pr\n"
        "reason=missing_operator_approval\n"
        "success_criteria=not_met"
    )

    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as post, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    run_codex.assert_not_called()
    assert "route=publish_only" in post.call_args.args[1]


@pytest.mark.parametrize(
    ("body_kwargs", "pr_state", "reason"),
    (
        ({"repository": "alanua/Other"}, None, "unsupported_repository"),
        (
            {"expected_source_branch": "runner/issue-999"},
            None,
            "missing_or_invalid_expected_source_branch",
        ),
        ({}, _existing_pr_publish_state(number=999), "pr_number_mismatch"),
        ({}, _existing_pr_publish_state(state="CLOSED"), "pr_not_open"),
        ({}, _existing_pr_publish_state(is_draft=False), "pr_not_draft"),
        ({}, _existing_pr_publish_state(base_ref="develop"), "pr_base_mismatch"),
        (
            {},
            _existing_pr_publish_state(head_repository="alanua/Other"),
            "pr_head_repository_mismatch",
        ),
        ({}, _existing_pr_publish_state(head_ref="runner/issue-999"), "pr_head_branch_mismatch"),
        ({}, _existing_pr_publish_state(head_sha="c" * 40), "pr_head_sha_mismatch"),
    ),
)
def test_publish_issue_worktree_to_existing_pr_wrong_metadata_or_pr_blocks_before_staging(
    tmp_path: Path,
    body_kwargs: dict[str, object],
    pr_state: dict[str, object] | None,
    reason: str,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path, issue_number=1640)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_existing_pr_publish_commands(
            worktree_path=worktree_path,
            pre_pr_state=pr_state,
        ),
    ) as run:
        report = runner.publish_issue_worktree_to_existing_pr(
            _publish_issue_worktree_to_existing_pr_body(**body_kwargs)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith(("NEEDS_OPERATOR:", "BLOCKED:"))
    assert f"reason={reason}" in report
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


@pytest.mark.parametrize(
    ("command_kwargs", "reason"),
    (
        ({"branch": "runner/issue-999"}, "source_branch_mismatch"),
        ({"ancestor_code": 1}, "source_not_derived_from_expected_pr_head"),
        (
            {
                "changed_files": ("scripts/runner_poll_github_tasks.py",),
                "untracked_files": ("docs/unexpected.md",),
            },
            "unexpected_untracked_files",
        ),
        (
            {"changed_files": ("docs/unexpected.md",)},
            "changed_files_outside_allowlist",
        ),
        ({"changed_files": ("../unsafe",)}, "changed_tracked_file_path_unsafe"),
        ({"diff_check_code": 1}, "diff_check_failed"),
        ({"commit_code": 1}, "commit_failed"),
        ({"push_code": 1}, "push_failed"),
    ),
)
def test_publish_issue_worktree_to_existing_pr_source_checks_block_safely(
    tmp_path: Path, command_kwargs: dict[str, object], reason: str
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path, issue_number=1640)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_existing_pr_publish_commands(
            worktree_path=worktree_path,
            **command_kwargs,
        ),
    ) as run:
        report = runner.publish_issue_worktree_to_existing_pr(
            _publish_issue_worktree_to_existing_pr_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    if reason in {
        "source_branch_mismatch",
        "source_not_derived_from_expected_pr_head",
        "unexpected_untracked_files",
        "changed_files_outside_allowlist",
        "changed_tracked_file_path_unsafe",
        "diff_check_failed",
    }:
        assert all(command[:2] != ["git", "add"] for command in commands)
    if reason != "push_failed":
        assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


@pytest.mark.parametrize(
    ("post_state", "reason"),
    (
        (_existing_pr_publish_state(number=999, head_sha="b" * 40), "post_push_pr_number_mismatch"),
        (_existing_pr_publish_state(state="CLOSED", head_sha="b" * 40), "post_push_pr_not_open"),
        (_existing_pr_publish_state(is_draft=False, head_sha="b" * 40), "post_push_pr_not_draft"),
        (_existing_pr_publish_state(base_ref="develop", head_sha="b" * 40), "post_push_pr_base_mismatch"),
        (
            _existing_pr_publish_state(head_ref="runner/issue-999", head_sha="b" * 40),
            "post_push_pr_head_branch_mismatch",
        ),
        (_existing_pr_publish_state(head_sha="c" * 40), "post_push_pr_head_sha_mismatch"),
        (_existing_pr_publish_state(head_sha="b" * 40, files=()), "pre_existing_pr_files_missing"),
        (
            _existing_pr_publish_state(
                head_sha="b" * 40,
                files=("scripts/runner_poll_github_tasks.py", "docs/unexpected.md"),
            ),
            "new_pr_files_outside_allowlist",
        ),
    ),
)
def test_publish_issue_worktree_to_existing_pr_post_push_verification_blocks(
    tmp_path: Path, post_state: dict[str, object], reason: str
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path, issue_number=1640)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_existing_pr_publish_commands(
            worktree_path=worktree_path,
            post_pr_state=post_state,
            post_commit_head="b" * 40,
        ),
    ):
        report = runner.publish_issue_worktree_to_existing_pr(
            _publish_issue_worktree_to_existing_pr_body()
        )

    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report


def test_publish_issue_worktree_to_existing_pr_rejects_unapproved_or_extra_metadata() -> None:
    report = runner.publish_issue_worktree_to_existing_pr(
        _publish_issue_worktree_to_existing_pr_body(
            operator_approval="approved",
            extra_metadata=("Command: git push anything",),
        )
    )

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=unsupported_metadata_field" in report


@pytest.mark.parametrize(
    "packet_id",
    ("home_edge_1640_to_pr_1638", "docs_1668_to_pr_1670"),
)
def test_overlay_registered_worktree_builds_one_parented_commit_for_static_packets(
    tmp_path: Path, packet_id: str
) -> None:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    source_head_marker = (worktree_path / ".git").read_bytes()
    new_commit = "d" * 40
    changed_files = packet.allowed_files[:1]
    allowed_untracked_files = packet.allowed_files[1:2]

    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            changed_files=changed_files,
            untracked_files=(*allowed_untracked_files, ".codex/session.json"),
            new_commit_sha=new_commit,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR in runner.PUBLISH_ONLY_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert f"maintenance_task_id={runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR}" in report
    assert f"recovery_packet={packet_id}" in report
    assert f"pull_request={packet.pr_number}" in report
    assert f"expected_target_branch={packet.target_branch}" in report
    assert f"expected_target_head_sha={packet.target_head_sha}" in report
    assert f"constructed_head_sha={new_commit}" in report
    assert f"pushed_head_sha={new_commit}" in report
    assert f"pr_url=https://github.com/alanua/Skeleton/pull/{packet.pr_number}" in report
    assert "validated_publish_files_count=2" in report
    assert ["git", "read-tree", "HEAD"] in commands
    assert [
        "git",
        "diff",
        "--check",
        "--cached",
        "HEAD",
        "--",
        *changed_files,
        *allowed_untracked_files,
    ] in commands
    assert [
        "git",
        "diff",
        "--check",
        "--no-index",
        "--",
        os.devnull,
        allowed_untracked_files[0],
    ] in commands
    assert ["git", "read-tree", packet.target_head_sha] in commands
    assert any(command[:2] == ["git", "commit-tree"] and command[3:5] == ["-p", packet.target_head_sha] for command in commands)
    assert ["git", "rev-parse", f"{new_commit}^"] in commands
    assert ["git", "diff", "--name-only", "a" * 40, new_commit, "--"] in commands
    assert ["git", "diff", "--name-only", "main", new_commit, "--"] not in commands
    assert [
        "git",
        "push",
        "origin",
        f"--force-with-lease={packet.target_branch}:{packet.target_head_sha}",
        f"{new_commit}:refs/heads/{packet.target_branch}",
    ] in commands
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "commit"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)
    assert all(command[:3] != ["gh", "workflow", "run"] for command in commands)
    assert not any("merge-base" in command for command in commands)
    assert (worktree_path / ".git").read_bytes() == source_head_marker


@pytest.mark.parametrize(
    ("file_kind", "tracked", "reason"),
    (
        ("symlink", True, "unsafe_source_file"),
        ("symlink", False, "unsafe_source_file"),
        ("directory", True, "unsafe_source_file"),
        ("fifo", True, "unsafe_source_file"),
        ("executable", True, "unsafe_source_file"),
        ("outside_parent_symlink", True, "unsafe_source_file"),
        ("untracked_whitespace", False, "untracked_diff_check_failed"),
    ),
)
def test_overlay_registered_worktree_rejects_unsafe_source_files_before_objects(
    tmp_path: Path, file_kind: str, tracked: bool, reason: str
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    relative_path = packet.allowed_files[0]
    source_file = worktree_path / relative_path
    if file_kind == "symlink":
        source_file.unlink()
        source_file.symlink_to(tmp_path / "outside-source.txt")
    elif file_kind == "directory":
        source_file.unlink()
        source_file.mkdir()
    elif file_kind == "fifo":
        source_file.unlink()
        os.mkfifo(source_file)
    elif file_kind == "executable":
        source_file.chmod(0o755)
    elif file_kind == "outside_parent_symlink":
        runner.shutil.rmtree(worktree_path / "core")
        outside_core = tmp_path / "outside-core"
        outside_core.mkdir()
        outside_file = outside_core / "home_edge" / "visual_capture.py"
        outside_file.parent.mkdir()
        outside_file.write_text("outside\n", encoding="utf-8")
        (worktree_path / "core").symlink_to(outside_core)
    elif file_kind == "untracked_whitespace":
        source_file.write_text("trailing whitespace \n", encoding="utf-8")

    changed_files = (relative_path,) if tracked else ()
    untracked_files = () if tracked else (relative_path,)
    command_kwargs: dict[str, object] = {}
    if file_kind == "untracked_whitespace":
        command_kwargs = {
            "untracked_diff_check_code": 1,
            "untracked_diff_check_output": f"{relative_path}:1: trailing whitespace.\n",
        }

    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            changed_files=changed_files,
            untracked_files=untracked_files,
            **command_kwargs,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    assert all(command[:2] != ["git", "read-tree"] for command in commands)
    assert all(command[:3] != ["git", "hash-object", "-w"] for command in commands)
    assert all(command[:2] != ["git", "commit-tree"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_overlay_registered_worktree_removes_temporary_index_on_failure(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    original_named_temporary_file = runner.tempfile.NamedTemporaryFile

    def named_temporary_file_in_tmp(
        prefix: str, delete: bool
    ) -> object:
        return original_named_temporary_file(
            prefix=prefix, delete=delete, dir=tmp_path
        )

    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner.tempfile,
        "NamedTemporaryFile",
        side_effect=named_temporary_file_in_tmp,
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            read_tree_code=1,
        ),
    ):
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=temporary_index_failed" in report
    assert not list(tmp_path.glob("runner-overlay-index-*"))


def test_overlay_registered_worktree_fetches_exact_branch_when_target_object_missing(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            cat_file_code=1,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert [
        "git",
        "fetch",
        "origin",
        f"{packet.target_branch}:refs/remotes/origin/{packet.target_branch}",
    ] in commands


def test_overlay_registered_worktree_fetches_registered_main_for_missing_base_ref_oid(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            base_cat_file_code=1,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert ["git", "fetch", "origin", "main:refs/remotes/origin/main"] in commands


@pytest.mark.parametrize(
    ("pre_state", "command_kwargs", "reason"),
    (
        (_overlay_pr_state("home_edge_1640_to_pr_1638", base_sha=""), {}, "base_ref_oid_invalid"),
        (
            _overlay_pr_state("home_edge_1640_to_pr_1638", base_sha="not-a-sha"),
            {},
            "base_ref_oid_invalid",
        ),
        (
            None,
            {"base_cat_file_code": 1, "base_fetch_code": 1},
            "base_object_unavailable",
        ),
        (
            None,
            {"base_cat_file_code": 1, "fetched_base_sha": "c" * 40},
            "fetched_base_sha_mismatch",
        ),
    ),
)
def test_overlay_registered_worktree_base_ref_oid_failures_block_before_push(
    tmp_path: Path,
    pre_state: dict[str, object] | None,
    command_kwargs: dict[str, object],
    reason: str,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            pre_pr_state=pre_state,
            **command_kwargs,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    assert "constructed_head_sha=" not in report
    assert "pushed_head_sha=" not in report
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_overlay_registered_worktree_staged_only_whitespace_blocks_before_objects(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    staged_file = packet.allowed_files[0]
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            changed_files=(staged_file,),
            staged_only_files=(staged_file,),
            diff_check_code=1,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=diff_check_failed" in report
    assert ["git", "ls-files", "--stage", "--", staged_file] in commands
    assert all(command[:3] != ["git", "hash-object", "-w"] for command in commands)
    assert all(command[:2] != ["git", "commit-tree"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


@pytest.mark.parametrize(
    ("body", "reason"),
    (
        (_overlay_registered_worktree_body(packet_id="unknown"), "unknown_recovery_packet"),
        (
            _overlay_registered_worktree_body(operator_approval="approved"),
            "missing_operator_approval",
        ),
        (
            _overlay_registered_worktree_body(extra_metadata=("Repository: alanua/Skeleton",)),
            "unsupported_metadata_field",
        ),
    ),
)
def test_overlay_registered_worktree_rejects_unregistered_inputs_before_commands(
    body: str, reason: str
) -> None:
    with mock.patch.object(runner, "run_command") as run:
        report = runner.overlay_registered_worktree_to_existing_pr(body)

    assert report.startswith("NEEDS_OPERATOR:")
    assert f"reason={reason}" in report
    run.assert_not_called()


def test_overlay_registered_worktree_gh_success_does_not_invoke_public_rest(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
        ),
    ), mock.patch.object(runner.urllib.request, "build_opener") as build_opener:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    assert report.startswith("DONE:")
    assert "pr_metadata_source=gh" in report
    assert "post_push_pr_metadata_source=gh" in report
    build_opener.assert_not_called()


def test_overlay_registered_worktree_gh_failure_uses_fixed_public_rest_endpoints(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    pr_url = f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}"
    files_url = f"{pr_url}/files?per_page=100&page=1"
    opener = _OverlayRestOpener(
        {
            pr_url: [
                _OverlayRestResponse(_overlay_rest_pr_payload(packet_id), pr_url),
                _OverlayRestResponse(
                    _overlay_rest_pr_payload(packet_id, head_sha="d" * 40), pr_url
                ),
            ],
            files_url: [
                _OverlayRestResponse(
                    _overlay_rest_file_payload(("scripts/runner_poll_github_tasks.py",)),
                    files_url,
                ),
                _OverlayRestResponse(
                    _overlay_rest_file_payload(
                        (
                            "scripts/runner_poll_github_tasks.py",
                            packet.allowed_files[0],
                        )
                    ),
                    files_url,
                ),
            ],
        }
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            pr_view_code=1,
        ),
    ), mock.patch.object(
        runner.urllib.request, "build_opener", return_value=opener
    ):
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    assert report.startswith("DONE:")
    assert "pr_metadata_source=public_rest" in report
    assert "post_push_pr_metadata_source=public_rest" in report
    assert [request.full_url for request in opener.requests[:2]] == [
        f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}",
        f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}/files?per_page=100&page=1",
    ]
    for request in opener.requests:
        parsed = urllib.parse.urlparse(request.full_url)
        assert parsed.scheme == "https"
        assert parsed.hostname == "api.github.com"
        assert parsed.path.startswith(f"/repos/alanua/Skeleton/pulls/{packet.pr_number}")
        assert request.headers == {
            "Accept": "application/vnd.github+json",
            "X-github-api-version": "2022-11-28",
            "User-agent": "skeleton-runner-registered-overlay",
        }
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
    assert set(opener.timeouts) == {runner._REGISTERED_OVERLAY_PUBLIC_REST_TIMEOUT_SECONDS}


def test_overlay_registered_worktree_invalid_gh_json_falls_back_but_fail_closed_gh_state_does_not(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    fallback_state = _overlay_pr_state(packet_id)
    with mock.patch.object(
        runner,
        "_registered_worktree_overlay_pr_state",
        side_effect=json.JSONDecodeError("bad", "}", 0),
    ), mock.patch.object(
        runner,
        "_registered_worktree_overlay_public_rest_pr_state",
        return_value=fallback_state,
    ) as public_rest:
        state, source = runner._registered_worktree_overlay_pr_state_with_fallback(
            runner.IssueWorktreeExistingPrPublishRequest(
                repository=runner.REPO,
                source_issue=1640,
                expected_source_branch="runner/issue-1640",
                pr_number=1638,
                expected_pr_head_sha=runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id].target_head_sha,
                expected_pr_head_branch=runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id].target_branch,
                allowed_files=frozenset(),
            ),
            runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id],
            worktree_path,
        )
    assert state == fallback_state
    assert source == "public_rest"
    public_rest.assert_called_once()

    bad_but_complete_state = _overlay_pr_state(packet_id, head_sha="c" * 40)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            pre_pr_state=bad_but_complete_state,
        ),
    ), mock.patch.object(runner.urllib.request, "build_opener") as build_opener:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=pr_head_sha_mismatch" in report
    build_opener.assert_not_called()


@pytest.mark.parametrize("packet_id", tuple(runner.REGISTERED_WORKTREE_OVERLAY_PACKETS))
def test_overlay_registered_public_rest_normalizes_both_registered_packets(
    tmp_path: Path, packet_id: str
) -> None:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    opener = _overlay_rest_opener(packet_id, files=packet.allowed_files[:1])
    request = runner.IssueWorktreeExistingPrPublishRequest(
        repository=runner.REPO,
        source_issue=packet.source_issue,
        expected_source_branch=packet.source_branch,
        pr_number=packet.pr_number,
        expected_pr_head_sha=packet.target_head_sha,
        expected_pr_head_branch=packet.target_branch,
        allowed_files=frozenset(packet.allowed_files),
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner.urllib.request, "build_opener", return_value=opener):
        state = runner._registered_worktree_overlay_public_rest_pr_state(request, packet)

    assert state == _overlay_pr_state(packet_id, files=packet.allowed_files[:1])
    assert runner._existing_pr_publish_block_reason(request, state) is None
    assert runner._registered_worktree_overlay_base_ref_oid(state) == "a" * 40
    assert runner._existing_pr_publish_file_paths(state) == frozenset(packet.allowed_files[:1])


@pytest.mark.parametrize(
    "mutate",
    (
        lambda packet: runner.RegisteredWorktreeOverlayPacket(
            packet.packet_id,
            packet.source_issue,
            packet.source_branch,
            999,
            packet.target_branch,
            packet.target_head_sha,
            packet.allowed_files,
        ),
        lambda packet: runner.RegisteredWorktreeOverlayPacket(
            "unknown",
            packet.source_issue,
            packet.source_branch,
            packet.pr_number,
            packet.target_branch,
            packet.target_head_sha,
            packet.allowed_files,
        ),
    ),
)
def test_overlay_registered_public_rest_rejects_unregistered_packet_or_pr(
    tmp_path: Path, mutate: object
) -> None:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS["home_edge_1640_to_pr_1638"]
    bad_packet = mutate(packet)
    request = runner.IssueWorktreeExistingPrPublishRequest(
        repository=runner.REPO,
        source_issue=bad_packet.source_issue,
        expected_source_branch=bad_packet.source_branch,
        pr_number=bad_packet.pr_number,
        expected_pr_head_sha=bad_packet.target_head_sha,
        expected_pr_head_branch=bad_packet.target_branch,
        allowed_files=frozenset(bad_packet.allowed_files),
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner.urllib.request, "build_opener") as build_opener:
        with pytest.raises(RuntimeError):
            runner._registered_worktree_overlay_public_rest_pr_state(request, bad_packet)

    build_opener.assert_not_called()


def test_overlay_registered_public_rest_rejects_private_project_tree_before_request(
    tmp_path: Path,
) -> None:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS["home_edge_1640_to_pr_1638"]
    project_tree = _project_tree_with_skeleton_worktree_root(tmp_path)
    project_tree["projects"]["skeleton"]["public"] = False
    request = runner.IssueWorktreeExistingPrPublishRequest(
        repository=runner.REPO,
        source_issue=packet.source_issue,
        expected_source_branch=packet.source_branch,
        pr_number=packet.pr_number,
        expected_pr_head_sha=packet.target_head_sha,
        expected_pr_head_branch=packet.target_branch,
        allowed_files=frozenset(packet.allowed_files),
    )
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner.urllib.request, "build_opener") as build_opener:
        with pytest.raises(RuntimeError):
            runner._registered_worktree_overlay_public_rest_pr_state(request, packet)

    build_opener.assert_not_called()


@pytest.mark.parametrize(
    ("responses", "reason_fragment"),
    (
        (
            {
                "pr_status_500": _OverlayRestResponse(
                    {}, "https://api.github.com/repos/alanua/Skeleton/pulls/1638", status=500
                )
            },
            "non-2xx",
        ),
        (
            {
                "pr_redirect": _OverlayRestResponse(
                    {},
                    "https://evil.example/repos/alanua/Skeleton/pulls/1638",
                )
            },
            "final URL",
        ),
        (
            {
                "pr_malformed": _OverlayRestResponse(
                    {}, "https://api.github.com/repos/alanua/Skeleton/pulls/1638", raw=b"{"
                )
            },
            "malformed JSON",
        ),
        (
            {
                "pr_oversized": _OverlayRestResponse(
                    {},
                    "https://api.github.com/repos/alanua/Skeleton/pulls/1638",
                    raw=b"[" * (runner._REGISTERED_OVERLAY_PUBLIC_REST_PR_BYTES + 1),
                )
            },
            "too large",
        ),
    ),
)
def test_overlay_registered_public_rest_pr_request_failures_close(
    tmp_path: Path, responses: dict[str, _OverlayRestResponse], reason_fragment: str
) -> None:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS["home_edge_1640_to_pr_1638"]
    pr_url = f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}"
    opener = _overlay_rest_opener("home_edge_1640_to_pr_1638")
    opener.responses[pr_url] = next(iter(responses.values()))
    request = runner.IssueWorktreeExistingPrPublishRequest(
        repository=runner.REPO,
        source_issue=packet.source_issue,
        expected_source_branch=packet.source_branch,
        pr_number=packet.pr_number,
        expected_pr_head_sha=packet.target_head_sha,
        expected_pr_head_branch=packet.target_branch,
        allowed_files=frozenset(packet.allowed_files),
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(RuntimeError, match=reason_fragment):
            runner._registered_worktree_overlay_public_rest_pr_state(request, packet)


@pytest.mark.parametrize(
    ("payload_update", "file_payload", "reason_fragment"),
    (
        ({"head": {"ref": "runner/issue-1630", "sha": "bad", "repo": {"full_name": runner.REPO}}}, None, "head SHA"),
        ({"head": {"ref": "runner/issue-1630", "sha": "1" * 40, "repo": {"full_name": "alanua/Other"}}}, None, "head repository"),
        ({"html_url": "https://example.com/alanua/Skeleton/pull/1638"}, None, "URL"),
        ({}, [{"filename": "../unsafe"}], "file path"),
        ({}, [{"filename": "a.txt"}, {"filename": "a.txt"}], "duplicate"),
    ),
)
def test_overlay_registered_public_rest_rejects_malformed_or_inconsistent_payloads(
    tmp_path: Path,
    payload_update: dict[str, object],
    file_payload: list[dict[str, object]] | None,
    reason_fragment: str,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    pr_payload = _overlay_rest_pr_payload(packet_id)
    pr_payload.update(payload_update)
    pr_url = f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}"
    files_url = f"{pr_url}/files?per_page=100&page=1"
    opener = _OverlayRestOpener(
        {
            pr_url: _OverlayRestResponse(pr_payload, pr_url),
            files_url: _OverlayRestResponse(
                file_payload if file_payload is not None else _overlay_rest_file_payload(("scripts/runner_poll_github_tasks.py",)),
                files_url,
            ),
        }
    )
    request = runner.IssueWorktreeExistingPrPublishRequest(
        repository=runner.REPO,
        source_issue=packet.source_issue,
        expected_source_branch=packet.source_branch,
        pr_number=packet.pr_number,
        expected_pr_head_sha=packet.target_head_sha,
        expected_pr_head_branch=packet.target_branch,
        allowed_files=frozenset(packet.allowed_files),
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(RuntimeError, match=reason_fragment):
            runner._registered_worktree_overlay_public_rest_pr_state(request, packet)


def test_overlay_registered_public_rest_rejects_excessive_or_inconsistent_pagination(
    tmp_path: Path,
) -> None:
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS["home_edge_1640_to_pr_1638"]
    pr_url = f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}"
    responses = {
        pr_url: _OverlayRestResponse(
            _overlay_rest_pr_payload("home_edge_1640_to_pr_1638"), pr_url
        )
    }
    for page in range(1, runner._REGISTERED_OVERLAY_PUBLIC_REST_FILE_PAGE_CAP + 1):
        files_url = f"{pr_url}/files?per_page=100&page={page}"
        responses[files_url] = _OverlayRestResponse(
            [{"filename": f"file-{page}-{index}.txt"} for index in range(100)],
            files_url,
            headers={"Link": f"<{pr_url}/files?per_page=100&page={page + 1}>; rel=\"next\""},
        )
    opener = _OverlayRestOpener(responses)
    request = runner.IssueWorktreeExistingPrPublishRequest(
        repository=runner.REPO,
        source_issue=packet.source_issue,
        expected_source_branch=packet.source_branch,
        pr_number=packet.pr_number,
        expected_pr_head_sha=packet.target_head_sha,
        expected_pr_head_branch=packet.target_branch,
        allowed_files=frozenset(packet.allowed_files),
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(RuntimeError, match="pagination exceeded"):
            runner._registered_worktree_overlay_public_rest_pr_state(request, packet)


def test_overlay_registered_post_push_public_rest_rereads_and_rejects_stale_metadata(
    tmp_path: Path,
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    packet = runner.REGISTERED_WORKTREE_OVERLAY_PACKETS[packet_id]
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    pr_url = f"https://api.github.com/repos/alanua/Skeleton/pulls/{packet.pr_number}"
    files_url = f"{pr_url}/files?per_page=100&page=1"
    opener = _OverlayRestOpener(
        {
            pr_url: _OverlayRestResponse(_overlay_rest_pr_payload(packet_id), pr_url),
            files_url: _OverlayRestResponse(
                _overlay_rest_file_payload(("scripts/runner_poll_github_tasks.py",)),
                files_url,
            ),
        }
    )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            pr_view_code=1,
        ),
    ), mock.patch.object(runner.urllib.request, "build_opener", return_value=opener):
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=post_push_pr_head_sha_mismatch" in report
    assert "constructed_head_sha=" in report
    assert "pushed_head_sha=" not in report
    assert [request.full_url for request in opener.requests].count(pr_url) == 2


@pytest.mark.parametrize(
    ("pre_state", "reason"),
    (
        (_overlay_pr_state("home_edge_1640_to_pr_1638", number=999), "pr_number_mismatch"),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", state="CLOSED"), "pr_not_open"),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", is_draft=False), "pr_not_draft"),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", base_ref="develop"), "pr_base_mismatch"),
        (
            _overlay_pr_state("home_edge_1640_to_pr_1638", head_repository="alanua/Other"),
            "pr_head_repository_mismatch",
        ),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", head_ref="runner/issue-999"), "pr_head_branch_mismatch"),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", head_sha="c" * 40), "pr_head_sha_mismatch"),
    ),
)
def test_overlay_registered_worktree_wrong_pr_state_blocks_before_write(
    tmp_path: Path, pre_state: dict[str, object], reason: str
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            pre_pr_state=pre_state,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    assert all(command[:2] != ["git", "read-tree"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


@pytest.mark.parametrize(
    ("command_kwargs", "reason"),
    (
        ({"branch": "runner/issue-999"}, "source_branch_mismatch"),
        ({"remote_url": "https://github.com/alanua/Other.git"}, "origin_remote_mismatch"),
        ({"changed_files": ("docs/unexpected.md",)}, "changed_files_outside_allowlist"),
        ({"changed_files": ("../unsafe",)}, "changed_tracked_file_path_unsafe"),
        ({"untracked_files": ("docs/unexpected.md",)}, "unexpected_untracked_files"),
        ({"untracked_files": ("../unsafe",)}, "untracked_file_path_unsafe"),
        ({"changed_files": ()}, "no_publishable_changes"),
        ({"diff_check_code": 1}, "diff_check_failed"),
        ({"cat_file_code": 1, "fetch_code": 1}, "target_object_unavailable"),
        ({"cat_file_code": 1, "fetched_sha": "c" * 40}, "fetched_target_sha_mismatch"),
        ({"read_tree_code": 1}, "temporary_index_failed"),
        ({"write_tree_code": 1}, "temporary_index_failed"),
        ({"commit_tree_code": 1}, "commit_failed"),
        ({"parent_sha": "c" * 40}, "commit_parent_verification_failed"),
        ({"commit_diff_files": ("docs/unexpected.md",)}, "commit_diff_outside_allowlist"),
        ({"pr_diff_files": ()}, "pre_existing_pr_files_missing"),
        ({"push_code": 1}, "push_failed"),
    ),
)
def test_overlay_registered_worktree_fail_closed_source_and_plumbing_checks(
    tmp_path: Path, command_kwargs: dict[str, object], reason: str
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            **command_kwargs,
        ),
    ) as run:
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    if reason in {
        "commit_parent_verification_failed",
        "commit_diff_outside_allowlist",
        "pre_existing_pr_files_missing",
        "push_failed",
    }:
        assert "constructed_head_sha=" in report
        assert "pushed_head_sha=" not in report
    if reason not in {"push_failed"}:
        assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)


@pytest.mark.parametrize(
    ("post_state", "reason"),
    (
        (
            _overlay_pr_state("home_edge_1640_to_pr_1638", number=999, head_sha="d" * 40),
            "post_push_pr_number_mismatch",
        ),
        (
            _overlay_pr_state("home_edge_1640_to_pr_1638", state="CLOSED", head_sha="d" * 40),
            "post_push_pr_not_open",
        ),
        (
            _overlay_pr_state("home_edge_1640_to_pr_1638", head_ref="runner/issue-999", head_sha="d" * 40),
            "post_push_pr_head_branch_mismatch",
        ),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", head_sha="e" * 40), "post_push_pr_head_sha_mismatch"),
        (_overlay_pr_state("home_edge_1640_to_pr_1638", head_sha="d" * 40, files=()), "pre_existing_pr_files_missing"),
        (
            _overlay_pr_state(
                "home_edge_1640_to_pr_1638",
                head_sha="d" * 40,
                files=("scripts/runner_poll_github_tasks.py", "docs/unexpected.md"),
            ),
            "new_pr_files_outside_allowlist",
        ),
    ),
)
def test_overlay_registered_worktree_post_push_verification_blocks(
    tmp_path: Path, post_state: dict[str, object], reason: str
) -> None:
    packet_id = "home_edge_1640_to_pr_1638"
    worktree_path = _prepare_overlay_source_worktree(tmp_path, packet_id)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_overlay_registered_worktree_commands(
            worktree_path=worktree_path,
            packet_id=packet_id,
            post_pr_state=post_state,
        ),
    ):
        report = runner.overlay_registered_worktree_to_existing_pr(
            _overlay_registered_worktree_body(packet_id=packet_id)
        )

    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    assert "constructed_head_sha=" in report
    assert "pushed_head_sha=" not in report


def test_overlay_registered_worktree_route_is_publish_only_and_normal_route_unchanged() -> None:
    assert set(runner.REGISTERED_WORKTREE_OVERLAY_PACKETS) == {
        "home_edge_1640_to_pr_1638",
        "docs_1668_to_pr_1670",
    }
    issue = _maintenance_issue(runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR)
    report = (
        "NEEDS_OPERATOR: Runner host maintenance task needs operator action.\n"
        f"maintenance_task_id={runner.OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR}\n"
        "reason=unknown_recovery_packet\n"
        "success_criteria=not_met"
    )

    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as post, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue)

    run_codex.assert_not_called()
    assert "route=publish_only" in post.call_args.args[1]


def test_publish_container_validation_worktree_publishes_fixed_static_packet(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_container_validation_worktree(tmp_path)
    pushed_head = "c" * 40
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_container_validation_publish_commands(
            worktree_path=worktree_path,
            untracked_files=(".codex/session.json",),
            post_commit_head=pushed_head,
        ),
    ) as run:
        report = runner.publish_container_validation_worktree(
            _publish_container_validation_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert runner.PUBLISH_CONTAINER_VALIDATION_WORKTREE in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert runner.PUBLISH_CONTAINER_VALIDATION_WORKTREE in runner.PUBLISH_ONLY_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert "maintenance_task_id=publish_container_validation_worktree" in report
    assert "repository=alanua/Skeleton" in report
    assert "source_issue=1667" in report
    assert "expected_source_branch=runner/issue-1667" in report
    assert "expected_branch=runner/issue-1667" in report
    assert f"pushed_head_sha={pushed_head}" in report
    assert f"pr_url={PR_URL}" in report
    assert "validated_publish_files_count=3" in report
    assert ["git", "diff", "--check", "--", *runner.CONTAINER_VALIDATION_PUBLISH_FILES] in commands
    assert ["git", "add", "--", *runner.CONTAINER_VALIDATION_PUBLISH_FILES] in commands
    assert [
        "git",
        "push",
        "origin",
        "refs/heads/runner/issue-1667:refs/heads/runner/issue-1667",
    ] in commands
    assert [
        "gh",
        "pr",
        "create",
        "--repo",
        runner.REPO,
        "--base",
        "main",
        "--head",
        "runner/issue-1667",
        "--title",
        "Publish container package validation workflow",
        "--body",
        "Automated Runner publish task for retained issue #1667.",
        "--draft",
    ] in commands
    assert not any(".codex/session.json" in command for command in commands)
    assert all("--force" not in command for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)
    assert all(command[:3] != ["gh", "workflow", "run"] for command in commands)


@pytest.mark.parametrize(
    ("body_kwargs", "reason"),
    (
        ({"repository": "alanua/Other"}, "unsupported_repository"),
        ({"source_issue": 1668}, "unsupported_source_issue"),
        (
            {"expected_source_branch": "runner/issue-999"},
            "unsupported_expected_source_branch",
        ),
        ({"base_branch": "develop"}, "unsupported_base_branch"),
        ({"output_branch": "runner/issue-999"}, "unsupported_output_branch"),
        ({"draft_pr": "false"}, "draft_pr_required"),
        ({"operator_approval": "approved"}, "missing_operator_approval"),
        (
            {"extra_metadata": ("Allowed Files:", "- README.md")},
            "unsupported_metadata_field",
        ),
        (
            {"extra_metadata": ("PR Title: arbitrary",)},
            "unsupported_metadata_field",
        ),
        (
            {"extra_metadata": ("Worktree Path: /tmp/issue-1667",)},
            "unsupported_metadata_field",
        ),
        (
            {"extra_metadata": ("Remote: git@github.com:evil/repo.git",)},
            "unsupported_metadata_field",
        ),
        (
            {"extra_metadata": ("Refspec: HEAD:refs/heads/main",)},
            "unsupported_metadata_field",
        ),
    ),
)
def test_publish_container_validation_worktree_metadata_fails_before_staging(
    tmp_path: Path, body_kwargs: dict[str, object], reason: str
) -> None:
    _prepare_container_validation_worktree(tmp_path)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.publish_container_validation_worktree(
            _publish_container_validation_worktree_body(**body_kwargs)
        )

    assert report.startswith("NEEDS_OPERATOR:")
    assert f"reason={reason}" in report
    run.assert_not_called()


@pytest.mark.parametrize(
    ("bad_line", "reason"),
    (
        ("Mode: CODEX_TASK", "invalid_mode"),
        ("Maintenance Task ID: publish_existing_issue_worktree", "unsupported_maintenance_task_id"),
    ),
)
def test_publish_container_validation_worktree_requires_exact_route_identity(
    tmp_path: Path, bad_line: str, reason: str
) -> None:
    _prepare_container_validation_worktree(tmp_path)
    body = _publish_container_validation_worktree_body()
    if bad_line.startswith("Mode:"):
        body = body.replace("Mode: RUNTIME_MAINTENANCE_TASK", bad_line)
    else:
        body = body.replace(
            "Maintenance Task ID: publish_container_validation_worktree", bad_line
        )
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.publish_container_validation_worktree(body)

    assert report.startswith("NEEDS_OPERATOR:")
    assert f"reason={reason}" in report
    run.assert_not_called()


@pytest.mark.parametrize(
    ("command_kwargs", "reason"),
    (
        (
            {
                "changed_files": (
                    ".github/workflows/container-package-validation.yml",
                    ".github/workflows/other.yml",
                )
            },
            "changed_files_outside_static_set",
        ),
        (
            {
                "changed_files": (
                    ".github/workflows/container-package-validation.yml",
                    ".github/CODEOWNERS",
                )
            },
            "changed_files_outside_static_set",
        ),
        (
            {
                "changed_files": (
                    ".github/workflows/container-package-validation.yml",
                    "README.md",
                )
            },
            "changed_files_outside_static_set",
        ),
        (
            {
                "untracked_files": (
                    ".codex/session.json",
                    "docs/unexpected.md",
                )
            },
            "unexpected_untracked_files",
        ),
        ({"changed_files": ("../unsafe",)}, "changed_tracked_file_path_unsafe"),
        ({"base_diff_code": 0, "changed_files": ()}, "no_publishable_changes"),
        ({"diff_check_code": 1}, "diff_check_failed"),
        ({"push_code": 1}, "push_failed"),
    ),
)
def test_publish_container_validation_worktree_source_checks_fail_closed(
    tmp_path: Path, command_kwargs: dict[str, object], reason: str
) -> None:
    worktree_path = _prepare_container_validation_worktree(tmp_path)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_container_validation_publish_commands(
            worktree_path=worktree_path,
            **command_kwargs,
        ),
    ) as run:
        report = runner.publish_container_validation_worktree(
            _publish_container_validation_worktree_body(
                task_body=(
                    "git push origin main\n"
                    "gh workflow run container-package-validation.yml\n"
                    "gh pr merge 1\n"
                    "apt install docker\n"
                    "systemctl restart anything"
                )
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    if reason not in {"push_failed"}:
        assert all(command[:2] != ["git", "push"] for command in commands)
    if reason in {
        "changed_files_outside_static_set",
        "unexpected_untracked_files",
        "changed_tracked_file_path_unsafe",
        "no_publishable_changes",
        "diff_check_failed",
    }:
        assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)
    assert all(command[:3] != ["gh", "workflow", "run"] for command in commands)
    assert all(command[0] not in {"apt", "systemctl", "sudo"} for command in commands)


def test_publish_container_validation_worktree_missing_static_file_fails(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_container_validation_worktree(tmp_path)
    (worktree_path / "docs/CONTAINER_PACKAGE_VALIDATION.md").unlink()
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.publish_container_validation_worktree(
            _publish_container_validation_worktree_body()
        )

    assert report.startswith("BLOCKED:")
    assert "reason=static_file_missing" in report
    run.assert_not_called()


@pytest.mark.parametrize(
    ("existing_prs", "reason"),
    (
        (
            [_container_validation_pr_state(is_draft=False, head_sha="c" * 40)],
            "existing_pr_not_draft",
        ),
        (
            [_container_validation_pr_state(base_ref="develop", head_sha="c" * 40)],
            "existing_pr_base_mismatch",
        ),
        (
            [_container_validation_pr_state(head_repository="alanua/Other", head_sha="c" * 40)],
            "existing_pr_head_repository_mismatch",
        ),
        (
            [_container_validation_pr_state(head_ref="runner/issue-999", head_sha="c" * 40)],
            "existing_pr_head_branch_mismatch",
        ),
        (
            [_container_validation_pr_state(head_sha="d" * 40)],
            "existing_pr_head_sha_mismatch",
        ),
        (
            [
                _container_validation_pr_state(head_sha="c" * 40),
                _container_validation_pr_state(head_sha="c" * 40, url="https://github.com/alanua/Skeleton/pull/1668"),
            ],
            "multiple_existing_prs",
        ),
    ),
)
def test_publish_container_validation_worktree_rejects_bad_existing_pr(
    tmp_path: Path, existing_prs: list[dict[str, object]], reason: str
) -> None:
    worktree_path = _prepare_container_validation_worktree(tmp_path)
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_container_validation_publish_commands(
            worktree_path=worktree_path,
            existing_prs=existing_prs,
        ),
    ) as run:
        report = runner.publish_container_validation_worktree(
            _publish_container_validation_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


def test_publish_container_validation_worktree_reuses_matching_existing_draft_pr(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_container_validation_worktree(tmp_path)
    pushed_head = "c" * 40
    pr_url = "https://github.com/alanua/Skeleton/pull/1667"
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_project_tree_with_skeleton_worktree_root(tmp_path),
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_container_validation_publish_commands(
            worktree_path=worktree_path,
            existing_prs=[
                _container_validation_pr_state(
                    head_sha=pushed_head,
                    url=pr_url,
                )
            ],
            post_commit_head=pushed_head,
        ),
    ) as run:
        report = runner.publish_container_validation_worktree(
            _publish_container_validation_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert f"pr_url={pr_url}" in report
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)


def test_publish_existing_issue_worktree_still_rejects_workflow_allowed_file() -> None:
    report = runner.publish_existing_issue_worktree(
        _publish_existing_issue_worktree_body(
            allowed_files=(".github/workflows/container-package-validation.yml",)
        )
    )

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=invalid_allowed_files" in report


def test_publish_existing_issue_worktree_requires_draft_pr_true() -> None:
    report = runner.publish_existing_issue_worktree(
        _publish_existing_issue_worktree_body(draft_pr="false")
    )

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=draft_pr_required" in report


def test_publish_target_project_issue_worktree_pr_uses_project_tree_and_target_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "lumenflow"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    worktree_path = _prepare_issue_publish_worktree(target_root)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_target_project_tree(target_root)
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            repository="alanua/LumenFlow",
            remote_url="https://github.com/alanua/LumenFlow.git",
            changed_files=("README.md",),
            commit_message="Publish target project issue #123 worktree",
        ),
    ) as run:
        report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert runner.PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert "maintenance_task_id=publish_target_project_issue_worktree_pr" in report
    assert "target_project=lumenflow" in report
    assert "repository=alanua/LumenFlow" in report
    assert "target_project_route=lumenflow:alanua/LumenFlow" in report
    assert "issue_worktree_id=issue-123" in report
    assert f"worktree_root={target_root}" not in report
    assert f"issue_worktree={worktree_path}" not in report
    assert str(tmp_path) not in report
    assert ["git", "add", "--", "README.md"] in commands
    assert [
        "git",
        "commit",
        "-m",
        "Publish target project issue #123 worktree",
    ] in commands
    assert [
        "git",
        "push",
        "origin",
        "refs/heads/runner/issue-123:refs/heads/runner/issue-123",
    ] in commands
    assert [
        "gh",
        "pr",
        "create",
        "--repo",
        "alanua/LumenFlow",
        "--base",
        "main",
        "--head",
        "runner/issue-123",
        "--title",
        "Runner task #123",
        "--body",
        "Automated Runner publish task from issue #123.",
        "--draft",
    ] in commands
    assert all(
        "alanua/Skeleton" not in command
        for command in commands
        if command[:3] == ["gh", "pr", "create"]
    )
    assert all("--force" not in command for command in commands)


def test_publish_target_project_issue_worktree_pr_rejects_issue_path_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "lumenflow"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_target_project_tree(target_root)
    ), mock.patch.object(runner, "run_command") as run:
        path_report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body(
                extra_metadata=("Worktree Path: /tmp/attacker/issue-123",)
            )
        )
        traversal_report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body(source_issue="../123")
        )

    assert path_report.startswith("NEEDS_OPERATOR:")
    assert "reason=path_input_not_allowed" in path_report
    assert traversal_report.startswith("NEEDS_OPERATOR:")
    assert "reason=missing_or_invalid_source_issue" in traversal_report
    run.assert_not_called()


def test_publish_target_project_issue_worktree_pr_rejects_mismatched_repo_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "lumenflow"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_target_project_tree(target_root)
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body(
                target_repository="alanua/Skeleton"
            )
        )

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=target_project_repository_mismatch" in report
    run.assert_not_called()


def test_publish_target_project_issue_worktree_pr_rejects_runner_disabled_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "lumenflow"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    with mock.patch.object(
        runner,
        "load_runner_project_tree",
        return_value=_target_project_tree(target_root, runner_enabled=False),
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body()
        )

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=target_project_runner_disabled" in report
    run.assert_not_called()


def test_publish_target_project_issue_worktree_pr_enforces_allowed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "lumenflow"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    worktree_path = _prepare_issue_publish_worktree(target_root)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_target_project_tree(target_root)
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            repository="alanua/LumenFlow",
            remote_url="git@github.com:alanua/LumenFlow.git",
            changed_files=("README.md", "secrets.env"),
        ),
    ) as run:
        report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=changed_tracked_files_outside_allowlist" in report
    assert "target_project_route=lumenflow:alanua/LumenFlow" in report
    assert "issue_worktree_id=issue-123" in report
    assert f"worktree_root={target_root}" not in report
    assert f"issue_worktree={worktree_path}" not in report
    assert str(tmp_path) not in report
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_target_project_issue_worktree_pr_ignores_codex_noise_and_reuses_existing_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "lumenflow"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    worktree_path = _prepare_issue_publish_worktree(target_root)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=_target_project_tree(target_root)
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            repository="alanua/LumenFlow",
            remote_url="https://github.com/alanua/LumenFlow.git",
            changed_files=("README.md",),
            untracked_files=(".codex/session.json",),
            existing_pr_url="https://github.com/alanua/LumenFlow/pull/55",
        ),
    ) as run:
        report = runner.publish_target_project_issue_worktree_pr(
            _publish_target_project_issue_worktree_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "unexpected_untracked_files_count=0" in report
    assert "existing_pr_url=https://github.com/alanua/LumenFlow/pull/55" in report
    assert not any(".codex/session.json" in command for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_issue_worktree_publish_inspection_valid_metadata_reports_done(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            changed_files=(
                "docs/RUNNER_MAINTENANCE_TASKS.md",
                "tests/test_runner_poll_github_tasks.py",
            ),
            untracked_files=(".codex/session.json",),
            raw_suffix="raw branch output must not leak",
        ),
    ):
        report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body(
                allowed_files=(
                    "docs/RUNNER_MAINTENANCE_TASKS.md",
                    "tests/test_runner_poll_github_tasks.py",
                )
            )
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=inspect_issue_worktree_for_publish" in report
    assert "source_issue=123" in report
    assert "expected_branch=runner/issue-123" in report
    assert "step=verify_origin_remote status=done" in report
    assert "changed_tracked_files_count=2" in report
    assert "changed_tracked_files=docs/RUNNER_MAINTENANCE_TASKS.md,tests/test_runner_poll_github_tasks.py" in report
    assert "unexpected_untracked_files_count=0" in report
    assert "tracked_files_match_allowlist=true" in report
    assert "raw branch output" not in report


def test_issue_worktree_publish_inspection_unsupported_repo_or_invalid_source_issue_blocks() -> None:
    unsupported_report = runner.inspect_issue_worktree_for_publish(
        _issue_publish_inspection_body(repository="alanua/Other")
    )
    invalid_issue_report = runner.inspect_issue_worktree_for_publish(
        _issue_publish_inspection_body(source_issue="../123")
    )

    assert unsupported_report.startswith("BLOCKED:")
    assert "reason=unsupported_repository" in unsupported_report
    assert invalid_issue_report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_source_issue" in invalid_issue_report


def test_issue_worktree_publish_inspection_branch_mismatch_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path, branch="runner/issue-999"
        ),
    ):
        report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    assert report.startswith("BLOCKED:")
    assert "reason=branch_mismatch" in report
    assert "current_branch=runner/issue-999" in report


def test_issue_worktree_publish_inspection_wrong_remote_blocks_before_diff(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            remote_url="https://github.com/alanua/Other.git",
        ),
    ) as run:
        report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report
    assert ["git", "diff", "--name-only", "HEAD", "--"] not in commands


def test_issue_worktree_publish_inspection_unsafe_or_missing_worktree_blocks(
    tmp_path: Path,
) -> None:
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "_issue_publish_worktree_path", return_value=Path("/tmp/outside")
    ), mock.patch.object(
        runner, "run_command"
    ) as run:
        unsafe_report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "run_command"
    ) as missing_run:
        missing_report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    assert unsafe_report.startswith("BLOCKED:")
    assert "reason=issue_worktree_path_unsafe" in unsafe_report
    assert missing_report.startswith("BLOCKED:")
    assert "reason=issue_worktree_missing" in missing_report
    run.assert_not_called()
    missing_run.assert_not_called()


def test_issue_worktree_publish_inspection_changed_files_outside_allowlist_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            changed_files=("scripts/runner_poll_github_tasks.py", "BOOT_MANIFEST.yaml"),
        ),
    ):
        report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    assert report.startswith("BLOCKED:")
    assert "tracked_files_match_allowlist=false" in report
    assert "reason=changed_tracked_files_outside_allowlist" in report


def test_issue_worktree_publish_inspection_unexpected_untracked_except_codex_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            untracked_files=(".codex/session.json", "scratch.txt"),
        ),
    ):
        report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    assert report.startswith("BLOCKED:")
    assert "unexpected_untracked_files_count=1" in report
    assert "reason=unexpected_untracked_files" in report
    assert 'unexpected_untracked_files=["scratch.txt"]' not in report


def test_issue_worktree_publish_inspection_ignores_arbitrary_commands(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    body = _issue_publish_inspection_body(
        task_body=(
            "git push origin runner/issue-123\n"
            "gh pr create --fill\n"
            "python3 -c 'open(\"/tmp/nope\", \"w\").write(\"x\")'"
        )
    )
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path),
    ) as run:
        report = runner.inspect_issue_worktree_for_publish(body)

    commands = [" ".join(call.args[0]) for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert all("push" not in command for command in commands)
    assert all("gh pr" not in command for command in commands)
    assert all("python3 -c" not in command for command in commands)


def test_issue_worktree_publish_inspection_report_does_not_leak_raw_command_output(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    leaked_output = "raw command output and token github-token-must-not-leak"

    def fail_diff(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        assert Path(cwd or "") == worktree_path
        if command == ["git", "branch", "--show-current"]:
            return 0, "runner/issue-123\n"
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "diff", "--name-only", "HEAD", "--"]:
            return 128, leaked_output
        return 2, leaked_output

    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(runner, "run_command", side_effect=fail_diff):
        report = runner.inspect_issue_worktree_for_publish(
            _issue_publish_inspection_body()
        )

    assert report.startswith("BLOCKED:")
    assert "step=read_changed_tracked_files status=failed" in report
    assert leaked_output not in report


def _quarantine_stale_worktrees_body(
    *,
    worktree_ids: tuple[str, ...] = ("issue-120",),
    protected_ids: tuple[str, ...] = (),
    repository: str | None = runner.REPO,
    task_body: str = "",
) -> str:
    metadata = []
    if repository is not None:
        metadata.append(f"Repository: {repository}")
    metadata.append("Issue Worktrees:")
    metadata.extend(f"- {worktree_id}" for worktree_id in worktree_ids)
    if protected_ids:
        metadata.append("Protected IDs:")
        metadata.extend(f"- {worktree_id}" for worktree_id in protected_ids)
    body = _maintenance_issue(
        runner.QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES,
        task_body,
        metadata="\n".join(metadata),
    )
    return str(body["body"])


def _prepare_quarantine_worktree(root: Path, worktree_id: str) -> Path:
    worktree_path = root / worktree_id
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: /tmp/git-dir\n", encoding="utf-8")
    return worktree_path


def test_quarantine_stale_clean_skeleton_worktrees_is_allowlisted() -> None:
    assert (
        runner.QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES
        == "quarantine_stale_clean_skeleton_worktrees"
    )
    assert (
        runner.QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES
        in runner.RUNTIME_MAINTENANCE_TASK_IDS
    )


def test_repair_protected_exact_source_worktree_fetches_with_120_second_timeout(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    worktree_path = tmp_path / "issue-1840"
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "target_repository_checkout_path", return_value=checkout_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_protected_exact_source_repair_commands(
            checkout_path=checkout_path,
            worktree_path=worktree_path,
        ),
    ) as run:
        report = runner.repair_protected_exact_source_worktree(
            _protected_exact_source_repair_body()
        )

    assert report.startswith("DONE:")
    assert "step=resolve_source status=done" in report
    assert "step=verify_head status=done" in report
    assert any(
        call.args[0][:3] == ["git", "fetch", "origin"]
        and call.kwargs["timeout"] == runner.PROTECTED_SOURCE_FETCH_TIMEOUT_SECONDS
        for call in run.call_args_list
    )
    assert all(
        call.kwargs.get("timeout") in {runner.PROTECTED_SOURCE_FETCH_TIMEOUT_SECONDS, runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS}
        for call in run.call_args_list
    )


def test_repair_protected_exact_source_worktree_full_sha_skips_fetch_and_bounds_reads(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    worktree_path = tmp_path / "issue-1840"
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "target_repository_checkout_path", return_value=checkout_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_protected_exact_source_repair_commands(
            checkout_path=checkout_path,
            worktree_path=worktree_path,
            source_ref=HEAD_SHA,
            expected_source_sha=HEAD_SHA,
        ),
    ) as run:
        report = runner.repair_protected_exact_source_worktree(
            _protected_exact_source_repair_body(source_ref=HEAD_SHA)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert all(command[:2] != ["git", "fetch"] for command in commands)
    assert ["git", "cat-file", "-e", f"{HEAD_SHA}^{{commit}}"] in commands
    assert all(
        call.kwargs.get("timeout") == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
        for call in run.call_args_list
    )


def test_repair_protected_exact_source_worktree_reused_checks_are_bounded_and_exact(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    worktree_path = tmp_path / "issue-1840"
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "target_repository_checkout_path", return_value=checkout_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_protected_exact_source_repair_commands(
            checkout_path=checkout_path,
            worktree_path=worktree_path,
            existing=True,
        ),
    ) as run:
        report = runner.repair_protected_exact_source_worktree(
            _protected_exact_source_repair_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "step=verify_existing_head status=done" in report
    assert ["git", "status", "--porcelain"] in commands
    assert ["git", "branch", "--show-current"] in commands
    assert ["git", "rev-parse", "HEAD"] in commands
    assert all(
        call.kwargs.get("timeout") == runner.PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS
        for call in run.call_args_list
        if tuple(call.args[0]) in {
            ("git", "status", "--porcelain"),
            ("git", "branch", "--show-current"),
            ("git", "rev-parse", "HEAD"),
        }
    )
    assert all(command[:3] != ["git", "worktree", "remove"] for command in commands)
    assert all(command[:3] != ["git", "worktree", "add"] for command in commands)


def test_repair_protected_exact_source_worktree_task_metadata_takes_precedence(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    worktree_path = tmp_path / "issue-1840"
    task_body = "\n".join(
        (
            "Source Issue: 1840",
            "Output Branch: runner/issue-1840",
            "Source Ref: refs/heads/_protected/source",
            f"Expected Source SHA: {HEAD_SHA}",
        )
    )
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "target_repository_checkout_path", return_value=checkout_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_protected_exact_source_repair_commands(
            checkout_path=checkout_path,
            worktree_path=worktree_path,
            source_ref="_protected/source",
        ),
    ) as run:
        report = runner.repair_protected_exact_source_worktree(
            _protected_exact_source_repair_body(
                source_issue=999,
                output_branch="runner/issue-999",
                source_ref="runner/issue-999",
                task_body=task_body,
            )
        )

    assert report.startswith("DONE:")
    assert "source_issue=1840" in report
    assert "source_ref=_protected/source" in report
    assert any(
        call.args[0]
        == [
            "git",
            "fetch",
            "origin",
            "_protected/source:refs/remotes/origin/_protected/source",
        ]
        for call in run.call_args_list
    )


def test_repair_protected_exact_source_worktree_rejects_raw_whitespace_reason_only() -> None:
    body = _maintenance_issue(
        runner.REPAIR_PROTECTED_EXACT_SOURCE_WORKTREE,
        metadata="\n".join(
            (
                "Target Repository: alanua/Skeleton",
                "Source Issue: 1840",
                "Output Branch: runner/issue-1840",
                "Source Ref:  runner/issue-1840",
                f"Expected Source SHA: {HEAD_SHA}",
            )
        ),
    )
    report = runner.repair_protected_exact_source_worktree(
        str(body["body"])
    )

    assert report.startswith("BLOCKED:")
    assert "reason=invalid_source_ref" in report
    assert "runner/issue-1840" not in report
    assert "fetch output" not in report


def test_repair_protected_exact_source_worktree_mismatched_expected_sha_blocks_before_add(
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    worktree_path = tmp_path / "issue-1840"
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "target_repository_checkout_path", return_value=checkout_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_protected_exact_source_repair_commands(
            checkout_path=checkout_path,
            worktree_path=worktree_path,
            resolved_sha="c" * 40,
        ),
    ) as run:
        report = runner.repair_protected_exact_source_worktree(
            _protected_exact_source_repair_body()
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=expected_source_sha_mismatch" in report
    assert all(command[:3] != ["git", "worktree", "add"] for command in commands)


def test_quarantine_stale_clean_skeleton_worktrees_removes_only_clean_matching_worktrees(
    tmp_path: Path,
) -> None:
    clean_path = _prepare_quarantine_worktree(tmp_path, "issue-120")
    dirty_path = _prepare_quarantine_worktree(tmp_path, "issue-121")
    wrong_remote_path = _prepare_quarantine_worktree(tmp_path, "issue-122")

    def run_quarantine_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == ["git", "remote", "get-url", "origin"]:
            if Path(cwd or "") == wrong_remote_path:
                return 0, "https://github.com/alanua/Other.git\n"
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "status", "--porcelain"]:
            if Path(cwd or "") == dirty_path:
                return 0, " M scripts/runner_poll_github_tasks.py\n"
            return 0, ""
        if command == ["git", "worktree", "remove", str(clean_path)]:
            return 0, ""
        return 2, "unexpected command output must not leak"

    with mock.patch.object(runner, "DEFAULT_WORKTREE_ROOT", tmp_path), mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "run_command", side_effect=run_quarantine_command
    ) as run:
        report = runner.quarantine_stale_clean_skeleton_worktrees(
            _quarantine_stale_worktrees_body(
                worktree_ids=("issue-120", "issue-121", "issue-122", "issue-123"),
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "removed_worktrees_count=1" in report
    assert "skipped_worktrees_count=3" in report
    assert "worktree=issue-120 action=removed" in report
    assert "worktree=issue-121 action=skipped reason=dirty" in report
    assert "worktree=issue-122 action=skipped reason=wrong_remote" in report
    assert "worktree=issue-123 action=skipped reason=missing" in report
    assert commands == [
        ["git", "remote", "get-url", "origin"],
        ["git", "status", "--porcelain"],
        ["git", "worktree", "remove", str(clean_path)],
        ["git", "remote", "get-url", "origin"],
        ["git", "status", "--porcelain"],
        ["git", "remote", "get-url", "origin"],
    ]


def test_quarantine_stale_clean_skeleton_worktrees_reports_remove_128_stderr(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_quarantine_worktree(tmp_path, "issue-128")

    def run_quarantine_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "worktree", "remove", str(worktree_path)]:
            return (
                128,
                "fatal: validation failed for worktree remove\n"
                "SECRET_TOKEN=must-not-leak\n",
            )
        if command == ["git", "worktree", "list", "--porcelain"]:
            return 0, f"worktree {worktree_path}\nHEAD abc123\nbranch refs/heads/x\n"
        return 2, "unexpected command output must not leak"

    with mock.patch.object(runner, "DEFAULT_WORKTREE_ROOT", tmp_path), mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "run_command", side_effect=run_quarantine_command
    ) as run:
        report = runner.quarantine_stale_clean_skeleton_worktrees(
            _quarantine_stale_worktrees_body(worktree_ids=("issue-128",))
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "worktree=issue-128 action=remove_failed exit_code=128" in report
    assert "remove_stderr_start" not in report
    assert "fatal: validation failed for worktree remove" not in report
    assert "[redacted environment variable]" not in report
    assert "must-not-leak" not in report
    assert "remove_stderr_end" not in report
    assert "git_worktree_list_registers_path=true" in report
    assert commands == [
        ["git", "remote", "get-url", "origin"],
        ["git", "status", "--porcelain"],
        ["git", "worktree", "remove", str(worktree_path)],
        ["git", "worktree", "list", "--porcelain"],
    ]


def test_quarantine_stale_clean_skeleton_worktrees_skips_unregistered_not_working_tree(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_quarantine_worktree(tmp_path, "issue-794")

    def run_quarantine_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "worktree", "remove", str(worktree_path)]:
            return 128, f"fatal: '{worktree_path}' is not a working tree\n"
        if command == ["git", "worktree", "list", "--porcelain"]:
            return 0, f"worktree {tmp_path / 'issue-120'}\nHEAD abc123\n"
        return 2, "unexpected command output must not leak"

    with mock.patch.object(runner, "DEFAULT_WORKTREE_ROOT", tmp_path), mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "run_command", side_effect=run_quarantine_command
    ) as run:
        report = runner.quarantine_stale_clean_skeleton_worktrees(
            _quarantine_stale_worktrees_body(worktree_ids=("issue-794",))
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "worktree=issue-794 action=skipped_unregistered exit_code=128" in report
    assert f"fatal: '{worktree_path}' is not a working tree" not in report
    assert "git_worktree_list_registers_path=false" in report
    assert "removed_worktrees_count=0" in report
    assert "skipped_worktrees_count=1" in report
    assert all(command[0] != "rm" for command in commands)
    assert commands == [
        ["git", "remote", "get-url", "origin"],
        ["git", "status", "--porcelain"],
        ["git", "worktree", "remove", str(worktree_path)],
        ["git", "worktree", "list", "--porcelain"],
    ]


def test_quarantine_stale_clean_skeleton_worktrees_skips_protected_without_commands(
    tmp_path: Path,
) -> None:
    _prepare_quarantine_worktree(tmp_path, "issue-834")
    with mock.patch.object(runner, "DEFAULT_WORKTREE_ROOT", tmp_path), mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.quarantine_stale_clean_skeleton_worktrees(
            _quarantine_stale_worktrees_body(
                worktree_ids=("issue-834",),
                protected_ids=("issue-834",),
            )
        )

    assert report.startswith("DONE:")
    assert "worktree=issue-834 action=skipped reason=protected" in report
    assert "removed_worktrees_count=0" in report
    run.assert_not_called()


def test_quarantine_stale_clean_skeleton_worktrees_blocks_invalid_metadata() -> None:
    unsafe_path_report = runner.quarantine_stale_clean_skeleton_worktrees(
        _quarantine_stale_worktrees_body(worktree_ids=("../issue-120",))
    )
    unsupported_repo_report = runner.quarantine_stale_clean_skeleton_worktrees(
        _quarantine_stale_worktrees_body(repository="alanua/Other")
    )

    assert unsafe_path_report.startswith("BLOCKED:")
    assert "reason=invalid_worktree_ids" in unsafe_path_report
    assert unsupported_repo_report.startswith("BLOCKED:")
    assert "reason=unsupported_repository" in unsupported_repo_report


def test_quarantine_stale_clean_skeleton_worktrees_ignores_command_text(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_quarantine_worktree(tmp_path, "issue-120")

    def run_quarantine_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == ["git", "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "worktree", "remove", str(worktree_path)]:
            return 0, ""
        return 2, "unexpected command output must not leak"

    with mock.patch.object(runner, "DEFAULT_WORKTREE_ROOT", tmp_path), mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner, "run_command", side_effect=run_quarantine_command
    ) as run:
        report = runner.quarantine_stale_clean_skeleton_worktrees(
            _quarantine_stale_worktrees_body(
                task_body="rm -rf /home/agent/agent-dev/worktrees/skeleton\n"
                "git worktree remove /tmp/unsafe\n"
                "TOKEN=must-not-leak",
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "TOKEN" not in report
    assert all(command[0] != "rm" for command in commands)
    assert ["git", "worktree", "remove", "/tmp/unsafe"] not in commands


def _issue_publish_body(**kwargs: object) -> str:
    return _issue_publish_inspection_body(
        task_id=runner.PUBLISH_ISSUE_WORKTREE_PR, **kwargs
    )


def test_publish_issue_worktree_pr_is_allowlisted_runtime_task() -> None:
    assert runner.PUBLISH_ISSUE_WORKTREE_PR in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_publish_issue_worktree_pr_missing_or_invalid_metadata_blocks() -> None:
    missing_issue = runner.publish_issue_worktree_pr(
        _issue_publish_body(source_issue="", expected_branch="runner/issue-123")
    )
    missing_repository = runner.publish_issue_worktree_pr(
        _issue_publish_body(repository=None)
    )
    invalid_branch = runner.publish_issue_worktree_pr(
        _issue_publish_body(source_issue=123, expected_branch="runner/issue-999")
    )

    assert missing_issue.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_source_issue" in missing_issue
    assert missing_repository.startswith("BLOCKED:")
    assert "reason=unsupported_repository" in missing_repository
    assert invalid_branch.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_expected_branch" in invalid_branch


def test_publish_issue_worktree_pr_blocks_unsupported_repository() -> None:
    report = runner.publish_issue_worktree_pr(
        _issue_publish_body(repository="alanua/Other")
    )

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_repository" in report


def test_publish_issue_worktree_pr_blocks_unsafe_allowed_file() -> None:
    report = runner.publish_issue_worktree_pr(
        _issue_publish_body(allowed_files=("../secrets.env",))
    )

    assert report.startswith("BLOCKED:")
    assert "reason=invalid_allowed_files" in report


def test_publish_issue_worktree_pr_branch_mismatch_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path, branch="runner/issue-999"
        ),
    ):
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    assert report.startswith("BLOCKED:")
    assert "reason=branch_mismatch" in report


def test_publish_issue_worktree_pr_changed_files_outside_allowlist_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            changed_files=("scripts/runner_poll_github_tasks.py", "BOOT_MANIFEST.yaml"),
        ),
    ):
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    assert report.startswith("BLOCKED:")
    assert "reason=changed_tracked_files_outside_allowlist" in report


def test_publish_issue_worktree_pr_unexpected_untracked_file_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            untracked_files=(".codex/session.json", "scratch.txt"),
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "unexpected_untracked_files_count=1" in report
    assert 'unexpected_untracked_files=["scratch.txt"]' not in report
    assert "reason=unexpected_untracked_files" in report
    assert all(command[:2] != ["git", "add"] for command in commands)
    assert all(command[:2] != ["git", "commit"] for command in commands)


def test_publish_issue_worktree_pr_allowlisted_untracked_file_is_committed(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    validated_publish_files = (
        "scripts/runner_poll_github_tasks.py",
        "tests/test_runner_poll_github_tasks.py",
    )
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            untracked_files=(
                ".codex/session.json",
                "tests/test_runner_poll_github_tasks.py",
            ),
            validated_publish_files=validated_publish_files,
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(
            _issue_publish_body(allowed_files=validated_publish_files)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "unexpected_untracked_files_count=0" in report
    assert "allowed_untracked_files_count=1" in report
    assert (
        'allowed_untracked_files=["tests/test_runner_poll_github_tasks.py"]'
        not in report
    )
    assert "validated_publish_files_count=2" in report
    assert ["git", "add", "--", *validated_publish_files] in commands
    assert ["git", "diff", "--check", "--", *validated_publish_files] in commands


def test_publish_issue_worktree_pr_wrong_remote_blocks_before_push(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            remote_url="git@github.com:alanua/Other.git",
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_issue_worktree_pr_existing_pr_returns_done_without_duplicate(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            existing_pr_url=PR_URL,
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert f"existing_pr_url={PR_URL}" in report
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "create"] for command in commands)
    assert all(command[:2] != ["git", "commit"] for command in commands)


def test_publish_issue_worktree_pr_diff_check_failure_blocks_before_staging(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            diff_check_code=1,
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=diff_check_failed" in report
    assert ["git", "add", "--", "scripts/runner_poll_github_tasks.py"] not in commands
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_issue_worktree_pr_staging_failure_blocks_before_commit(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path, add_code=1),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=staging_failed" in report
    assert all(command[:2] != ["git", "commit"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_issue_worktree_pr_commit_failure_blocks_before_push(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path, commit_code=1),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=commit_failed" in report
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_issue_worktree_pr_blocks_if_commit_head_does_not_move(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    head = "1111111111111111111111111111111111111111"
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            pre_commit_head=head,
            post_commit_head=head,
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=branch_head_did_not_move" in report
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_issue_worktree_pr_no_uncommitted_changes_with_branch_diff_publishes(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            changed_files=(),
            branch_diff_code=1,
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "step=read_branch_diff status=done" in report
    assert all(command[:2] != ["git", "commit"] for command in commands)
    assert [
        "git",
        "push",
        "origin",
        "refs/heads/runner/issue-123:refs/heads/runner/issue-123",
    ] in commands


def test_publish_issue_worktree_pr_no_publishable_changes_blocks(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path,
            changed_files=(),
            branch_diff_code=0,
        ),
    ) as run:
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=no_publishable_changes" in report
    assert all(command[:2] != ["git", "commit"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_publish_issue_worktree_pr_push_failure_returns_blocked(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path, push_code=1),
    ):
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    assert report.startswith("BLOCKED:")
    assert "step=push_expected_branch status=failed" in report
    assert "push failed output" not in report


def test_publish_issue_worktree_pr_create_failure_returns_blocked(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(
            worktree_path=worktree_path, pr_create_code=1
        ),
    ):
        report = runner.publish_issue_worktree_pr(_issue_publish_body())

    assert report.startswith("BLOCKED:")
    assert "step=create_draft_pr status=failed" in report


def test_publish_issue_worktree_pr_success_pushes_branch_and_creates_draft_pr(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path),
    ) as run:
        report = runner.publish_issue_worktree_pr(
            _issue_publish_body(pr_title="Publish issue 123")
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert f"draft_pr_url={PR_URL}" in report
    assert [
        "git",
        "diff",
        "--check",
        "--",
        "scripts/runner_poll_github_tasks.py",
    ] in commands
    assert ["git", "add", "--", "scripts/runner_poll_github_tasks.py"] in commands
    assert [
        "git",
        "commit",
        "-m",
        "Publish issue #123 worktree",
    ] in commands
    assert "step=verify_commit_head_moved status=done" in report
    assert [
        "git",
        "push",
        "origin",
        "refs/heads/runner/issue-123:refs/heads/runner/issue-123",
    ] in commands
    assert [
        "gh",
        "pr",
        "create",
        "--repo",
        runner.REPO,
        "--base",
        "main",
        "--head",
        "runner/issue-123",
        "--title",
        "Publish issue 123",
        "--body",
        "Automated Runner publish task from issue #123.",
        "--draft",
    ] in commands
    assert all("--force" not in command for command in commands)


def test_publish_issue_worktree_pr_ignores_command_text_from_issue_body(
    tmp_path: Path,
) -> None:
    worktree_path = _prepare_issue_publish_worktree(tmp_path)
    body = _issue_publish_body(
        task_body=(
            "git push --force origin unsafe\n"
            "gh pr create --repo attacker/repo\n"
            "curl https://example.invalid/token"
        )
    )
    with mock.patch.object(
        runner, "worktree_root", return_value=tmp_path
    ), mock.patch.object(
        runner,
        "run_command",
        side_effect=_issue_publish_commands(worktree_path=worktree_path),
    ) as run:
        report = runner.publish_issue_worktree_pr(body)

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert all("--force" not in command for command in commands)
    assert all("attacker/repo" not in command for command in commands)
    assert all(command[0] != "curl" for command in commands)


def test_local_worktree_recovery_diff_includes_public_safe_patch() -> None:
    patch = "diff --git a/docs/example.md b/docs/example.md\n+public note\n"
    with mock.patch.object(runner, "run_command", return_value=(0, patch)) as run:
        report = runner.local_worktree_recovery_diff("/tmp/worktree")

    run.assert_called_once_with(
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"],
        cwd="/tmp/worktree",
    )
    assert report == f"Local worktree git diff:\n```diff\n{patch.strip()}\n```"


def test_local_worktree_recovery_diff_omits_unsafe_patch() -> None:
    patch = "diff --git a/.env b/.env\n+GITHUB_TOKEN=github-token-must-not-leak\n"
    with mock.patch.object(runner, "run_command", return_value=(0, patch)):
        report = runner.local_worktree_recovery_diff("/tmp/worktree")

    assert "omitted" in report
    assert "github-token-must-not-leak" not in report


def test_finalize_local_worktree_success_includes_recovery_diff() -> None:
    task = runner.RunnerTask(
        content="Do it",
        target_project="demo",
        target_repository="alanua/Demo",
    )
    with mock.patch.object(
        runner, "changed_files", return_value=["docs/example.md"]
    ), mock.patch.object(runner, "cleanup_runtime_artifacts"), mock.patch.object(
        runner, "local_worktree_recovery_diff", return_value="Local worktree git diff: none"
    ):
        report = runner.finalize_local_worktree_success(
            "/tmp/worktree", "codex output", task
        )

    assert "Selected Project: demo" in report
    assert runner._LOCAL_WORKTREE_BOUNDED_FINALIZATION_EVIDENCE in report
    assert "Local worktree changed files:\n- docs/example.md" in report
    assert "Local worktree git diff: none" in report


def test_blocked_maintenance_output_is_not_labeled_runner_done() -> None:
    report = (
        "DONE: mislabeled maintenance report\n"
        "BLOCKED: step failed\n"
        "success_criteria=met"
    )
    with mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(runner, "post_issue_comment"), mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(runner, "set_issue_label") as set_label:
        runner.process_runtime_maintenance_issue(
            145, runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, str(runner.ROOT)
        )

    set_label.assert_called_once_with(145, runner.LABEL_RUNNING, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(145, "BLOCKED", report)


def test_needs_operator_maintenance_output_records_and_notifies_exact_status() -> None:
    report = (
        "NEEDS_OPERATOR: Runner host maintenance task needs operator action.\n"
        "maintenance_task_id=publish_existing_issue_worktree\n"
        "reason=missing_operator_approval\n"
        "success_criteria=not_met"
    )
    with mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(
        runner, "record_runner_executor_result", return_value=None
    ) as record, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label:
        runner.process_runtime_maintenance_issue(
            145, runner.PUBLISH_EXISTING_ISSUE_WORKTREE, str(runner.ROOT)
        )

    record.assert_called_once_with(
        145,
        "skeleton",
        "NEEDS_OPERATOR",
        "NEEDS_OPERATOR",
        "maintenance",
        report,
    )
    set_label.assert_called_once_with(145, runner.LABEL_RUNNING, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(145, "NEEDS_OPERATOR", report)


def test_maintenance_report_status_blocks_middle_top_level_blocked() -> None:
    report = (
        "DONE: maintenance step returned\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "BLOCKED: step failed\n"
        "success_criteria=met"
    )

    assert runner.maintenance_report_status(report) == "BLOCKED"


def test_maintenance_report_status_blocks_middle_duplicate_done() -> None:
    report = (
        "DONE: maintenance step returned\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "DONE: duplicate status\n"
        "success_criteria=met"
    )

    assert runner.maintenance_report_status(report) == "BLOCKED"


def test_maintenance_report_status_ignores_blocked_diagnostic_assignment() -> None:
    report = (
        "DONE: maintenance step returned\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "blocked_write_status=BLOCKED\n"
        "success_criteria=met"
    )

    assert runner.maintenance_report_status(report) == "DONE"


def test_maintenance_report_status_ignores_done_diagnostic_assignment() -> None:
    report = (
        "BLOCKED: maintenance step failed\n"
        "maintenance_task_id=private_memory_healthcheck\n"
        "private_memory_status=DONE\n"
        "success_criteria=not_met"
    )

    assert runner.maintenance_report_status(report) == "BLOCKED"


def test_maintenance_report_status_accepts_single_last_line_status() -> None:
    report = (
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "success_criteria=not_met\n"
        "NEEDS_OPERATOR: approve runtime action"
    )

    assert runner.maintenance_report_status(report) == "NEEDS_OPERATOR"


def test_maintenance_report_status_blocks_missing_top_level_status() -> None:
    report = (
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "blocked_write_status=BLOCKED\n"
        "success_criteria=not_met"
    )

    assert runner.maintenance_report_status(report) == "BLOCKED"


def test_maintenance_report_is_done_requires_success_criteria_met() -> None:
    report = (
        "DONE: maintenance step returned\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "success_criteria=not_met"
    )

    assert runner.maintenance_report_status(report) == "DONE"
    assert runner.maintenance_report_is_done(report) is False


def test_maintenance_report_sanitizer_drops_multiline_status_line() -> None:
    report = runner._maintenance_report(
        "BLOCKED",
        runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        ["reason=maintenance_step_raised\nstatus=BLOCKED", "reason=maintenance_step_raised"],
        "not_met",
    )

    assert "reason=maintenance_step_raised\nstatus=BLOCKED" not in report
    assert "reason=maintenance_step_raised" in report


def test_maintenance_report_sanitizer_drops_failed_command() -> None:
    report = runner._maintenance_report(
        "BLOCKED",
        runner.VALIDATE_PR_BRANCH,
        [
            "step=validation_profile_command_1 status=failed exit_code=1",
            "failed_command=python3 -m pytest -q tests/test_knowledge_intake.py",
            "validation_command_text=python3_-m_pytest_-q_tests/test_knowledge_intake.py",
            "validation_output_tail=AssertionError:_safe_summary",
        ],
        "not_met",
    )

    assert "failed_command=" not in report
    assert "validation_command_text=python3_-m_pytest_-q_tests/test_knowledge_intake.py" in report
    assert "validation_output_tail=AssertionError:_safe_summary" in report
    assert "step=validation_profile_command_1 status=failed exit_code=1" in report


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "github-token-must-not-leak",
        "client-secret-value",
        "api_key-value",
        "access_key-value",
        "db-password-value",
        "credential-value",
        "private_key-value",
        "bearer-value",
        "authorization-value",
    ],
)
def test_maintenance_report_sanitizer_drops_sensitive_values(
    sensitive_value: str,
) -> None:
    report = runner._maintenance_report(
        "BLOCKED",
        runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        [f"reason={sensitive_value}", "reason=maintenance_step_raised"],
        "not_met",
    )

    assert sensitive_value not in report
    assert "reason=maintenance_step_raised" in report


def test_maintenance_privileged_commands_are_non_interactive() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    privileged_commands = [
        call.args[0] for call in run.call_args_list if call.args[0][0] == "sudo"
    ]
    assert privileged_commands
    assert all(command[:2] == ["sudo", "-n"] for command in privileged_commands)


def test_copied_callback_units_are_owned_by_root_and_read_only() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    service_unit = f"/etc/systemd/system/{runner.TELEGRAM_CALLBACK_POLLER_SERVICE}"
    timer_unit = f"/etc/systemd/system/{runner.TELEGRAM_CALLBACK_POLLER_TIMER}"

    assert report.startswith("DONE:")
    assert ["sudo", "-n", "chown", "root:root", service_unit] in commands
    assert ["sudo", "-n", "chown", "root:root", timer_unit] in commands
    assert ["sudo", "-n", "chmod", "0644", service_unit] in commands
    assert ["sudo", "-n", "chmod", "0644", timer_unit] in commands


def test_local_callback_config_task_maintains_only_callback_hmac_setting() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run:
        report = runner.dispatch_runtime_maintenance_task(
            runner.ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG, str(runner.ROOT)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "step=verify_callback_hmac_secret" not in report
    assert ["sudo", "-n", "touch", runner.TELEGRAM_CALLBACK_LOCAL_CONFIG] in commands
    assert [
        "sudo",
        "-n",
        "chown",
        "root:root",
        runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
    ] in commands
    assert [
        "sudo",
        "-n",
        "chmod",
        "0600",
        runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
    ] in commands
    python_commands = [
        command for command in commands if command[2:4] == ["python3", "-c"]
    ]
    assert len(python_commands) == 2
    assert all(
        command[-2:]
        == [
            runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
            runner.TELEGRAM_CALLBACK_HMAC_ENV,
        ]
        for command in python_commands
    )


def test_local_callback_config_task_reports_blocked_on_verification_failure() -> None:
    def fail_config_verification(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        if command[-2:] == [
            runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
            runner.TELEGRAM_CALLBACK_HMAC_ENV,
        ] and runner._VERIFY_CALLBACK_HMAC_SCRIPT in command:
            return 1, "local config value must not be reported"
        return 0, ""

    with mock.patch.object(runner, "run_command", side_effect=fail_config_verification):
        report = runner.ensure_telegram_callback_local_config()

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_hmac_secret" not in report
    assert "local config value" not in report


def _project_tree_for_checkout(project_id: str, checkout_path: Path) -> dict[str, object]:
    project_tree = _project_tree_with(
        project_id,
        repo="alanua/CheckoutTest",
        runner_enabled=True,
        planning_only=False,
        codex_issue_worktree=True,
        live_cross_repo=False,
    )
    project_tree["projects"][project_id]["checkout_path"] = str(checkout_path)
    return project_tree


def _checkout_issue_body(project_id: str = "checkout_test") -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runner.CHECK_PROJECT_CHECKOUT}",
            f"Target Project: {project_id}",
        )
    )


def _ensure_checkout_issue_body(project_id: str = "checkout_test") -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runner.ENSURE_PROJECT_CHECKOUT}",
            f"Target Project: {project_id}",
        )
    )


def _skeleton_freshness_issue_body(task_body: str = "") -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.CHECK_SKELETON_FRESHNESS}",
    ]
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _runtime_sync_main_issue_body(
    expected_head_sha: str | None = None, task_body: str = ""
) -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.RUNTIME_SYNC_MAIN}",
    ]
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _recover_skeleton_checkout_issue_body(
    expected_head_sha: str | None = None, task_body: str = ""
) -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.RECOVER_SKELETON_CHECKOUT}",
    ]
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _project_tree_for_skeleton_checkout(checkout_path: Path) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"]["skeleton"]["checkout_path"] = str(checkout_path)
    project_tree["projects"]["skeleton"]["repo"] = runner.REPO
    return project_tree


def _validate_pr_issue_body(
    *,
    repository: str | None = None,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    expected_base_sha: str | None = "b" * 40,
    profile: str | None = "full_pytest",
    task_body: str = "",
) -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.VALIDATE_PR_BRANCH}",
    ]
    if repository is not None:
        lines.append(f"Repository: {repository}")
    if pr_number is not None:
        lines.append(f"Pull Request: {pr_number}")
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    if expected_base_sha is not None:
        lines.append(f"Expected Base SHA: {expected_base_sha}")
    if profile is not None:
        lines.append(f"Validation Profile: {profile}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _validation_metadata_command(
    command: list[str], cwd: str | Path | None, validation_path: Path
) -> tuple[int, str] | None:
    if cwd != validation_path:
        return None
    try:
        os.makedirs(validation_path / ".git", exist_ok=True)
    except OSError:
        pass
    if command == ["git", "diff", "--name-only", "b" * 40, "HEAD", "--"]:
        return 0, "scripts/runner_poll_github_tasks.py\n"
    if command == ["python3", "-m", "pytest", "--version"]:
        return 0, "pytest 8.0.0\n"
    if command == ["git", "rev-parse", "--is-inside-work-tree"]:
        return 0, "true\n"
    if command == ["git", "rev-parse", "--path-format=absolute", "--git-dir"]:
        return 0, f"{validation_path / '.git'}\n"
    if command == ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]:
        return 0, f"{validation_path / '.git'}\n"
    if command == ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"]:
        return 0, f"{validation_path / '.git' / 'index'}\n"
    if command == ["git", "status", "--short"]:
        return 0, ""
    return None


def _preflight_pr_issue_body(
    *,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    task_body: str = "",
) -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.PREFLIGHT_PR_REFRESH}",
    ]
    if pr_number is not None:
        lines.append(f"Pull Request: {pr_number}")
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _project_tree_for_aufmass_private(
    checkout_path: Path, workspace_root: Path
) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"][runner.AUFMASS_PRIVATE_PROJECT_ID] = {
        "repo": runner.AUFMASS_PRIVATE_REGISTERED_REPO,
        "checkout_path": str(checkout_path),
        "worktree_root": str(workspace_root),
        "public": False,
        "runner_enabled": True,
        "execution_modes": {
            "planning_only": False,
            "codex_issue_worktree": True,
            "live_cross_repo": False,
        },
        "requires_explicit_approval_for_mode_change": True,
        "future_parallel_worktrees": False,
        "runtime_approval_required": True,
        "worktree_name_prefix": "aufmass-private",
        "description": "Private Aufmass route kept outside public artifacts.",
    }
    return project_tree


def test_hermes_worker_preflight_is_allowlisted() -> None:
    assert runner.HERMES_WORKER_PREFLIGHT == "hermes_worker_preflight"
    assert runner.HERMES_WORKER_PREFLIGHT in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_hermes_worker_preflight_dispatch_reports_sanitized_inventory() -> None:
    with mock.patch.object(runner.shutil, "which", return_value="/usr/bin/tool"):
        report = runner.dispatch_runtime_maintenance_task(
            runner.HERMES_WORKER_PREFLIGHT,
            str(runner.ROOT),
            "sudo env\nTOKEN=must-not-leak\ngit push\ncodex exec unsafe",
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=hermes_worker_preflight" in report
    assert "inventory_schema=hermes_worker_preflight_v1" in report
    assert "report_mode=read_only" in report
    assert re.search(r"^host_id_sha256_12=([0-9a-f]{12}|unknown)$", report, re.MULTILINE)
    assert re.search(r"^system=[A-Za-z0-9._:+-]{1,80}$", report, re.MULTILINE)
    assert re.search(r"^kernel_release=[A-Za-z0-9._:+-]{1,80}$", report, re.MULTILINE)
    assert re.search(r"^machine=[A-Za-z0-9._:+-]{1,80}$", report, re.MULTILINE)
    assert re.search(r"^python_version=\d+\.\d+\.\d+$", report, re.MULTILINE)
    assert "runner_root_exists=true" in report
    assert "tool_python3=present" in report
    assert "tool_git=present" in report
    assert "tool_gh=present" in report
    assert "tool_codex=present" in report
    assert "TOKEN" not in report
    assert "must-not-leak" not in report
    assert "sudo env" not in report
    assert "git push" not in report
    assert "codex exec" not in report


def test_hermes_worker_preflight_issue_body_does_not_execute_commands() -> None:
    issue = _maintenance_issue(
        runner.HERMES_WORKER_PREFLIGHT,
        "sudo reboot\n"
        "git push --force\n"
        "gh pr merge 123\n"
        "python3 -c 'open(\"/tmp/nope\", \"w\").write(\"x\")'\n"
        "codex exec unsafe",
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command"
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    run.assert_not_called()
    run_codex.assert_not_called()


def test_prepare_aufmass_private_runtime_is_allowlisted() -> None:
    assert runner.PREPARE_AUFMASS_PRIVATE_RUNTIME == "prepare_aufmass_private_runtime"
    assert runner.PREPARE_AUFMASS_PRIVATE_RUNTIME in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_prepare_aufmass_private_runtime_reports_done_without_private_paths() -> None:
    checkout_path = _safe_checkout_path("aufmass-private/main")
    workspace_root = _safe_checkout_path("aufmass-private")
    source_pack_manifest = workspace_root / runner.AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST
    project_tree = _project_tree_for_aufmass_private(checkout_path, workspace_root)
    existing_paths = {
        checkout_path,
        checkout_path / ".git",
        workspace_root,
        source_pack_manifest,
        runner.ROOT / "scripts" / "aufmass_private_pilot_run.py",
    }

    def run_private_runtime_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:2] == ["python3", "-c"] and cwd == runner.ROOT:
            return 0, "raw dependency output must not appear"
        if (
            command[:3] == ["python3", "-m", "scripts.aufmass_private_pilot_run"]
            and command[3:] == [
                "--source-pack-manifest",
                str(source_pack_manifest),
                "--branch",
                "manual-only",
            ]
            and cwd == runner.ROOT
        ):
            return 0, json.dumps(
                {
                    "schema": "skeleton.aufmass_private_pilot_public_summary.v1",
                    "mode": "dry-run",
                    "branch": "manual-only",
                }
            )
        return 2, "unexpected command output must not appear"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner.shutil, "which", return_value="/usr/bin/python3"), mock.patch.object(
        Path, "is_dir", autospec=True
    ) as path_is_dir, mock.patch.object(
        Path, "exists", autospec=True
    ) as path_exists, mock.patch.object(
        Path, "is_file", autospec=True
    ) as path_is_file, mock.patch.object(
        runner, "run_command", side_effect=run_private_runtime_command
    ) as run:
        path_is_dir.side_effect = lambda path: path in {checkout_path, workspace_root}
        path_exists.side_effect = lambda path: path in existing_paths
        path_is_file.side_effect = lambda path: path in existing_paths
        report = runner.dispatch_runtime_maintenance_task(
            runner.PREPARE_AUFMASS_PRIVATE_RUNTIME,
            str(runner.ROOT),
            "sudo reboot\ncat /private/path\ncalculate quantities",
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=prepare_aufmass_private_runtime" in report
    assert "private_workspace=registered" not in report
    assert "report_private_paths=false" in report
    assert "report_drawings=false" in report
    assert "report_quantities=false" in report
    assert "step=verify_required_python_modules status=done" in report
    assert "step=verify_dxf_python_module status=done" in report
    assert "step=dry_run_private_pilot status=done" in report
    assert "pilot_mode=dry-run" in report
    assert str(workspace_root) not in report
    assert str(checkout_path) not in report
    assert "raw dependency output" not in report
    assert "sudo reboot" not in report
    assert "calculate quantities" not in report
    commands = [call.args[0] for call in run.call_args_list]
    assert len(commands) == 3
    assert commands[-1] == [
        "python3",
        "-m",
        "scripts.aufmass_private_pilot_run",
        "--source-pack-manifest",
        str(source_pack_manifest),
        "--branch",
        "manual-only",
    ]


def test_prepare_aufmass_private_runtime_dry_run_uses_module_invocation() -> None:
    checkout_path = _safe_checkout_path("aufmass-private/main")
    workspace_root = _safe_checkout_path("aufmass-private")
    source_pack_manifest = workspace_root / runner.AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST
    project_tree = _project_tree_for_aufmass_private(checkout_path, workspace_root)
    existing_paths = {
        checkout_path,
        checkout_path / ".git",
        workspace_root,
        source_pack_manifest,
        runner.ROOT / "scripts" / "aufmass_private_pilot_run.py",
    }

    def run_private_runtime_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:2] == ["python3", "-c"] and cwd == runner.ROOT:
            return 0, ""
        if command == [
            "python3",
            "-m",
            "scripts.aufmass_private_pilot_run",
            "--source-pack-manifest",
            str(source_pack_manifest),
            "--branch",
            "manual-only",
        ] and cwd == runner.ROOT:
            return 0, json.dumps(
                {
                    "schema": "skeleton.aufmass_private_pilot_public_summary.v1",
                    "mode": "dry-run",
                }
            )
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner.shutil, "which", return_value="/usr/bin/python3"), mock.patch.object(
        Path, "is_dir", autospec=True
    ) as path_is_dir, mock.patch.object(
        Path, "exists", autospec=True
    ) as path_exists, mock.patch.object(
        Path, "is_file", autospec=True
    ) as path_is_file, mock.patch.object(
        runner, "run_command", side_effect=run_private_runtime_command
    ) as run:
        path_is_dir.side_effect = lambda path: path in {checkout_path, workspace_root}
        path_exists.side_effect = lambda path: path in existing_paths
        path_is_file.side_effect = lambda path: path in existing_paths
        report = runner.prepare_aufmass_private_runtime()

    assert report.startswith("DONE:")
    dry_run_commands = [
        call.args[0]
        for call in run.call_args_list
        if call.args[0][:3] == ["python3", "-m", "scripts.aufmass_private_pilot_run"]
    ]
    assert dry_run_commands == [
        [
            "python3",
            "-m",
            "scripts.aufmass_private_pilot_run",
            "--source-pack-manifest",
            str(source_pack_manifest),
            "--branch",
            "manual-only",
        ]
    ]
    assert all(
        str(runner.ROOT / "scripts" / "aufmass_private_pilot_run.py") not in command
        for call in run.call_args_list
        for command in call.args[0]
    )


def test_prepare_aufmass_private_runtime_missing_inventory_blocks_without_commands() -> None:
    checkout_path = _safe_checkout_path("aufmass-private/main")
    workspace_root = _safe_checkout_path("aufmass-private")
    project_tree = _project_tree_for_aufmass_private(checkout_path, workspace_root)

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner.shutil, "which", return_value="/usr/bin/python3"), mock.patch.object(
        Path, "is_dir", autospec=True, return_value=False
    ), mock.patch.object(
        runner, "run_command"
    ) as run:
        report = runner.prepare_aufmass_private_runtime()

    assert report.startswith("BLOCKED:")
    assert "reason=private_checkout_unavailable" in report
    assert str(workspace_root) not in report
    assert str(checkout_path) not in report
    run.assert_not_called()


def test_prepare_aufmass_private_runtime_issue_body_does_not_execute_commands() -> None:
    issue = _maintenance_issue(
        runner.PREPARE_AUFMASS_PRIVATE_RUNTIME,
        "sudo reboot\n"
        "git push --force\n"
        "gh pr merge 123\n"
        "python3 -c 'open(\"/tmp/nope\", \"w\").write(\"x\")'\n"
        "codex exec unsafe",
    )
    checkout_path = _safe_checkout_path("aufmass-private/main")
    workspace_root = _safe_checkout_path("aufmass-private")
    source_pack_manifest = workspace_root / runner.AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST
    project_tree = _project_tree_for_aufmass_private(checkout_path, workspace_root)
    existing_paths = {
        checkout_path,
        checkout_path / ".git",
        workspace_root,
        source_pack_manifest,
        runner.ROOT / "scripts" / "aufmass_private_pilot_run.py",
    }

    def run_private_runtime_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:2] == ["python3", "-c"] and cwd == runner.ROOT:
            return 0, ""
        if command[:3] == [
            "python3",
            "-m",
            "scripts.aufmass_private_pilot_run",
        ]:
            return 0, json.dumps(
                {
                    "schema": "skeleton.aufmass_private_pilot_public_summary.v1",
                    "mode": "dry-run",
                }
            )
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner.shutil, "which", return_value="/usr/bin/python3"), mock.patch.object(
        Path, "is_dir", autospec=True
    ) as path_is_dir, mock.patch.object(
        Path, "exists", autospec=True
    ) as path_exists, mock.patch.object(
        Path, "is_file", autospec=True
    ) as path_is_file, mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_private_runtime_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        path_is_dir.side_effect = lambda path: path in {checkout_path, workspace_root}
        path_exists.side_effect = lambda path: path in existing_paths
        path_is_file.side_effect = lambda path: path in existing_paths
        runner.process_issue(issue, workdir=str(runner.ROOT))

    command_words = [" ".join(call.args[0]) for call in run.call_args_list]
    assert all("reboot" not in command for command in command_words)
    assert all("push" not in command for command in command_words)
    assert all("pr merge" not in command for command in command_words)
    assert all("open(" not in command for command in command_words)
    assert all("codex exec" not in command for command in command_words)
    run_codex.assert_not_called()


def _aufmass_private_source_pack(pack_id: str = "pack_token") -> dict[str, object]:
    return {
        "schema": "skeleton.aufmass_source_pack.v1",
        "pack_id": pack_id,
        "project_id": "aufmass",
        "sources": [
            {
                "source_id": "source_token",
                "source_type": "dxf",
                "artifact_ref": "artifact_token",
                "artifact_route": "private_local_runner",
                "metadata": {
                    "title": "title-token",
                    "source_revision": "rev-token",
                    "prepared_by": "operator-token",
                },
                "scale_hint": {"basis": "drawing_scale", "detail": "scale-token"},
                "privacy_status": "private_pilot",
                "review_status": "approved_for_private_intake",
            }
        ],
    }


def _write_aufmass_private_registry(workspace: Path) -> tuple[Path, Path, Path]:
    pack_dir = workspace / "packs" / "packA"
    output_root = workspace / "runs" / "runA"
    manifest = pack_dir / runner.AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST
    artifact_map = pack_dir / "artifact_map.json"
    manifest.parent.mkdir(parents=True)
    output_root.mkdir(parents=True)
    manifest.write_text(json.dumps(_aufmass_private_source_pack()), encoding="utf-8")
    artifact_map.write_text(json.dumps({"artifact_token": "sources/source.dxf"}), encoding="utf-8")
    registry = {
        "schema": runner.AUFMASS_PRIVATE_AUTOMATION_REGISTRY_SCHEMA,
        "source_packs": {
            "pack_token": {
                "source_pack_manifest": "packs/packA/source_pack_manifest.json",
                "artifact_map": "packs/packA/artifact_map.json",
                "latest_run_id": "run_token",
                "runs": {"run_token": {"output_root": "runs/runA"}},
            }
        },
    }
    (workspace / runner.AUFMASS_PRIVATE_AUTOMATION_REGISTRY).write_text(
        json.dumps(registry),
        encoding="utf-8",
    )
    return manifest, artifact_map, output_root


def test_aufmass_private_review_tasks_are_allowlisted() -> None:
    assert runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW == "run_aufmass_private_dxf_review"
    assert runner.SUMMARIZE_AUFMASS_PRIVATE_REVIEW == "summarize_aufmass_private_review"
    assert runner.BUILD_AUFMASS_PRIVATE_SHORTLIST == "build_aufmass_private_shortlist"
    assert (
        runner.BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE
        == "build_aufmass_private_area_schedule"
    )
    assert runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert runner.SUMMARIZE_AUFMASS_PRIVATE_REVIEW in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert runner.BUILD_AUFMASS_PRIVATE_SHORTLIST in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert (
        runner.BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE
        in runner.RUNTIME_MAINTENANCE_TASK_IDS
    )


@pytest.mark.parametrize(
    "field_value",
    ("../source_pack_manifest.json", "/tmp/private.dxf", "drawing.dxf", "pack; reboot"),
)
def test_run_aufmass_private_dxf_review_rejects_issue_supplied_paths_and_shell(
    field_value: str,
) -> None:
    body = _maintenance_issue(
        runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        "sudo reboot\npython3 scripts/aufmass_private_pilot_run.py",
        metadata=f"Private Source Pack ID: {field_value}",
    )["body"]

    with mock.patch.object(runner, "run_command") as run:
        report = runner.run_aufmass_private_dxf_review(str(body))

    assert report.startswith("BLOCKED:")
    assert "reason=invalid_private_source_pack_id" in report
    assert field_value not in report
    run.assert_not_called()


@pytest.mark.parametrize(
    "metadata",
    (
        "Private Source Pack ID: pack_token\nSource File: customer-plan.dxf",
        "Private Source Pack ID: pack_token\n/private/customer/source_pack_manifest.json",
    ),
)
def test_aufmass_private_review_rejects_unsupported_issue_metadata(
    metadata: str,
) -> None:
    body = _maintenance_issue(
        runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        metadata=metadata,
    )["body"]

    with mock.patch.object(runner, "run_command") as run:
        report = runner.run_aufmass_private_dxf_review(str(body))

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_private_aufmass_issue_field" in report
    assert "customer-plan.dxf" not in report
    assert "/private/customer" not in report
    run.assert_not_called()


def test_build_aufmass_private_shortlist_rejects_issue_supplied_private_data() -> None:
    body = _maintenance_issue(
        runner.BUILD_AUFMASS_PRIVATE_SHORTLIST,
        metadata=(
            "Private Source Pack ID: pack_token\n"
            "Drawing Name: confidential-plan.dxf\n"
            "Room Label: Secret Room"
        ),
    )["body"]

    report = runner.build_aufmass_private_shortlist(str(body))

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_private_aufmass_issue_field" in report
    assert "confidential-plan.dxf" not in report
    assert "Secret Room" not in report


def test_build_aufmass_private_area_schedule_rejects_issue_supplied_private_data() -> None:
    body = _maintenance_issue(
        runner.BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        metadata=(
            "Private Source Pack ID: pack_token\n"
            "Layer: private-layer\n"
            "Area: 42.5"
        ),
    )["body"]

    report = runner.build_aufmass_private_area_schedule(str(body))

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_private_aufmass_issue_field" in report
    assert "private-layer" not in report
    assert "42.5" not in report


def test_aufmass_private_registry_is_required_inside_private_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    request = runner.AufmassPrivateAutomationRequest("pack_token")

    entry, report = runner._resolve_aufmass_private_registry_entry(
        runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        request,
        workspace,
    )

    assert entry is None
    assert report is not None
    assert "reason=registry_missing" in report
    assert str(workspace) not in report


def test_aufmass_private_registry_paths_are_relative_and_constrained(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    registry = {
        "schema": runner.AUFMASS_PRIVATE_AUTOMATION_REGISTRY_SCHEMA,
        "source_packs": {
            "pack_token": {
                "source_pack_manifest": "/private/source_pack_manifest.json",
                "artifact_map": "maps/artifact_map.json",
                "output_root": "../leak",
            }
        },
    }
    (workspace / runner.AUFMASS_PRIVATE_AUTOMATION_REGISTRY).write_text(
        json.dumps(registry),
        encoding="utf-8",
    )
    request = runner.AufmassPrivateAutomationRequest("pack_token")

    entry, report = runner._resolve_aufmass_private_registry_entry(
        runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        request,
        workspace,
    )

    assert entry is None
    assert report is not None
    assert "reason=source_pack_manifest_unsafe" in report
    assert "/private/source_pack_manifest.json" not in report
    assert "../leak" not in report


def test_run_aufmass_private_dxf_review_dry_run_uses_bounded_module_invocation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    manifest, _artifact_map, _output_root = _write_aufmass_private_registry(workspace)
    body = _maintenance_issue(
        runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        "python3 scripts/aufmass_private_pilot_run.py --local-debug\nrm -rf /",
        metadata="Private Source Pack ID: pack_token",
    )["body"]

    def run_private_command(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        assert cwd == runner.ROOT
        assert command == [
            "python3",
            "-m",
            "scripts.aufmass_private_pilot_run",
            "--source-pack-manifest",
            str(manifest),
            "--branch",
            "dxf-assisted",
        ]
        return 0, json.dumps(
            {
                "schema": "skeleton.aufmass_private_pilot_public_summary.v1",
                "mode": "dry-run",
                "branch": "dxf-assisted",
                "selected_source_count": 1,
                "private_artifacts": ["room_review_table"],
                "source_validation": {"warnings": []},
            }
        )

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ), mock.patch.object(runner, "run_command", side_effect=run_private_command) as run:
        report = runner.run_aufmass_private_dxf_review(str(body))

    assert report.startswith("DONE:")
    assert "maintenance_task_id=run_aufmass_private_dxf_review" in report
    assert "source_pack_id=pack_token" not in report
    assert "mode=dry-run" in report
    assert "branch=dxf-assisted" in report
    assert "selected_source_count=1" in report
    assert "dxf_source_count=1" in report
    assert "artifact_count=1" in report
    assert "run_id=run_token" not in report
    assert str(workspace) not in report
    assert "source.dxf" not in report
    assert "rm -rf" not in report
    assert run.call_count == 1


def test_run_aufmass_private_dxf_review_execute_uses_bounded_command(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    manifest, artifact_map, output_root = _write_aufmass_private_registry(workspace)
    body = _maintenance_issue(
        runner.RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        metadata="\n".join(
            (
                "Private Source Pack ID: pack_token",
                "Pilot Mode: execute",
                "Run ID: run_token",
            )
        ),
    )["body"]

    def run_private_command(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        assert cwd == runner.ROOT
        assert command == [
            "python3",
            "-m",
            "scripts.aufmass_private_pilot_run",
            "--source-pack-manifest",
            str(manifest),
            "--branch",
            "dxf-assisted",
            "--execute",
            "--private-workspace",
            str(workspace),
            "--output-root",
            str(output_root),
            "--artifact-map",
            str(artifact_map),
        ]
        return 0, json.dumps(
            {
                "schema": "skeleton.aufmass_private_pilot_public_summary.v1",
                "mode": "execute",
                "branch": "dxf-assisted",
                "selected_source_count": 1,
                "dxf_source_count": 1,
                "artifact_count": 4,
                "source_validation": {"warnings": []},
            }
        )

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ), mock.patch.object(runner, "run_command", side_effect=run_private_command):
        report = runner.run_aufmass_private_dxf_review(str(body))

    assert report.startswith("DONE:")
    assert "mode=execute" in report
    assert "artifact_count=4" in report
    assert str(workspace) not in report
    assert "artifact_map.json" not in report


def test_summarize_aufmass_private_review_redacts_private_rows_and_quantities(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    _manifest, _artifact_map, output_root = _write_aufmass_private_registry(workspace)
    review_table = {
        "rows": [
            {
                "source_ref": "source_token",
                "room_label": "Conference 101",
                "review_status": "needs_review",
                "area_m2": 42.5,
                "width_m": 7,
            },
            {
                "source_ref": "source_token",
                "room_label": "Office Secret",
                "review_status": "approved",
                "quantity": 3,
            },
        ]
    }
    (output_root / "source_token_room_review_table.json").write_text(
        json.dumps(review_table),
        encoding="utf-8",
    )
    body = _maintenance_issue(
        runner.SUMMARIZE_AUFMASS_PRIVATE_REVIEW,
        "cat /private/customer/source_token_room_review_table.json",
        metadata="Private Source Pack ID: pack_token",
    )["body"]

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ):
        report = runner.summarize_aufmass_private_review(str(body))

    assert report.startswith("DONE:")
    assert "maintenance_task_id=summarize_aufmass_private_review" in report
    assert "source_pack_id=pack_token" not in report
    assert "run_id=run_token" not in report
    assert "review_table_count=1" in report
    assert "row_count=2" in report
    assert "source_token_count=1" in report
    assert "status_count_approved=1" in report
    assert "status_count_needs_review=1" in report
    assert str(workspace) not in report
    assert "Conference 101" not in report
    assert "Office Secret" not in report
    assert "42.5" not in report
    assert "source_token_room_review_table.json" not in report


def test_build_aufmass_private_shortlist_writes_private_artifacts_and_public_counts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    _manifest, _artifact_map, output_root = _write_aufmass_private_registry(workspace)
    review_table = {
        "rows": [
            {
                "source_ref": "source_token",
                "room_label": "Conference 101",
                "label": "Wall finish A",
                "review_status": "needs_review",
                "area_m2": 42.5,
                "quantity": 3,
            },
            {
                "source_ref": "source_token",
                "review_status": "approved",
                "quantity": 99,
            },
        ]
    }
    (output_root / "source_token_room_review_table.json").write_text(
        json.dumps(review_table),
        encoding="utf-8",
    )
    body = _maintenance_issue(
        runner.BUILD_AUFMASS_PRIVATE_SHORTLIST,
        "cat /private/customer/source_token_room_review_table.json",
        metadata="Private Source Pack ID: pack_token\nRun ID: run_token",
    )["body"]

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ):
        report = runner.build_aufmass_private_shortlist(str(body))

    assert report.startswith("DONE:")
    assert "maintenance_task_id=build_aufmass_private_shortlist" in report
    assert "source_pack_id=pack_token" not in report
    assert "run_id=run_token" not in report
    assert "input_table_count=1" in report
    assert "input_row_count=2" in report
    assert "shortlist_row_count=1" in report
    assert "status_count_approved=1" in report
    assert "status_count_needs_review=1" in report
    assert "warning_count=0" in report
    assert str(workspace) not in report
    assert "Conference 101" not in report
    assert "Wall finish A" not in report
    assert "42.5" not in report
    assert "99" not in report
    assert "source_token_room_review_table.json" not in report

    json_path, csv_path = runner._private_shortlist_artifact_paths(output_root)
    shortlist = json.loads(json_path.read_text(encoding="utf-8"))
    assert shortlist["schema"] == "skeleton.aufmass_private_shortlist.v1"
    assert shortlist["input_row_count"] == 2
    assert shortlist["shortlist_row_count"] == 1
    assert shortlist["rows"][0]["filtering_reason"] == "usable_evidence"
    assert shortlist["rows"][0]["row"]["room_label"] == "Conference 101"
    assert shortlist["rows"][0]["row"]["quantity"] == 3
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "Conference 101" in csv_text
    assert "Wall finish A" in csv_text


def test_build_aufmass_private_area_schedule_writes_explicit_room_and_wall_fields(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    _manifest, _artifact_map, output_root = _write_aufmass_private_registry(workspace)
    review_table = {
        "rows": [
            {
                "source_ref": "source_token",
                "room_ref": "room-ref-office",
                "room_label": "Executive Office",
                "review_status": "approved",
                "area_m2": 42.5,
            },
            {
                "source_ref": "source_token",
                "wall_ref": "wall-ref-north",
                "label": "North Wall",
                "review_status": "approved",
                "wall_length_m": 6.0,
                "height_m": 2.8,
                "opening_area_m2": 1.6,
            },
        ]
    }
    (output_root / "source_token_room_review_table.json").write_text(
        json.dumps(review_table),
        encoding="utf-8",
    )
    body = _maintenance_issue(
        runner.BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        "cat /private/customer/source_token_room_review_table.json",
        metadata="Private Source Pack ID: pack_token\nRun ID: run_token",
    )["body"]

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ):
        report = runner.build_aufmass_private_area_schedule(str(body))

    assert report.startswith("DONE:")
    assert "maintenance_task_id=build_aufmass_private_area_schedule" in report
    assert "source_pack_id=pack_token" not in report
    assert "run_id=run_token" not in report
    assert "room_area_row_count=1" in report
    assert "wall_area_row_count=1" in report
    assert "warning_count=0" in report
    assert "diagnostic_count=0" in report
    assert str(workspace) not in report
    assert "Executive Office" not in report
    assert "North Wall" not in report
    assert "42.5" not in report
    assert "6.0" not in report
    assert "2.8" not in report
    assert "1.6" not in report
    assert "room-ref-office" not in report
    assert "wall-ref-north" not in report
    assert "source_token_room_review_table.json" not in report

    json_path, room_csv_path, wall_csv_path = runner._private_area_schedule_artifact_paths(
        output_root
    )
    schedule = json.loads(json_path.read_text(encoding="utf-8"))
    assert schedule["schema"] == "skeleton.aufmass_private_area_schedule.v1"
    assert schedule["room_area_row_count"] == 1
    assert schedule["wall_area_row_count"] == 1
    room = schedule["room_area_schedule"][0]
    assert "area_m2" not in room
    assert room["room_ref"] == "room-ref-office"
    assert room["floor_area_m2"] == 42.5
    assert room["ceiling_area_m2"] == 42.5
    assert room["evidence"]["floor_area_m2"]["source"] == "area_m2"
    assert (
        room["evidence"]["ceiling_area_m2"]["status"]
        == "assumed_equal_to_floor_area"
    )
    assert room["evidence"]["ceiling_area_m2"]["confidence"] == "assumed"
    wall = schedule["wall_area_schedule"][0]
    assert wall["wall_ref"] == "wall-ref-north"
    assert wall["wall_length_m"] == 6.0
    assert wall["height_m"] == 2.8
    assert wall["gross_wall_area_m2"] == 16.8
    assert wall["opening_area_m2"] == 1.6
    assert wall["opening_area_status"] == "known_value"
    assert wall["net_wall_area_m2"] == 15.2
    room_csv_text = room_csv_path.read_text(encoding="utf-8")
    wall_csv_text = wall_csv_path.read_text(encoding="utf-8")
    assert "floor_area_m2" in room_csv_text
    assert "room-ref-office" in room_csv_text
    assert "net_wall_area_m2" in wall_csv_text
    assert "wall-ref-north" in wall_csv_text


def test_build_aufmass_private_area_schedule_missing_evidence_emits_empty_private_tables(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    _manifest, _artifact_map, output_root = _write_aufmass_private_registry(workspace)
    review_table = {
        "rows": [
            {"room_label": "Unmeasured Room", "review_status": "approved"},
            {
                "label": "Incomplete Wall",
                "wall_length_m": 4.0,
                "height_m": 3.0,
                "review_status": "approved",
            },
            {
                "row_type": "candidate_contour",
                "area_m2": 100.0,
                "wall_length_m": 10.0,
                "height_m": 3.0,
                "opening_area_m2": 0.0,
            },
        ]
    }
    (output_root / "source_token_room_review_table.json").write_text(
        json.dumps(review_table),
        encoding="utf-8",
    )
    body = _maintenance_issue(
        runner.BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        metadata="Private Source Pack ID: pack_token",
    )["body"]

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ):
        report = runner.build_aufmass_private_area_schedule(str(body))

    assert report.startswith("DONE:")
    assert "room_area_row_count=0" in report
    assert "wall_area_row_count=1" in report
    assert "diagnostic_count=2" in report
    assert "Unmeasured Room" not in report
    assert "Incomplete Wall" not in report
    assert "100.0" not in report

    json_path, _room_csv_path, _wall_csv_path = runner._private_area_schedule_artifact_paths(
        output_root
    )
    schedule = json.loads(json_path.read_text(encoding="utf-8"))
    assert schedule["room_area_schedule"] == []
    assert schedule["wall_area_schedule"][0]["opening_area_status"] == "assumed_zero"
    assert schedule["diagnostic_count"] == 2
    reasons = {diagnostic["reason"] for diagnostic in schedule["diagnostics"]}
    assert any("missing_floor_area_evidence" in reason for reason in reasons)
    assert reasons == {
        "weak_review_status_not_payable_quantity",
        "missing_floor_area_evidence,missing_wall_length_evidence",
    }


def test_build_aufmass_private_area_schedule_excludes_weak_rows_before_numeric_parsing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    _manifest, _artifact_map, output_root = _write_aufmass_private_registry(workspace)
    weak_statuses = [
        "candidate",
        "contour",
        "fallback",
        "weak",
        "needs_review",
        "area_mismatch",
    ]
    review_table = {
        "rows": [
            {
                "room_ref": f"weak-room-{index}",
                "room_label": f"Weak Room {index}",
                "review_status": status,
                "area_m2": 900 + index,
                "wall_length_m": 10,
                "height_m": 3,
                "opening_area_m2": 0,
            }
            for index, status in enumerate(weak_statuses)
        ]
    }
    (output_root / "source_token_room_review_table.json").write_text(
        json.dumps(review_table),
        encoding="utf-8",
    )
    body = _maintenance_issue(
        runner.BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        metadata="Private Source Pack ID: pack_token",
    )["body"]

    with mock.patch.object(
        runner,
        "_aufmass_private_registered_paths",
        return_value=(workspace / "checkout", workspace, None),
    ):
        report = runner.build_aufmass_private_area_schedule(str(body))

    assert report.startswith("DONE:")
    assert "room_area_row_count=0" in report
    assert "wall_area_row_count=0" in report
    assert "diagnostic_count=6" in report
    assert "Weak Room" not in report
    assert "900" not in report

    json_path, _room_csv_path, _wall_csv_path = runner._private_area_schedule_artifact_paths(
        output_root
    )
    schedule = json.loads(json_path.read_text(encoding="utf-8"))
    assert schedule["room_area_schedule"] == []
    assert schedule["wall_area_schedule"] == []
    assert {
        diagnostic["reason"] for diagnostic in schedule["diagnostics"]
    } == {"weak_review_status_not_payable_quantity"}


def test_private_area_schedule_uses_generic_area_only_for_approved_or_strong_rows() -> None:
    unreviewed, unreviewed_reason = runner._private_room_area_schedule_row(
        0,
        0,
        {
            "room_ref": "room-unreviewed",
            "review_status": "unreviewed",
            "area_m2": 30,
        },
    )
    approved, approved_reason = runner._private_room_area_schedule_row(
        0,
        1,
        {
            "room_ref": "room-approved",
            "review_status": "approved",
            "area_m2": 30,
        },
    )
    strong, strong_reason = runner._private_room_area_schedule_row(
        0,
        2,
        {
            "room_ref": "room-strong",
            "review_status": "strong",
            "area_m2": "31.5",
        },
    )

    assert unreviewed is None
    assert unreviewed_reason == "missing_floor_area_evidence"
    assert approved is not None
    assert approved_reason is None
    assert approved["room_ref"] == "room-approved"
    assert approved["floor_area_m2"] == 30.0
    assert approved["evidence"]["floor_area_m2"]["status"] == (
        "explicit_generic_area_used_as_floor_area"
    )
    assert strong is not None
    assert strong_reason is None
    assert strong["floor_area_m2"] == 31.5


def test_private_area_schedule_does_not_emit_same_row_as_room_and_wall_quantity(
    tmp_path: Path,
) -> None:
    table_path = tmp_path / "source_token_room_review_table.json"
    table_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "room_ref": "room-1",
                        "wall_ref": "wall-1",
                        "review_status": "approved",
                        "floor_area_m2": 12,
                        "wall_length_m": 4,
                        "height_m": 3,
                        "opening_area_m2": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows, walls, diagnostics, warning_count = runner._build_private_area_schedules(
        [table_path]
    )

    assert rows == []
    assert walls == []
    assert warning_count == 0
    assert diagnostics == [
        {"table_index": 0, "row_index": 0, "reason": "ambiguous_room_wall_quantity"}
    ]


def test_private_area_schedule_keeps_room_and_wall_reference_fields_in_private_csv(
    tmp_path: Path,
) -> None:
    entry = runner.AufmassPrivateRegistryEntry(
        source_pack_id="pack_token",
        run_id="run_token",
        manifest_path=tmp_path / "manifest.json",
        artifact_map_path=tmp_path / "artifact_map.json",
        output_root=tmp_path,
    )
    reason = runner._write_private_area_schedule_artifacts(
        entry,
        [
            {
                "room_ref": "room-1",
                "table_index": 0,
                "row_index": 0,
                "floor_area_m2": 12,
                "ceiling_area_m2": 12,
                "evidence": {},
            }
        ],
        [
            {
                "wall_ref": "wall-1",
                "table_index": 0,
                "row_index": 1,
                "wall_length_m": 5,
                "height_m": 2,
                "gross_wall_area_m2": 10,
                "opening_area_m2": 0,
                "opening_area_status": "known_zero",
                "net_wall_area_m2": 10,
            }
        ],
        [],
        0,
    )

    assert reason is None
    _json_path, room_csv_path, wall_csv_path = runner._private_area_schedule_artifact_paths(
        tmp_path
    )
    room_rows = list(csv.DictReader(room_csv_path.read_text(encoding="utf-8").splitlines()))
    wall_rows = list(csv.DictReader(wall_csv_path.read_text(encoding="utf-8").splitlines()))
    assert room_rows[0]["room_ref"] == "room-1"
    assert wall_rows[0]["wall_ref"] == "wall-1"
    assert wall_rows[0]["opening_area_status"] == "known_zero"


def test_private_area_schedule_distinguishes_known_zero_from_assumed_zero_openings() -> None:
    known_zero, known_reason = runner._private_wall_area_schedule_row(
        0,
        0,
        {
            "wall_ref": "wall-known-zero",
            "review_status": "approved",
            "wall_length_m": 4,
            "height_m": 3,
            "opening_area_m2": 0,
        },
    )
    assumed_zero, assumed_reason = runner._private_wall_area_schedule_row(
        0,
        1,
        {
            "wall_ref": "wall-assumed-zero",
            "review_status": "approved",
            "wall_length_m": 4,
            "height_m": 3,
        },
    )

    assert known_zero is not None
    assert known_reason is None
    assert known_zero["opening_area_m2"] == 0.0
    assert known_zero["opening_area_status"] == "known_zero"
    assert known_zero["evidence"]["opening_area_m2"]["confidence"] == "sufficient"
    assert assumed_zero is not None
    assert assumed_reason is None
    assert assumed_zero["opening_area_m2"] == 0.0
    assert assumed_zero["opening_area_status"] == "assumed_zero"
    assert assumed_zero["evidence"]["opening_area_m2"]["confidence"] == "assumed"


def _pr_validation_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "baseRefName": "main",
        "baseRefOid": "b" * 40,
        "headRefName": "runner-test-branch",
        "headRefOid": HEAD_SHA,
    }
    state.update(updates)
    return state


def _preflight_pr_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "runner-test-branch",
        "headRefOid": HEAD_SHA,
        "headRepository": {
            "name": "Skeleton",
            "nameWithOwner": runner.REPO,
            "owner": {"login": "alanua"},
        },
        "headRepositoryOwner": {"login": "alanua"},
        "files": [{"path": "new_runner_file.py"}],
    }
    state.update(updates)
    return state


def _preflight_compare_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
    }
    state.update(updates)
    return state


def _safe_checkout_path(name: str) -> Path:
    return runner.RUNNER_PROJECT_CHECKOUT_BASE / "worktrees" / name


def _assert_checkout_path_not_public(report: str, checkout_path: Path) -> None:
    assert "checkout_path=" not in report
    assert str(checkout_path) not in report


def test_check_project_checkout_missing_target_project_blocks() -> None:
    report = runner.check_project_checkout(
        "Mode: RUNTIME_MAINTENANCE_TASK\n"
        f"Maintenance Task ID: {runner.CHECK_PROJECT_CHECKOUT}"
    )

    assert report.startswith("BLOCKED:")
    assert "reason=missing_target_project" in report


def test_check_project_checkout_unknown_target_project_blocks() -> None:
    report = runner.check_project_checkout(_checkout_issue_body("unknown"))

    assert report.startswith("BLOCKED:")
    assert "reason=target_project_unknown" in report


def test_check_project_checkout_unsafe_path_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/tmp/checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_unsafe" in report


def test_check_project_checkout_path_traversal_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/home/agent/agent-dev/../checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_traversal" in report


def test_check_project_checkout_missing_checkout_path_blocks() -> None:
    checkout_path = _safe_checkout_path("missing-checkout-for-maintenance-test")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_missing" in report
    _assert_checkout_path_not_public(report, checkout_path)


def test_check_project_checkout_missing_git_blocks_under_runner_base() -> None:
    checkout_path = _safe_checkout_path("checkout-without-git")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: True, checkout_path / ".git": False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_git_missing" in report


def test_check_project_checkout_wrong_remote_blocks() -> None:
    checkout_path = _safe_checkout_path("checkout-wrong-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", return_value=(0, "https://github.com/alanua/Wrong.git\n")
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report


def test_check_project_checkout_matching_remote_reports_done() -> None:
    checkout_path = _safe_checkout_path("checkout-matching-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner,
        "run_command",
        return_value=(0, "git@github.com:alanua/CheckoutTest.git\n"),
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("DONE:")
    assert "success_criteria=met" in report
    assert "target_project_route=registered_checkout" in report
    _assert_checkout_path_not_public(report, checkout_path)
    run.assert_called_once_with(
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    )


def test_check_project_checkout_task_never_runs_mutating_git_or_gh_pr() -> None:
    issue = _maintenance_issue(
        runner.CHECK_PROJECT_CHECKOUT,
        "git clone bad\n"
        "git pull\n"
        "git fetch\n"
        "git push\n"
        "gh pr create\n"
        "sudo chmod 777 /tmp/nope",
        metadata="Target Project: checkout_test",
    )
    checkout_path = _safe_checkout_path("checkout-task-never-runs")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner,
        "run_command",
        return_value=(0, "https://github.com/alanua/CheckoutTest.git\n"),
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    ]


def test_check_skeleton_freshness_is_allowlisted() -> None:
    assert runner.CHECK_SKELETON_FRESHNESS == "check_skeleton_freshness"
    assert runner.CHECK_SKELETON_FRESHNESS in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert runner.RUNTIME_SYNC_MAIN == "runtime_sync_main"
    assert runner.RUNTIME_SYNC_MAIN in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_check_skeleton_freshness_reports_done_with_bounded_status_queries() -> None:
    checkout_path = _safe_checkout_path("skeleton-fresh")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    github_main_sha = "b" * 40
    checkout_head_sha = github_main_sha

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "fetch",
            "--prune",
            "origin",
            "main",
        ]:
            return 0, "raw fetch output must not appear"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{checkout_head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{github_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{github_main_sha}\trefs/heads/main\n"
        if command == ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"]:
            return 0, "123\tFix runner\n124\tRetest\n"
        if command == ["gh", "issue", "list", "--repo", runner.REPO, "--state", "open"]:
            return 0, "533\tFreshness task\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.dispatch_runtime_maintenance_task(
            runner.CHECK_SKELETON_FRESHNESS,
            str(runner.ROOT),
            _skeleton_freshness_issue_body(
                "git push\nsudo env\ngh pr merge 123\ncodex exec unsafe"
            ),
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=check_skeleton_freshness" in report
    assert "target_project_route=registered_checkout" in report
    _assert_checkout_path_not_public(report, checkout_path)
    assert f"checkout_head_sha={checkout_head_sha}" in report
    assert f"github_main_sha={github_main_sha}" in report
    assert "github_main_source_of_truth=true" in report
    assert "checkout_sync_state=equal" in report
    assert "open_pull_requests_count=2" in report
    assert "open_issues_count=1" in report
    assert "NOTEBOOKLM_SOURCEPACK.md" in report
    assert "old_chats_and_old_branches_are_not_canon" in report
    assert "raw fetch output" not in report
    assert "Fix runner" not in report
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
        ["git", "-C", str(checkout_path), "status", "--porcelain"],
        ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"],
        ["git", "-C", str(checkout_path), "rev-parse", "HEAD"],
        ["git", "-C", str(checkout_path), "rev-parse", "origin/main"],
        ["git", "-C", str(checkout_path), "ls-remote", "origin", "refs/heads/main"],
        ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"],
        ["gh", "issue", "list", "--repo", runner.REPO, "--state", "open"],
    ]


def test_check_skeleton_freshness_unsafe_path_blocks_before_commands() -> None:
    project_tree = _project_tree_for_skeleton_checkout(Path("/tmp/Skeleton"))
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_unsafe" in report
    run.assert_not_called()


def test_check_skeleton_freshness_missing_git_blocks() -> None:
    checkout_path = _safe_checkout_path("skeleton-missing-git")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command"
    ) as run:
        path_exists.side_effect = lambda path: path == checkout_path
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_git_missing" in report
    _assert_checkout_path_not_public(report, checkout_path)
    run.assert_not_called()


def test_check_skeleton_freshness_origin_mismatch_blocks_without_raw_output() -> None:
    checkout_path = _safe_checkout_path("skeleton-wrong-origin")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", return_value=(0, "https://github.com/alanua/Wrong.git\n")
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report
    assert "Wrong.git" not in report


def test_check_skeleton_freshness_github_query_failure_blocks_safely() -> None:
    checkout_path = _safe_checkout_path("skeleton-gh-query-fails")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command[:4] == ["git", "-C", str(checkout_path), "fetch"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{HEAD_SHA}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{HEAD_SHA}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{HEAD_SHA}\trefs/heads/main\n"
        if command == ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"]:
            return 1, "token must not leak"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "step=query_open_pull_requests status=failed exit_code=1" in report
    assert "token must not leak" not in report


def test_check_skeleton_freshness_unclassified_sync_state_blocks() -> None:
    checkout_path = _safe_checkout_path("skeleton-unclassified")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    github_main_sha = "b" * 40

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command[:4] == ["git", "-C", str(checkout_path), "fetch"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{HEAD_SHA}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{github_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{github_main_sha}\trefs/heads/main\n"
        if command[:5] == ["git", "-C", str(checkout_path), "merge-base", "--is-ancestor"]:
            return 128, "fatal output must not leak"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "step=classify_checkout_behind status=failed exit_code=128" in report
    assert "fatal output" not in report


def test_check_skeleton_freshness_dirty_checkout_blocks_without_fetch() -> None:
    checkout_path = _safe_checkout_path("skeleton-dirty")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, " M scripts/runner_poll_github_tasks.py\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_dirty" in report
    assert "scripts/runner_poll_github_tasks.py" not in report
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
        ["git", "-C", str(checkout_path), "status", "--porcelain"],
    ]


def test_check_skeleton_freshness_behind_blocks_before_github_queries() -> None:
    checkout_path = _safe_checkout_path("skeleton-behind")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    checkout_head_sha = "a" * 40
    github_main_sha = "b" * 40

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command[:4] == ["git", "-C", str(checkout_path), "fetch"]:
            return 0, "fetch output must not appear"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{checkout_head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{github_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{github_main_sha}\trefs/heads/main\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            checkout_head_sha,
            github_main_sha,
        ]:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "checkout_sync_state=behind" in report
    assert "reason=checkout_behind" in report
    assert "fetch output" not in report
    commands = [call.args[0] for call in run.call_args_list]
    assert ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"] not in commands


def test_runtime_sync_main_fast_forwards_registered_skeleton_checkout() -> None:
    checkout_path = _safe_checkout_path("skeleton-sync-main")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    old_head_sha = "a" * 40
    origin_main_sha = "b" * 40

    def run_sync_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "main\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"]:
            return 0, "raw fetch output must not appear"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            if run_sync_command.final_head:
                return 0, f"{origin_main_sha}\n"
            return 0, f"{old_head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{origin_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            old_head_sha,
            origin_main_sha,
        ]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "merge", "--ff-only", "origin/main"]:
            run_sync_command.final_head = True
            return 0, "raw merge output must not appear"
        return 2, "unexpected command"

    run_sync_command.final_head = False

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_sync_command
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.dispatch_runtime_maintenance_task(
            runner.RUNTIME_SYNC_MAIN,
            str(runner.ROOT),
            _runtime_sync_main_issue_body(
                expected_head_sha=origin_main_sha,
                task_body="git reset --hard\nsudo env\ngh pr merge 123",
            ),
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=runtime_sync_main" in report
    assert "target_project_route=registered_checkout" in report
    _assert_checkout_path_not_public(report, checkout_path)
    assert f"expected_head_sha={origin_main_sha}" in report
    assert f"checkout_head_sha={origin_main_sha}" in report
    assert f"github_main_sha={origin_main_sha}" in report
    assert "checkout_sync_state=equal" in report
    assert "raw fetch output" not in report
    assert "raw merge output" not in report
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
        ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"],
        ["git", "-C", str(checkout_path), "status", "--porcelain"],
        ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"],
        ["git", "-C", str(checkout_path), "rev-parse", "HEAD"],
        ["git", "-C", str(checkout_path), "rev-parse", "origin/main"],
        [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            old_head_sha,
            origin_main_sha,
        ],
        ["git", "-C", str(checkout_path), "merge", "--ff-only", "origin/main"],
        ["git", "-C", str(checkout_path), "rev-parse", "HEAD"],
    ]


def test_runtime_sync_main_blocks_detached_or_non_main_branch() -> None:
    checkout_path = _safe_checkout_path("skeleton-sync-wrong-branch")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)

    def run_sync_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "runner/issue-1226\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_sync_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.runtime_sync_main(_runtime_sync_main_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=branch_not_main" in report
    assert "current_branch=runner/issue-1226" in report
    _assert_checkout_path_not_public(report, checkout_path)


def test_runtime_sync_main_blocks_expected_head_mismatch_and_omits_output() -> None:
    checkout_path = _safe_checkout_path("skeleton-sync-expected-mismatch")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    head_sha = "a" * 40
    origin_main_sha = "b" * 40
    expected_head_sha = "c" * 40

    def run_sync_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "main\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"]:
            return 0, "fetch token must not leak"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{origin_main_sha}\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_sync_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.runtime_sync_main(
            _runtime_sync_main_issue_body(expected_head_sha=expected_head_sha)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=expected_head_sha_mismatch" in report
    assert f"github_main_sha={origin_main_sha}" in report
    assert "fetch token" not in report


def test_runtime_sync_main_blocks_diverged_and_fast_forward_failure() -> None:
    checkout_path = _safe_checkout_path("skeleton-sync-diverged")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    head_sha = "a" * 40
    origin_main_sha = "b" * 40

    def run_diverged_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "main\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{origin_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            head_sha,
            origin_main_sha,
        ]:
            return 1, ""
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            origin_main_sha,
            head_sha,
        ]:
            return 1, ""
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_diverged_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.runtime_sync_main(_runtime_sync_main_issue_body())

    assert report.startswith("BLOCKED:")
    assert "checkout_sync_state=diverged" in report
    assert "reason=checkout_diverged" in report


def test_runtime_sync_main_blocks_fetch_and_fast_forward_failures() -> None:
    checkout_path = _safe_checkout_path("skeleton-sync-ff-failure")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    head_sha = "a" * 40
    origin_main_sha = "b" * 40

    def run_ff_failure_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "main\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{origin_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            head_sha,
            origin_main_sha,
        ]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "merge", "--ff-only", "origin/main"]:
            return 128, "merge output must not leak"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_ff_failure_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.runtime_sync_main(_runtime_sync_main_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=fast_forward_main status=failed exit_code=128" in report
    assert "merge output" not in report


def _prepare_recover_skeleton_checkout_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    checkout_path = tmp_path / "checkout"
    assert runner.run_command(["git", "init", "--bare", str(origin)])[0] == 0
    assert runner.run_command(["git", "init", str(seed)])[0] == 0
    assert (
        runner.run_command(["git", "config", "user.email", "runner@example.test"], cwd=seed)[0]
        == 0
    )
    assert runner.run_command(["git", "config", "user.name", "Runner"], cwd=seed)[
        0
    ] == 0
    (seed / "tracked.txt").write_text("base\n", encoding="utf-8")
    assert runner.run_command(["git", "add", "tracked.txt"], cwd=seed)[0] == 0
    assert runner.run_command(["git", "commit", "-m", "base"], cwd=seed)[0] == 0
    assert runner.run_command(["git", "branch", "-M", "main"], cwd=seed)[0] == 0
    assert runner.run_command(
        ["git", "remote", "add", "origin", str(origin)], cwd=seed
    )[0] == 0
    assert runner.run_command(["git", "push", "-u", "origin", "main"], cwd=seed)[
        0
    ] == 0
    assert runner.run_command(["git", "clone", str(origin), str(checkout_path)])[0] == 0
    assert runner.run_command(["git", "checkout", "main"], cwd=checkout_path)[0] == 0
    assert runner.run_command(
        ["git", "config", "user.email", "runner@example.test"], cwd=checkout_path
    )[0] == 0
    assert runner.run_command(
        ["git", "config", "user.name", "Runner"], cwd=checkout_path
    )[0] == 0
    return origin, seed, checkout_path


def _recover_from_bundle(checkout_path: Path, recovery_root: Path) -> None:
    bundles = sorted(recovery_root.glob("skeleton-checkout-*.bundle"))
    assert len(bundles) == 1
    stash_sha = bundles[0].stem.removeprefix("skeleton-checkout-")
    recovery_ref = f"refs/recovery/{stash_sha}"
    fetch_refspec = f"refs/skeleton-runner/pending-recovery/{stash_sha}:{recovery_ref}"
    assert runner.run_command(
        ["git", "fetch", str(bundles[0]), fetch_refspec], cwd=checkout_path
    )[0] == 0
    assert runner.run_command(["git", "stash", "apply", recovery_ref], cwd=checkout_path)[
        0
    ] == 0


def test_recover_skeleton_checkout_is_allowlisted() -> None:
    assert runner.RECOVER_SKELETON_CHECKOUT == "recover_skeleton_checkout"
    assert runner.RECOVER_SKELETON_CHECKOUT in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_recover_skeleton_checkout_bundle_recovers_tracked_and_untracked_after_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _origin, _seed, checkout_path = _prepare_recover_skeleton_checkout_repo(tmp_path)
    recovery_root = tmp_path / "recovery"
    monkeypatch.setenv("SKELETON_RUNNER_CHECKOUT_RECOVERY_ROOT", str(recovery_root))
    tracked = checkout_path / "tracked.txt"
    untracked = checkout_path / "new.txt"
    tracked.write_text("base\nmodified\n", encoding="utf-8")
    untracked.write_text("untracked\n", encoding="utf-8")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(
        runner, "_remote_url_matches_project_repo", return_value=True
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.RECOVER_SKELETON_CHECKOUT,
            str(runner.ROOT),
            _recover_skeleton_checkout_issue_body(task_body="git push\nsudo env"),
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=recover_skeleton_checkout" in report
    assert "recovery_stash_status=created" in report
    assert "recovery_ref_status=created" in report
    assert "recovery_artifact_status=verified" in report
    assert "reset_status=hard_origin_main" in report
    assert "final_clean_state=true" in report
    assert str(recovery_root) not in report
    assert "modified" not in report
    assert tracked.read_text(encoding="utf-8") == "base\n"
    assert not untracked.exists()

    _recover_from_bundle(checkout_path, recovery_root)
    assert tracked.read_text(encoding="utf-8") == "base\nmodified\n"
    assert untracked.read_text(encoding="utf-8") == "untracked\n"


def test_recover_skeleton_checkout_preverification_failure_restores_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _origin, _seed, checkout_path = _prepare_recover_skeleton_checkout_repo(tmp_path)
    recovery_root = tmp_path / "recovery"
    monkeypatch.setenv("SKELETON_RUNNER_CHECKOUT_RECOVERY_ROOT", str(recovery_root))
    tracked = checkout_path / "tracked.txt"
    untracked = checkout_path / "new.txt"
    tracked.write_text("base\nmodified\n", encoding="utf-8")
    untracked.write_text("untracked\n", encoding="utf-8")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    original_run_command = runner.run_command

    def fail_bundle_verify(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:5] == ["git", "-C", str(checkout_path), "bundle", "verify"]:
            return 1, "bundle verify output must not leak"
        return original_run_command(command, cwd=cwd)

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(
        runner, "_remote_url_matches_project_repo", return_value=True
    ), mock.patch.object(
        runner, "run_command", side_effect=fail_bundle_verify
    ):
        report = runner.recover_skeleton_checkout(_recover_skeleton_checkout_issue_body())

    assert report.startswith("NEEDS_OPERATOR:")
    assert "reason=recovery_artifact_verify_failed" in report
    assert "recovery_restore_status=restored" in report
    assert "bundle verify output" not in report
    assert tracked.read_text(encoding="utf-8") == "base\nmodified\n"
    assert untracked.read_text(encoding="utf-8") == "untracked\n"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "_project_checkout_path_is_under_runner_base", return_value=True
    ), mock.patch.object(
        runner, "_remote_url_matches_project_repo", return_value=True
    ):
        retry_report = runner.recover_skeleton_checkout(
            _recover_skeleton_checkout_issue_body()
        )

    assert retry_report.startswith("DONE:")
    assert "recovery_artifact_status=verified" in retry_report
    assert tracked.read_text(encoding="utf-8") == "base\n"
    assert not untracked.exists()
    _recover_from_bundle(checkout_path, recovery_root)
    assert tracked.read_text(encoding="utf-8") == "base\nmodified\n"
    assert untracked.read_text(encoding="utf-8") == "untracked\n"


def test_recover_skeleton_checkout_blocks_when_final_status_dirty_after_clean() -> None:
    checkout_path = _safe_checkout_path("skeleton-recover-final-dirty")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    origin_main_sha = "b" * 40

    def run_recover_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"]:
            return 0, "main\n"
        if command == ["git", "-C", str(checkout_path), "status", "--porcelain"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{origin_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "reset",
            "--hard",
            "origin/main",
        ]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "clean", "-fd"]:
            return 0, ""
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ]:
            return 0, " M scripts/runner_poll_github_tasks.py\n?? scratch.txt\n"
        return 2, "unexpected command output must not appear"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_recover_command
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.recover_skeleton_checkout(_recover_skeleton_checkout_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "step=clean_untracked status=done" in report
    assert "step=read_final_worktree_status status=done" in report
    assert "final_clean_state=false" in report
    assert "reason=final_checkout_dirty" in report
    assert "scripts/runner_poll_github_tasks.py" not in report
    assert "scratch.txt" not in report
    assert [
        "git",
        "-C",
        str(checkout_path),
        "status",
        "--porcelain",
        "--untracked-files=all",
    ] in commands
    assert ["git", "-C", str(checkout_path), "rev-parse", "HEAD"] not in commands


def test_recover_skeleton_checkout_rejects_unsafe_recovery_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symlink_root = tmp_path / "recovery-link"
    symlink_root.symlink_to(tmp_path)
    monkeypatch.setenv("SKELETON_RUNNER_CHECKOUT_RECOVERY_ROOT", str(symlink_root))
    with pytest.raises(ValueError, match="unsafe"):
        runner._ensure_private_skeleton_checkout_recovery_root()

    loose_root = tmp_path / "loose-recovery"
    loose_root.mkdir(mode=0o700)
    loose_root.chmod(0o755)
    monkeypatch.setenv("SKELETON_RUNNER_CHECKOUT_RECOVERY_ROOT", str(loose_root))
    with pytest.raises(ValueError, match="permissions"):
        runner._ensure_private_skeleton_checkout_recovery_root()

    owned_root = tmp_path / "owned-recovery"
    owned_root.mkdir(mode=0o700)
    monkeypatch.setenv("SKELETON_RUNNER_CHECKOUT_RECOVERY_ROOT", str(owned_root))
    with mock.patch.object(runner.os, "getuid", return_value=os.getuid() + 1):
        with pytest.raises(ValueError, match="owned"):
            runner._ensure_private_skeleton_checkout_recovery_root()


def test_ensure_project_checkout_missing_target_project_blocks() -> None:
    report = runner.ensure_project_checkout(
        "Mode: RUNTIME_MAINTENANCE_TASK\n"
        f"Maintenance Task ID: {runner.ENSURE_PROJECT_CHECKOUT}"
    )

    assert report.startswith("BLOCKED:")
    assert "reason=missing_target_project" in report


def test_ensure_project_checkout_unknown_target_project_blocks() -> None:
    report = runner.ensure_project_checkout(_ensure_checkout_issue_body("unknown"))

    assert report.startswith("BLOCKED:")
    assert "reason=target_project_unknown" in report


def test_ensure_project_checkout_unsafe_path_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/tmp/checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_unsafe" in report


def test_ensure_project_checkout_path_traversal_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/home/agent/agent-dev/../checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_traversal" in report


def test_ensure_project_checkout_existing_valid_reports_done_without_preparation() -> None:
    checkout_path = _safe_checkout_path("checkout-existing-valid")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner,
        "run_command",
        return_value=(0, "https://github.com/alanua/CheckoutTest.git\n"),
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("DONE:")
    assert "step=prepare_checkout_parent" not in report
    assert "step=prepare_checkout" not in report
    run.assert_called_once_with(
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    )


def test_ensure_project_checkout_existing_missing_git_blocks() -> None:
    checkout_path = _safe_checkout_path("checkout-existing-missing-git")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: True, checkout_path / ".git": False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_git_missing" in report


def test_ensure_project_checkout_existing_wrong_remote_blocks() -> None:
    checkout_path = _safe_checkout_path("checkout-existing-wrong-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", return_value=(0, "https://github.com/alanua/Wrong.git\n")
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report


def test_ensure_project_checkout_missing_checkout_prepares_parent_before_clone_and_uses_only_registry_repo_and_path() -> None:
    checkout_path = _safe_checkout_path("prepared-checkout")
    checkout_parent = checkout_path.parent
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False, checkout_path / ".git": False}
    operations: list[str] = []

    def run_registered_command(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == [
            "git",
            "clone",
            "https://github.com/alanua/CheckoutTest.git",
            str(checkout_path),
        ]:
            operations.append("clone")
            exists[checkout_path] = True
            exists[checkout_path / ".git"] = True
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            operations.append("verify_origin")
            return 0, "https://github.com/alanua/CheckoutTest.git\n"
        return 2, "unexpected command"

    def mkdir_registered_parent(
        path: Path, parents: bool = False, exist_ok: bool = False
    ) -> None:
        operations.append("mkdir")
        assert path == checkout_parent
        assert parents is True
        assert exist_ok is True

    body = (
        _ensure_checkout_issue_body()
        + "\n```task\n"
        + "Repo: https://github.com/evil/Repo.git\n"
        + "Path: /tmp/evil\n"
        + "git clone https://github.com/evil/Repo.git /tmp/evil\n"
        + "```"
    )
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ) as path_mkdir, mock.patch.object(
        runner, "run_command", side_effect=run_registered_command
    ) as run:
        path_exists.side_effect = lambda path: exists.get(path, False)
        path_mkdir.side_effect = mkdir_registered_parent
        report = runner.ensure_project_checkout(body)

    assert report.startswith("DONE:")
    assert "step=prepare_checkout_parent status=done" in report
    assert "step=prepare_checkout status=done" in report
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        [
            "git",
            "clone",
            "https://github.com/alanua/CheckoutTest.git",
            str(checkout_path),
        ],
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
    ]
    path_mkdir.assert_called_once_with(checkout_parent, parents=True, exist_ok=True)
    assert operations == ["mkdir", "clone", "verify_origin"]


def test_ensure_project_checkout_parent_preparation_failure_blocks_safely() -> None:
    checkout_path = _safe_checkout_path("parent-preparation-fails")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path,
        "mkdir",
        autospec=True,
        side_effect=OSError("must not leak parent mkdir failure"),
    ) as path_mkdir, mock.patch.object(runner, "run_command") as run:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=prepare_checkout_parent status=failed" in report
    assert "reason=checkout_parent_prepare_failed" in report
    assert "must not leak" not in report
    path_mkdir.assert_called_once_with(
        checkout_path.parent, parents=True, exist_ok=True
    )
    run.assert_not_called()


def test_ensure_project_checkout_preparation_failure_blocks() -> None:
    checkout_path = _safe_checkout_path("clone-fails")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", return_value=(128, "clone failure must not leak")
    ):
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=prepare_checkout_parent status=done" in report
    assert "step=prepare_checkout status=failed exit_code=128" in report
    assert "clone failure" not in report


def test_ensure_project_checkout_remote_mismatch_after_preparation_blocks() -> None:
    checkout_path = _safe_checkout_path("prepared-wrong-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False, checkout_path / ".git": False}

    def run_registered_command(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        del cwd
        if command[:2] == ["git", "clone"]:
            exists[checkout_path] = True
            exists[checkout_path / ".git"] = True
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Wrong.git\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_registered_command
    ):
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=prepare_checkout_parent status=done" in report
    assert "step=prepare_checkout status=done" in report
    assert "step=verify_origin_remote status=failed" in report


def test_ensure_project_checkout_task_never_runs_forbidden_commands_or_codex() -> None:
    issue = _maintenance_issue(
        runner.ENSURE_PROJECT_CHECKOUT,
        "git pull\n"
        "git fetch\n"
        "git checkout main\n"
        "git push\n"
        "gh pr create\n"
        "codex exec unsafe",
        metadata="Target Project: checkout_test",
    )
    checkout_path = _safe_checkout_path("prepared-checkout-task")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False, checkout_path / ".git": False}

    def run_registered_command(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        del cwd
        if command[:2] == ["git", "clone"]:
            exists[checkout_path] = True
            exists[checkout_path / ".git"] = True
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/CheckoutTest.git\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_registered_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        path_exists.side_effect = lambda path: exists.get(path, False)
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    command_words = [" ".join(command) for command in commands]
    assert commands == [
        [
            "git",
            "clone",
            "https://github.com/alanua/CheckoutTest.git",
            str(checkout_path),
        ],
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
    ]
    assert all(" pull" not in command for command in command_words)
    assert all(" fetch" not in command for command in command_words)
    assert all(" checkout " not in command for command in command_words)
    assert all(" push" not in command for command in command_words)
    assert all("gh pr" not in command for command in command_words)
    run_codex.assert_not_called()


def test_validate_pr_branch_missing_pr_number_blocks() -> None:
    report = runner.validate_pr_branch(_validate_pr_issue_body(pr_number=None))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_preflight_pr_refresh_missing_pr_number_blocks() -> None:
    report = runner.preflight_pr_refresh(_preflight_pr_issue_body(pr_number=None))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_preflight_pr_refresh_closed_pr_reports_manual_review() -> None:
    with mock.patch.object(
        runner,
        "_get_preflight_pr_refresh_state",
        return_value=_preflight_pr_state(state="CLOSED"),
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(ahead_by=1, behind_by=0),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=False
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "pr_state=CLOSED" in report
    assert "next_action=manual_review_required" in report


def test_preflight_pr_refresh_head_sha_mismatch_blocks() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(runner, "_get_preflight_compare_state") as compare:
        report = runner.preflight_pr_refresh(
            _preflight_pr_issue_body(expected_head_sha="b" * 40)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=expected_head_sha_mismatch" in report
    compare.assert_not_called()


def test_preflight_pr_refresh_identical_to_main_reports_obsolete() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(
            status="identical", ahead_by=0, behind_by=0
        ),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=True
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "compare_status=identical" in report
    assert "next_action=mark_obsolete" in report


def test_preflight_pr_refresh_behind_only_new_files_recommends_fresh_pr() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(
            status="diverged", ahead_by=1, behind_by=3
        ),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=False
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "compare_status=diverged" in report
    assert "files_on_main_count=0" in report
    assert "next_action=create_fresh_pr" in report


def test_preflight_pr_refresh_overlapping_main_files_requires_manual_review() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(
            status="diverged", ahead_by=1, behind_by=2
        ),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=True
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "files_on_main_count=1" in report
    assert "next_action=manual_review_required" in report


def test_preflight_pr_refresh_open_current_pr_recommends_validate_and_merge() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(status="ahead", ahead_by=1, behind_by=0),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=False
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "next_action=validate_and_merge" in report


def test_preflight_pr_refresh_task_makes_no_mutating_calls() -> None:
    issue = _maintenance_issue(
        runner.PREFLIGHT_PR_REFRESH,
        "git update-ref refs/heads/main HEAD\n"
        "git merge unsafe\n"
        "git push --force\n"
        "gh pr merge 123\n"
        "python3 -c 'open(\"/tmp/nope\", \"w\").write(\"x\")'",
        metadata="\n".join(
            (
                "Pull Request: 123",
                f"Expected Head SHA: {HEAD_SHA}",
            )
        ),
    )

    def run_preflight_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_preflight_pr_state())
        if command[:2] == ["gh", "api"] and "compare" in command[2]:
            return 0, json.dumps(_preflight_compare_state(status="ahead"))
        if command[:4] == ["gh", "api", "--method", "GET"]:
            return 1, "not found"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_preflight_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    command_words = [" ".join(command) for command in commands]
    assert all("update-ref" not in command for command in command_words)
    assert all(" merge" not in command for command in command_words)
    assert all(" push" not in command for command in command_words)
    assert all("checkout" not in command for command in command_words)
    assert all("python3 -c" not in command for command in command_words)
    assert all("open(" not in command for command in command_words)
    run_codex.assert_not_called()


def test_validate_pr_branch_invalid_pr_number_blocks() -> None:
    report = runner.validate_pr_branch(_validate_pr_issue_body(pr_number="abc"))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_validate_pr_branch_unsupported_profile_blocks() -> None:
    report = runner.validate_pr_branch(_validate_pr_issue_body(profile="shell"))

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_validation_profile" in report


def test_validate_pr_branch_expected_head_sha_mismatch_blocks() -> None:
    with mock.patch.object(
        runner, "_get_pr_branch_validation_state", return_value=_pr_validation_state()
    ):
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(expected_head_sha="b" * 40)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=expected_head_sha_mismatch" in report


def test_validate_pr_branch_requires_valid_expected_base_sha() -> None:
    missing_report = runner.validate_pr_branch(
        _validate_pr_issue_body(expected_base_sha=None)
    )
    invalid_report = runner.validate_pr_branch(
        _validate_pr_issue_body(expected_base_sha="not-a-sha")
    )

    assert missing_report.startswith("BLOCKED:")
    assert "reason=missing_expected_base_sha" in missing_report
    assert invalid_report.startswith("BLOCKED:")
    assert "reason=invalid_expected_base_sha" in invalid_report


def test_validate_pr_branch_expected_base_sha_mismatch_blocks_before_commands() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_branch_validation_state",
        return_value=_pr_validation_state(baseRefOid="c" * 40),
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=expected_base_sha_mismatch" in report
    run.assert_not_called()


def test_validate_pr_branch_rejects_closed_or_non_main_prs() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_branch_validation_state",
        return_value=_pr_validation_state(state="CLOSED"),
    ):
        closed_report = runner.validate_pr_branch(_validate_pr_issue_body())
    with mock.patch.object(
        runner,
        "_get_pr_branch_validation_state",
        return_value=_pr_validation_state(baseRefName="develop"),
    ):
        base_report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert closed_report.startswith("BLOCKED:")
    assert "reason=pr_not_open" in closed_report
    assert base_report.startswith("BLOCKED:")
    assert "reason=pr_base_not_main" in base_report


def test_validate_pr_branch_unsafe_validation_path_blocks(tmp_path: Path) -> None:
    with mock.patch.object(
        runner, "_get_pr_branch_validation_state", return_value=_pr_validation_state()
    ), mock.patch.object(
        runner, "_validation_worktree_path", return_value=Path("/tmp/unsafe-pr-validation")
    ), mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(
        runner, "run_command"
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=validation_worktree_path_unsafe" in report
    run.assert_not_called()


def test_validate_pr_branch_uses_exact_pr_head_selection(tmp_path: Path) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    exists = {validation_path: False}

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command == [
            "gh",
            "pr",
            "view",
            "123",
            "--repo",
            runner.REPO,
            "--json",
            "number,state,baseRefName,baseRefOid,headRefName,headRefOid",
        ]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            exists[validation_path] = True
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 0, "99 passed\n"
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert f"head_sha={HEAD_SHA}" in report
    assert [
        "git",
        "fetch",
        "origin",
        "+refs/pull/123/head:refs/remotes/origin/pr-validation/123",
    ] in commands
    assert [
        "git",
        "worktree",
        "add",
        "--detach",
        str(validation_path),
        HEAD_SHA,
    ] in commands
    assert ["python3", "-m", "pytest", "-q"] in commands


def test_validate_pr_branch_non_git_worktree_blocks_before_pytest(tmp_path: Path) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == ["git", "rev-parse", "--is-inside-work-tree"] and cwd == validation_path:
            return 0, "false\n"
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["python3", "-m", "pytest"]:
            return 0, "pytest should not run"
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "validation_real_writable_git_worktree=false" in report
    assert "reason=validation_not_real_git_worktree" in report
    assert ["python3", "-m", "pytest", "-q"] not in commands


def test_validate_pr_branch_unwritable_git_metadata_blocks_before_pytest(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["python3", "-m", "pytest"]:
            return 0, "pytest should not run"
        return 2, "unexpected command"

    def write_probe(directory: Path, name: str) -> bool:
        return name == ".runner-validation-worktree-write-probe"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "_validation_write_probe", side_effect=write_probe
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=validation_git_metadata_unwritable" in report
    assert not any(command[:3] == ["python3", "-m", "pytest"] for command in commands)


def test_validate_pr_branch_changed_file_discovery_failure_blocks(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == ["git", "diff", "--name-only", "b" * 40, "HEAD", "--"]:
            return 2, "fatal: bad diff"
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["python3", "-m", "pytest"]:
            return 0, "pytest should not run"
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=changed_file_discovery_failed" in report
    assert "validation_changed_files_count=0" not in report
    assert not any(command[:3] == ["python3", "-m", "pytest"] for command in commands)


def test_validate_pr_branch_knowledge_intake_profile_runs_allowlisted_tests(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command in (
            ["python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"],
            ["python3", "-m", "pytest", "-q"],
        ) and cwd == validation_path:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(profile="knowledge_intake")
        )

    profile_commands = [
        ["python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"],
        ["python3", "-m", "pytest", "-q"],
    ]
    commands = [
        call.args[0] for call in run.call_args_list if call.args[0] in profile_commands
    ]
    assert report.startswith("DONE:")
    assert commands == profile_commands
    assert "failed_output_start" not in report


def test_validate_pr_branch_runner_exact_base_profile_runs_commands_in_order(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    profile_commands = [
        ["python3", "-m", "pytest", "-q", "tests/test_runner_poll_github_tasks.py"],
        ["python3", "-m", "pytest", "-q"],
        [
            "python3",
            "-m",
            "py_compile",
            "scripts/runner_poll_github_tasks.py",
            "tests/test_runner_poll_github_tasks.py",
        ],
        ["git", "diff", "--check", f"{'b' * 40}...HEAD"],
    ]

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command in profile_commands and cwd == validation_path:
            return 0, "649 passed\n" if command[:3] == ["python3", "-m", "pytest"] else ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(profile="runner_exact_base")
        )

    commands = [
        call.args[0]
        for call in run.call_args_list
        if call.args[0] in profile_commands
    ]
    assert report.startswith("DONE:")
    assert commands == profile_commands
    assert "validation_checkout_head_sha=" + HEAD_SHA in report
    assert "validation_base_ref=main" in report
    assert "validation_base_sha=" + ("b" * 40) in report
    assert "validation_initial_status=clean" in report
    assert "validation_final_status=clean" in report
    assert "validation_pytest_totals=649_passed" in report
    assert f"validation_command_text=git_diff_--check_{'b' * 40}.dot.dot.HEAD" in report


def test_validate_pr_branch_bauclock_time_ledger_profile_uses_target_checkout(
    tmp_path: Path,
) -> None:
    del tmp_path
    checkout_path = _safe_checkout_path("bauclock-validation-main")
    worktree_root = _safe_checkout_path("bauclock-validation-worktrees")
    validation_path = worktree_root / "validate-pr-branch" / "pr-52"
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"]["bauclock"]["checkout_path"] = str(checkout_path)
    project_tree["projects"]["bauclock"]["worktree_root"] = str(worktree_root)

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command == [
            "gh",
            "pr",
            "view",
            "52",
            "--repo",
            "alanua/bauclock",
            "--json",
            "number,state,baseRefName,baseRefOid,headRefName,headRefOid",
        ]:
            return 0, json.dumps(
                _pr_validation_state(number=52, headRefName="runner/issue-668")
            )
        if (
            command
            == [
                "git",
                "fetch",
                "origin",
                "+refs/pull/52/head:refs/remotes/origin/pr-validation/52",
            ]
            and cwd == checkout_path
        ):
            return 0, ""
        if (
            command
            == ["git", "rev-parse", "refs/remotes/origin/pr-validation/52^{commit}"]
            and cwd == checkout_path
        ):
            return 0, f"{HEAD_SHA}\n"
        if (
            command
            == ["git", "worktree", "add", "--detach", str(validation_path), HEAD_SHA]
            and cwd == checkout_path
        ):
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if (
            command
            == ["python3", "-m", "pytest", "-q", "tests/test_time_ledger.py"]
            and cwd == validation_path
        ):
            return 0, "12 passed\n"
        if (
            command
            == [
                "python3",
                "-m",
                "py_compile",
                "api/services/time_ledger.py",
                "api/services/arbzg_policy.py",
                "tests/test_time_ledger.py",
            ]
            and cwd == validation_path
        ):
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "verify_target_repository_checkout", return_value=None
    ), mock.patch.object(
        Path, "exists", autospec=True, return_value=False
    ), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner,
        "_validation_git_worktree_check",
        return_value=(True, ["validation_real_writable_git_worktree=true"], None),
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(
                repository="alanua/bauclock",
                pr_number=52,
                profile="time_ledger_stage1",
            )
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "repository=alanua/bauclock" in report
    assert "pull_request=52" in report
    assert "head_ref=runner/issue-668" in report
    assert f"head_sha={HEAD_SHA}" in report
    assert [
        "python3",
        "-m",
        "pytest",
        "-q",
        "tests/test_time_ledger.py",
    ] in commands
    assert [
        "python3",
        "-m",
        "py_compile",
        "api/services/time_ledger.py",
        "api/services/arbzg_policy.py",
        "tests/test_time_ledger.py",
    ] in commands
    assert all(command[:2] != ["git", "checkout"] for command in commands)
    assert all(command[:2] != ["git", "merge"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)


def test_validate_pr_branch_failed_knowledge_intake_command_reports_output(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    pytest_output = "\n".join(
        (
            "tests/test_knowledge_intake.py::test_rejects_unknown_entry FAILED",
            "E       AssertionError: expected unknown entry to be rejected",
            "SKELETON_TG_CALLBACK_HMAC_SECRET=should-not-leak",
            "1 failed, 4 passed",
        )
    )

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if (
            command
            == ["python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"]
            and cwd == validation_path
        ):
            return 1, pytest_output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(profile="knowledge_intake")
        )

    assert report.startswith("BLOCKED:")
    assert "step=validation_profile_command_1 status=failed exit_code=1" in report
    assert (
        "validation_command_text=python3_-m_pytest_-q_tests/test_knowledge_intake.py"
        in report
    )
    assert "validation_output_tail=" in report
    assert "validation_failing_node=tests/test_knowledge_intake.py::test_rejects_unknown_entry" in report
    assert (
        "validation_error_summary=AssertionError:_expected_unknown_entry_to_be_rejected"
        in report
    )
    assert "validation_pytest_totals=1_failed,4_passed" in report
    assert "validation_failure_phase=call" in report
    assert "validation_final_status=clean" in report
    assert "failed_command=" not in report
    assert "failed_output_start" not in report
    assert "AssertionError: expected unknown entry to be rejected" not in report
    assert "SKELETON_TG_CALLBACK_HMAC_SECRET=should-not-leak" not in report
    assert "should-not-leak" not in report
    assert "failed_output_end" not in report


def test_validate_pr_branch_reports_missing_dependency_module_names(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 1, "ModuleNotFoundError: No module named 'aiogram'\n"
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(
        Path, "exists", autospec=True, return_value=False
    ), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "missing_dependency_module=aiogram" in report


def test_validate_pr_branch_collection_failure_without_node_reports_phase_and_tail(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    output = "\n".join(
        (
            "ERROR collecting tests/test_runner_poll_github_tasks.py",
            "ImportError: cannot import name runner",
            "no tests collected, 1 error in 0.12s",
        )
    )

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 2, output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "validation_failure_phase=collection" in report
    assert "validation_output_tail=" in report
    assert "ERROR_collecting_tests/test_runner_poll_github_tasks.py" in report
    assert "validation_error_summary=ImportError:_cannot_import_name_runner" in report
    assert "validation_failing_node=" not in report


def test_validate_pr_branch_redacts_secret_values_and_external_paths(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    output = "\n".join(
        (
            "PermissionError: [Errno 13] Permission denied: '/home/operator/private.env'",
            "API_TOKEN=secret-value",
            "See https://user:secret@example.invalid/path",
        )
    )

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 1, output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "validation_failure_phase=permissions" in report
    assert "redacted_path" in report
    assert "redacted_url" in report
    assert "/home/operator" not in report
    assert "secret-value" not in report
    assert "user:secret@example.invalid" not in report


def test_validate_pr_branch_failed_command_output_is_truncated(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    long_output = "pytest failure line\n" + ("x" * 5000)

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 1, long_output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=validation_profile_command_1 status=failed exit_code=1" in report
    assert "validation_output_tail=" in report
    assert "Runner_validation_output_truncated_to_4000_characters." in report
    assert "failed_output_start" not in report
    assert "failed_output_end" not in report


def test_validate_pr_branch_long_output_tail_preserves_final_pytest_lines(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    long_output = "\n".join(
        [
            *(f"setup noise {index}" for index in range(140)),
            "tests/test_runner_poll_github_tasks.py::test_actual_failure FAILED",
            "E       AssertionError: final summary survived",
            "1 failed, 649 passed in 12.34s",
        ]
    )

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 1, long_output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "setup_noise_0" not in report
    assert (
        "validation_failing_node=tests/test_runner_poll_github_tasks.py::test_actual_failure"
        in report
    )
    assert "validation_error_summary=AssertionError:_final_summary_survived" in report
    assert "validation_pytest_totals=1_failed,649_passed" in report


def test_validate_pr_branch_runtime_artifact_cleanup_failure_blocks(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["python3", "-m", "pytest"]:
            return 0, "pytest should not run"
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner,
        "cleanup_runtime_artifacts",
        side_effect=OSError("busy runtime artifact"),
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("BLOCKED:")
    assert "reason=runtime_artifact_cleanup_failed" in report
    assert "busy runtime artifact" not in report
    assert ["python3", "-m", "pytest", "-q"] not in commands


def test_validate_pr_branch_issue_body_does_not_execute_arbitrary_commands(
    tmp_path: Path,
) -> None:
    issue = _maintenance_issue(
        runner.VALIDATE_PR_BRANCH,
        "sudo env\n"
        "git push\n"
        "gh pr merge 123\n"
        "codex exec unsafe\n"
        "python3 -c 'print(1)'",
        metadata="\n".join(
            (
                "Pull Request: 123",
                f"Expected Head SHA: {HEAD_SHA}",
                f"Expected Base SHA: {'b' * 40}",
                "Validation Profile: full_pytest",
            )
        ),
    )
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    command_words = [" ".join(call.args[0]) for call in run.call_args_list]
    assert all("sudo" not in command for command in command_words)
    assert all("env" not in command for command in command_words)
    assert all("push" not in command for command in command_words)
    assert all("merge" not in command for command in command_words)
    assert all("codex" not in command for command in command_words)
    assert all("python3 -c" not in command for command in command_words)
    run_codex.assert_not_called()


def test_inspect_pr_mergeability_missing_pr_number_blocks() -> None:
    report = runner.inspect_pr_mergeability(_inspect_pr_issue_body(pr_number=None))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_inspect_pr_mergeability_unsupported_repository_blocks() -> None:
    report = runner.inspect_pr_mergeability(
        _inspect_pr_issue_body(repository="alanua/Other")
    )

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_repository" in report


def test_inspect_pr_mergeability_expected_head_sha_mismatch_blocks() -> None:
    with mock.patch.object(
        runner, "_get_pr_mergeability_state", return_value=_inspect_pr_state()
    ):
        report = runner.inspect_pr_mergeability(
            _inspect_pr_issue_body(expected_head_sha="b" * 40)
        )

    assert report.startswith("BLOCKED:")
    assert "head_sha=" + HEAD_SHA in report
    assert "reason=expected_head_sha_mismatch" in report
    assert "next_action=refresh_inspection_request" in report


def test_inspect_pr_mergeability_closed_pr_reports_obsolete() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(pr={"state": "closed"}),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "pr_state=closed" in report
    assert "reason=pr_not_open" in report
    assert "next_action=obsolete_close_or_reopen_request" in report


def test_inspect_pr_mergeability_draft_pr_reports_draft_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(pr={"draft": True}),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "draft=true" in report
    assert "reason=pr_is_draft" in report
    assert "next_action=mark_pr_ready_for_review" in report


def test_inspect_pr_mergeability_validation_missing_reports_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(
            combined_status={"state": "pending", "statuses": []},
            check_runs=[],
        ),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "validation_state=missing" in report
    assert "reason=validation_missing" in report
    assert "next_action=run_required_validation" in report


def test_inspect_pr_mergeability_open_mergeable_pr_reports_ready_next_action() -> None:
    with mock.patch.object(
        runner, "_get_pr_mergeability_state", return_value=_inspect_pr_state()
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("DONE:")
    assert f"repository={runner.REPO}" in report
    assert "pr_state=open" in report
    assert "draft=false" in report
    assert "base_branch=main" in report
    assert f"head_sha={HEAD_SHA}" in report
    assert "mergeable=true" in report
    assert "changed_files=scripts/runner_poll_github_tasks.py" in report
    assert "ahead_by=1" in report
    assert "behind_by=0" in report
    assert "next_action=mark_ready_or_merge" in report


def test_inspect_pr_mergeability_diverged_pr_reports_refresh_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(
            compare={"status": "diverged", "ahead_by": 2, "behind_by": 1}
        ),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "compare_status=diverged" in report
    assert "reason=branch_behind_or_diverged" in report
    assert "next_action=refresh_pr_branch" in report


def test_inspect_pr_mergeability_non_mergeable_pr_reports_conflict_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(
            pr={"mergeable": False, "mergeable_state": "dirty"}
        ),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "mergeable=false" in report
    assert "reason=pr_has_merge_conflicts" in report
    assert "next_action=resolve_merge_conflicts" in report


def test_inspect_pr_mergeability_uses_github_api_only() -> None:
    payloads = {
        f"https://api.github.com/repos/{runner.REPO}/pulls/123": _inspect_pr_state()[
            "pr"
        ],
        (
            f"https://api.github.com/repos/{runner.REPO}/pulls/123/files"
            "?per_page=100&page=1"
        ): _inspect_pr_state()["files"],
        (
            f"https://api.github.com/repos/{runner.REPO}/compare/"
            f"{'b' * 40}...{HEAD_SHA}"
        ): _inspect_pr_state()["compare"],
        f"https://api.github.com/repos/{runner.REPO}/commits/{HEAD_SHA}/status": _inspect_pr_state()[
            "combined_status"
        ],
        (
            f"https://api.github.com/repos/{runner.REPO}/commits/{HEAD_SHA}/check-runs"
            "?per_page=100&page=1"
        ): {"check_runs": []},
    }

    def urlopen(request: object, timeout: int = 0) -> mock.MagicMock:
        del timeout
        assert isinstance(request, runner.urllib.request.Request)
        return _json_response(payloads[request.full_url])

    with mock.patch.object(runner.urllib.request, "urlopen", side_effect=urlopen), mock.patch.object(
        runner, "run_command"
    ) as run:
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("DONE:")
    run.assert_not_called()


def test_inspect_pr_mergeability_issue_body_does_not_execute_arbitrary_commands() -> None:
    issue = _maintenance_issue(
        runner.INSPECT_PR_MERGEABILITY,
        "sudo env\n"
        "git push\n"
        "gh pr merge 123\n"
        "codex exec unsafe\n"
        "python3 -c 'print(1)'",
        metadata="\n".join(
            (
                f"Repository: {runner.REPO}",
                "Pull Request: 123",
                f"Expected Head SHA: {HEAD_SHA}",
            )
        ),
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "_get_pr_mergeability_state", return_value=_inspect_pr_state()
    ), mock.patch.object(
        runner, "run_command"
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    run.assert_not_called()
    run_codex.assert_not_called()


def test_validate_pr_branch_removes_existing_validation_worktree_only(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        metadata_result = _validation_metadata_command(command, cwd, validation_path)
        if metadata_result is not None:
            return metadata_result
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:4] == ["git", "worktree", "remove", "--force"]:
            return 0, ""
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=True), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert [
        "git",
        "worktree",
        "remove",
        "--force",
        str(validation_path),
    ] in commands
    assert all(command[:2] != ["git", "commit"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)


def test_sync_task_uses_only_allowed_service_names() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    systemctl_commands = [
        call.args[0]
        for call in run.call_args_list
        if call.args[0][:3] == ["sudo", "-n", "systemctl"]
    ]
    used_units = {
        value
        for command in systemctl_commands
        for value in command
        if value.endswith((".service", ".timer"))
    }
    assert used_units == {
        runner.TELEGRAM_CALLBACK_POLLER_SERVICE,
        runner.TELEGRAM_CALLBACK_POLLER_TIMER,
    }


def test_done_requires_callback_timer_active_verification() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "step=verify_callback_timer_active status=done" in report
    assert [
        "sudo",
        "-n",
        "systemctl",
        "is-active",
        "--quiet",
        runner.TELEGRAM_CALLBACK_POLLER_TIMER,
    ] in commands


def test_timer_verification_failure_reports_blocked() -> None:
    def fail_timer_verification(command: list[str], cwd: str | None = None) -> tuple[int, str]:
        if command[:5] == ["sudo", "-n", "systemctl", "is-active", "--quiet"]:
            return 3, ""
        return _successful_maintenance_command(command, cwd)

    with mock.patch.object(runner, "run_command", side_effect=fail_timer_verification):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_timer_active status=failed exit_code=3" in report
    assert "success_criteria=not_met" in report


def test_done_requires_callback_service_success_verification() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    assert "step=verify_callback_service_result status=done" in report


def test_service_verification_failure_reports_blocked() -> None:
    def fail_service_verification(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        if command[:5] == ["sudo", "-n", "systemctl", "show", "--property=Result"]:
            return 0, "failed\n"
        return _successful_maintenance_command(command, cwd)

    with mock.patch.object(runner, "run_command", side_effect=fail_service_verification):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_service_result status=failed" in report


def test_failed_maintenance_verification_is_not_labeled_runner_done() -> None:
    report = (
        "BLOCKED: Runner host maintenance task did not complete.\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "step=verify_callback_timer_active status=failed exit_code=3\n"
        "success_criteria=not_met"
    )
    with mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(runner, "post_issue_comment"), mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(runner, "set_issue_label") as set_label:
        runner.process_runtime_maintenance_issue(
            145, runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, str(runner.ROOT)
        )

    set_label.assert_called_once_with(145, runner.LABEL_RUNNING, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(145, "BLOCKED", report)


def test_maintenance_issue_body_does_not_execute_arbitrary_command() -> None:
    issue = _maintenance_issue(
        runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        "sudo reboot\nsudo apt upgrade\nsystemctl restart unrelated.service",
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [" ".join(call.args[0]) for call in run.call_args_list]
    assert all("reboot" not in command for command in commands)
    assert all("apt" not in command for command in commands)
    assert all("unrelated.service" not in command for command in commands)
    run_codex.assert_not_called()


def test_maintenance_report_does_not_include_command_output_token_values() -> None:
    token = "github-token-must-not-leak"
    with mock.patch.object(runner, "run_command", return_value=(1, token)):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert token not in report

def test_codex_exec_command_default_env_unset_keeps_existing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(runner.CODEX_MODEL_ENV, raising=False)

    command = runner.codex_exec_command("Task body", "/tmp/work", None)

    assert command == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        "/tmp/work",
        runner.build_codex_task_prompt("Task body", "/tmp/work", None),
    ]


def test_codex_exec_command_blank_env_keeps_existing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(runner.CODEX_MODEL_ENV, "   ")

    command = runner.codex_exec_command("Task body", "/tmp/work", None)

    assert "--model" not in command
    assert command == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        "/tmp/work",
        runner.build_codex_task_prompt("Task body", "/tmp/work", None),
    ]


def test_codex_exec_command_env_override_inserts_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(runner.CODEX_MODEL_ENV, "gpt-5-codex")

    command = runner.codex_exec_command("Task body", "/tmp/work", None)

    assert command[:6] == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--model",
        "gpt-5-codex",
    ]
    assert command[6:8] == ["--cd", "/tmp/work"]


def test_codex_exec_command_rejects_invalid_model_before_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(runner.CODEX_MODEL_ENV, "../../bad")

    with mock.patch.object(runner, "run_command") as run_command:
        with pytest.raises(ValueError, match="unsupported characters"):
            runner.run_codex_task("Task body", "/tmp/work", None)

    run_command.assert_not_called()


def test_run_codex_task_sanitizes_home_edge_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "live-home-edge")
    monkeypatch.setenv("SKELETON_RUNNER_MEMORY_DB", "/private/runner.sqlite")
    monkeypatch.setenv("PATH", "/usr/bin")

    completed = runner.subprocess.CompletedProcess(
        args=["codex"],
        returncode=0,
        stdout="done\n",
        stderr="",
    )

    with mock.patch.object(
        runner.subprocess,
        "run",
        return_value=completed,
    ) as subprocess_run:
        code, output = runner.run_codex_task("Task body", str(tmp_path), None)

    assert code == 0
    assert output == "done\n"
    child_environment = subprocess_run.call_args.kwargs["env"]
    assert "SKELETON_HOME_EDGE_01_HOSTNAME" not in child_environment
    assert child_environment["SKELETON_RUNNER_MEMORY_DB"] == "/private/runner.sqlite"
    assert os.environ["SKELETON_HOME_EDGE_01_HOSTNAME"] == "live-home-edge"



def test_maintenance_report_sanitizer_rejects_raw_blocks_paths_and_prose() -> None:
    report = runner._maintenance_report(
        "BLOCKED",
        runner.VALIDATE_PR_BRANCH,
        [
            "failed_output_start",
            "AssertionError: private details",
            "failed_output_end",
            "remove_stderr_start",
            "arbitrary stderr text",
            "remove_stderr_end",
            "reason=arbitrary free text",
            "checkout_path=/home/agent/private",
            "pr_url=https://example.com/not-a-pr",
            "raw_output=plain_text",
            "stdout=plain_text",
            "stderr=plain_text",
            "failed_command=plain_text",
            "step=validation_profile_command_1 status=failed exit_code=1",
            "reason=maintenance_step_raised",
            "pr_url=https://github.com/alanua/Skeleton/pull/1168",
        ],
        "not_met",
    )

    for unsafe in (
        "failed_output_start",
        "AssertionError: private details",
        "failed_output_end",
        "remove_stderr_start",
        "arbitrary stderr text",
        "remove_stderr_end",
        "reason=arbitrary free text",
        "/home/agent/private",
        "https://example.com/not-a-pr",
        "raw_output=plain_text",
        "stdout=plain_text",
        "stderr=plain_text",
        "failed_command=plain_text",
    ):
        assert unsafe not in report
    assert "step=validation_profile_command_1 status=failed exit_code=1" in report
    assert "reason=maintenance_step_raised" in report
    assert "pr_url=https://github.com/alanua/Skeleton/pull/1168" in report


def _cloned_mapping(value: object) -> object:
    return json.loads(json.dumps(value))


def test_hermes_memory_gateway_smoke_is_allowlisted_and_reports_aggregate_only() -> None:
    assert runner.HERMES_MEMORY_GATEWAY_SMOKE in runner.RUNTIME_MAINTENANCE_TASK_IDS

    report = runner.dispatch_runtime_maintenance_task(
        runner.HERMES_MEMORY_GATEWAY_SMOKE, str(runner.ROOT)
    )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=hermes_memory_gateway_smoke" in report
    assert "hermes_memory_operation_count=6" in report
    assert (
        f"hermes_gateway_contract={runner.MEMORY_GATEWAY_CONTRACT_VERSION}" in report
    )
    report_lines = report.splitlines()
    assert report.count("hermes_memory_smoke_status=") == 1
    assert report_lines[-2] == "hermes_memory_smoke_status=done"
    assert "hermes_memory_smoke_status=done" in report
    assert "success_criteria=met" in report
    assert "hermes.memory_task_packet" not in report
    assert "proposal-event" not in report
    assert "canon-" not in report
    assert "canonical_revision" not in report
    assert "primary_fact" not in report


@pytest.mark.parametrize(
    ("case_name", "expected_token"),
    (
        ("wrong_schema", "hermes_result_schema_mismatch"),
        ("wrong_operation", "hermes_result_operation_mismatch"),
        ("wrong_gateway_command", "hermes_gateway_command_mismatch"),
        ("wrong_namespace", "hermes_result_namespace_mismatch"),
        ("wrong_project", "hermes_result_project_mismatch"),
        ("wrong_lookup_payload", "hermes_lookup_payload_semantics_mismatch"),
        ("wrong_isolation_reason", "hermes_cross_namespace_reason_mismatch"),
        ("wrong_duplicate_result", "hermes_duplicate_classification_mismatch"),
        ("changed_after_state", "hermes_canonical_after_state_changed"),
    ),
)
def test_hermes_memory_gateway_smoke_contract_mismatches_block(
    case_name: str, expected_token: str
) -> None:
    original = runner.run_hermes_memory_task_packet
    call_counts: dict[str, int] = {}

    def corrupting_worker(packet: dict[str, object], *, gateway: object) -> dict[str, object]:
        result = _cloned_mapping(original(packet, gateway=gateway))
        assert isinstance(result, dict)
        operation = str(packet.get("operation"))
        task_packet_id = str(packet.get("task_id"))
        call_counts[operation] = call_counts.get(operation, 0) + 1

        if case_name == "wrong_schema" and operation == "memory.lookup_exact":
            result["schema"] = "wrong.schema"
        elif case_name == "wrong_operation" and operation == "memory.get_conflicts":
            result["operation"] = "memory.lookup_exact"
        elif case_name == "wrong_gateway_command" and operation == "memory.get_audit_log":
            gateway_result = result.get("gateway")
            assert isinstance(gateway_result, dict)
            gateway_result["command"] = "aufmass.memory.search_semantic"
        elif case_name == "wrong_namespace" and operation == "memory.get_index_freshness":
            result["namespace"] = "bauclock"
        elif case_name == "wrong_project" and operation == "memory.get_conflicts":
            result["project_id"] = "project-b"
        elif case_name == "wrong_lookup_payload" and operation == "memory.lookup_exact":
            payload = result.get("payload")
            assert isinstance(payload, dict)
            payload["canonical_revision"] = "3"
        elif (
            case_name == "wrong_isolation_reason"
            and task_packet_id == "hermes-memory-gateway-smoke-cross-namespace"
        ):
            result["decision"] = {"allowed": False, "reason": "BLOCKED"}
        elif (
            case_name == "wrong_duplicate_result"
            and operation == "memory.propose_patch"
            and result.get("status") == "DUPLICATE_EXISTING"
        ):
            payload = result.get("payload")
            assert isinstance(payload, dict)
            payload["classification"] = "NEW_PROPOSAL"
        elif (
            case_name == "changed_after_state"
            and operation == "memory.lookup_exact"
            and call_counts[operation] == 2
        ):
            payload = result.get("payload")
            assert isinstance(payload, dict)
            payload["canonical_revision"] = 4
        return result

    with mock.patch.object(
        runner, "run_hermes_memory_task_packet", side_effect=corrupting_worker
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.HERMES_MEMORY_GATEWAY_SMOKE, str(runner.ROOT)
        )

    assert report.startswith("BLOCKED:")
    report_lines = report.splitlines()
    assert report.count("hermes_memory_smoke_status=") == 1
    assert report_lines[-2] == "hermes_memory_smoke_status=blocked"
    assert f"status_token={expected_token}" in report
    assert f"reason={expected_token}" in report
    assert "error_class=none" not in report
    assert "success_criteria=not_met" in report


@pytest.mark.parametrize(
    ("isolation_task_id", "field_name", "field_value", "expected_token"),
    (
        (
            "hermes-memory-gateway-smoke-cross-project",
            "schema",
            "wrong.schema",
            "hermes_isolation_result_schema_mismatch",
        ),
        (
            "hermes-memory-gateway-smoke-cross-namespace",
            "status",
            "DRY_RUN_OK",
            "hermes_isolation_status_mismatch",
        ),
        (
            "hermes-memory-gateway-smoke-cross-project",
            "status",
            "DRY_RUN_OK",
            "hermes_isolation_status_mismatch",
        ),
        (
            "hermes-memory-gateway-smoke-cross-namespace",
            "schema",
            "wrong.schema",
            "hermes_isolation_result_schema_mismatch",
        ),
    ),
)
def test_hermes_memory_gateway_smoke_isolation_schema_and_status_must_fail_closed(
    isolation_task_id: str,
    field_name: str,
    field_value: str,
    expected_token: str,
) -> None:
    original = runner.run_hermes_memory_task_packet

    def corrupting_worker(packet: dict[str, object], *, gateway: object) -> dict[str, object]:
        result = _cloned_mapping(original(packet, gateway=gateway))
        assert isinstance(result, dict)
        if str(packet.get("task_id")) == isolation_task_id:
            result[field_name] = field_value
        return result

    with mock.patch.object(
        runner, "run_hermes_memory_task_packet", side_effect=corrupting_worker
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.HERMES_MEMORY_GATEWAY_SMOKE, str(runner.ROOT)
        )

    assert report.startswith("BLOCKED:")
    report_lines = report.splitlines()
    assert report.count("hermes_memory_smoke_status=") == 1
    assert report_lines[-2] == "hermes_memory_smoke_status=blocked"
    assert f"status_token={expected_token}" in report
    assert f"reason={expected_token}" in report


@pytest.mark.parametrize(
    ("isolation_task_id", "expected_token"),
    (
        (
            "hermes-memory-gateway-smoke-cross-project",
            "hermes_cross_project_reason_mismatch",
        ),
        (
            "hermes-memory-gateway-smoke-cross-namespace",
            "hermes_cross_namespace_reason_mismatch",
        ),
    ),
)
def test_hermes_memory_gateway_smoke_isolation_reason_must_be_exact(
    isolation_task_id: str, expected_token: str
) -> None:
    original = runner.run_hermes_memory_task_packet

    def corrupting_worker(packet: dict[str, object], *, gateway: object) -> dict[str, object]:
        result = _cloned_mapping(original(packet, gateway=gateway))
        assert isinstance(result, dict)
        if str(packet.get("task_id")) == isolation_task_id:
            result["decision"] = {"allowed": False, "reason": "BLOCKED"}
        return result

    with mock.patch.object(
        runner, "run_hermes_memory_task_packet", side_effect=corrupting_worker
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.HERMES_MEMORY_GATEWAY_SMOKE, str(runner.ROOT)
        )

    assert report.startswith("BLOCKED:")
    report_lines = report.splitlines()
    assert report.count("hermes_memory_smoke_status=") == 1
    assert report_lines[-2] == "hermes_memory_smoke_status=blocked"
    assert f"status_token={expected_token}" in report
    assert "error_class=none" not in report



def test_block_issue_uses_actual_bounded_failure_reason_for_retry_signature() -> None:
    body = "Expected Output: draft PR\nAllowed Files:\n- core/example.py"
    condition = runner.retry_condition_for_issue(
        body,
        runner.ROUTE_CODE_GENERATION,
        None,
    )
    decision = runner.evaluate_retry_policy(condition, [])
    comments: list[str] = []

    with mock.patch.object(
        runner,
        "post_issue_comment",
        side_effect=lambda _number, comment: comments.append(comment),
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "record_runner_executor_result", return_value=None
    ):
        runner.block_issue(
            1450,
            "Codex task failed:\nReason: codex_nonzero_exit",
            retry_decision=decision,
        )
        runner.block_issue(
            1450,
            "Runner error:\nReason: RuntimeError",
            retry_decision=decision,
        )

    reports = runner.parse_prior_blocked_reports(comments)
    assert len(reports) == 2
    assert reports[0].condition_signature == reports[1].condition_signature
    assert reports[0].blocker_signature != reports[1].blocker_signature



def test_trusted_runner_comment_authors_include_owner_and_configured_actor() -> None:
    with mock.patch.dict(
        os.environ,
        {runner.RUNNER_GITHUB_ACTOR_ENV: "Skeleton-Runner-Service"},
        clear=False,
    ):
        actors = runner.trusted_runner_comment_authors()

    assert "alanua" in actors
    assert "github-actions[bot]" in actors
    assert "skeleton-runner-service" in actors


def _loop_engine_issue_body(packet: object) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.LOOP_ENGINE_PACKET}",
            "```task",
            json.dumps(packet, sort_keys=True),
            "```",
        )
    )


def _loop_engine_packet(action: str, **updates: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "skeleton.loop_runner_packet.v1",
        "action": action,
        "task_id": "issue-1468",
        "run_id": "run-1468",
        "recorded_at": 1,
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "authority_boundary": {
            "operational_state_write": True,
            "external_side_effects_allowed": False,
            "runtime_mutation_allowed": False,
        },
    }
    if action == "step":
        packet.update({"event": "PREPARED", "expected_version": 0})
    packet.update(updates)
    return packet


def test_loop_engine_packet_missing_db_env_fails_closed() -> None:
    body = _loop_engine_issue_body(_loop_engine_packet("create"))

    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
        runner, "LoopStateStore"
    ) as store:
        report = runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), body
        )

    assert report.startswith("BLOCKED:")
    assert "reason=loop_state_db_missing" in report
    assert "success_criteria=not_met" in report
    store.assert_not_called()


@pytest.mark.parametrize("configured_path", ("relative.sqlite", "../unsafe.sqlite"))
def test_loop_engine_packet_relative_or_unsafe_db_path_fails_closed(
    configured_path: str,
) -> None:
    body = _loop_engine_issue_body(_loop_engine_packet("create"))

    with mock.patch.dict(
        os.environ, {runner.LOOP_STATE_DB_ENV: configured_path}, clear=False
    ), mock.patch.object(runner, "LoopStateStore") as store:
        report = runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), body
        )

    assert report.startswith("BLOCKED:")
    assert "success_criteria=not_met" in report
    assert "reason=loop_state_db_" in report
    store.assert_not_called()


def test_loop_engine_packet_create_and_step_persist_without_model_route(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "loop-state.sqlite"
    create_body = _loop_engine_issue_body(_loop_engine_packet("create"))
    step_body = _loop_engine_issue_body(
        _loop_engine_packet("step", recorded_at=2, now=2, budget_delta=1)
    )

    with mock.patch.dict(
        os.environ, {runner.LOOP_STATE_DB_ENV: str(db_path)}, clear=False
    ), mock.patch.object(runner, "run_codex_task") as codex:
        created = runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), create_body
        )
        stepped = runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), step_body
        )

    assert created.startswith("DONE:")
    assert "status=CREATED" in created
    assert "loop_state=CREATED" in created
    assert "version=0" in created
    assert "external_side_effects_executed=false" in created
    assert stepped.startswith("DONE:")
    assert "status=READY" in stepped
    assert "loop_state=READY" in stepped
    assert "event=PREPARED" in stepped
    assert "version=1" in stepped
    codex.assert_not_called()


def test_loop_engine_packet_stale_version_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "loop-state.sqlite"
    create_body = _loop_engine_issue_body(_loop_engine_packet("create"))
    first_step = _loop_engine_issue_body(
        _loop_engine_packet("step", recorded_at=2)
    )
    stale_step = _loop_engine_issue_body(
        _loop_engine_packet(
            "step",
            event="STARTED",
            expected_version=0,
            recorded_at=3,
        )
    )

    with mock.patch.dict(
        os.environ, {runner.LOOP_STATE_DB_ENV: str(db_path)}, clear=False
    ):
        runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), create_body
        )
        runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), first_step
        )
        report = runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), stale_step
        )

    assert report.startswith("BLOCKED:")
    assert "reason=LOOP_STATE_CONFLICT" in report
    assert "accepted=false" in report
    assert "success_criteria=not_met" in report


def test_loop_engine_packet_invalid_packet_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "loop-state.sqlite"
    body = _loop_engine_issue_body({"schema": "wrong"})

    with mock.patch.dict(
        os.environ, {runner.LOOP_STATE_DB_ENV: str(db_path)}, clear=False
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.LOOP_ENGINE_PACKET, str(runner.ROOT), body
        )

    assert report.startswith("BLOCKED:")
    assert "reason=INVALID_LOOP_TASK_PACKET" in report
    assert "accepted=false" in report
    assert "external_side_effects_executed=false" in report


def test_loop_receipt_status_keys_are_sanitized_and_private_keys_rejected() -> None:
    accepted = runner._sanitize_maintenance_status_line(
        "schema=skeleton.loop_runner_result.v1 status=READY action=step "
        "task_id=issue-1468 run_id=run-1468 version=1 loop_state=READY "
        "event=PREPARED accepted=true decision=CONTINUE "
        f"context_hash={'a' * 64} public_safe=true "
        "external_side_effects_executed=false"
    )

    assert accepted is not None
    assert runner._sanitize_maintenance_status_line("private_path=/tmp/private") is None
    assert runner._sanitize_maintenance_status_line("unexpected_loop_key=value") is None


def test_unrelated_runtime_maintenance_dispatch_is_unchanged() -> None:
    with mock.patch.object(
        runner, "check_skeleton_freshness", return_value="DONE: unchanged"
    ) as check:
        report = runner.dispatch_runtime_maintenance_task(
            runner.CHECK_SKELETON_FRESHNESS, str(runner.ROOT)
        )

    assert report == "DONE: unchanged"
    check.assert_called_once_with()

def test_home_edge_lightweight_diagnostic_does_not_require_usb_modem() -> None:
    artifact = {
        "node": {"node_id": "home-edge-01"},
        "summary": {
            "gateway": {"status": "ready"},
            "route": {"status": "unchanged"},
            "tailscale": {"status": "healthy"},
            "modem": {
                "status": "optional_not_attached",
                "registered_expectation": {
                    "internet_path": "default_gateway",
                    "gateway_modem_internals": "not_observed_by_home_edge",
                },
                "observed": {"state": "observed", "present": False},
            },
        },
    }
    with mock.patch(
        "core.home_edge.diagnostics.run_audited_home_edge_command",
        return_value=artifact,
    ):
        report = runner.home_edge_01_read_only_diagnostic()

    assert runner.maintenance_report_status(report) == "DONE"
    assert "usb_modem_health_requirement=false" in report
    assert "internet_path_expectation=default_gateway" in report
    assert "gateway_modem_internals=not_observed_by_home_edge" in report


def test_home_edge_lan_inventory_reports_aggregate_only() -> None:
    result = {
        "node_id": "home-edge-01",
        "status": "observed",
        "aggregate": {
            "device_count": 7,
            "responsive_count": 5,
            "service_category_counts": {"web": 2, "home_automation": 1},
            "gateway_presence": "present",
            "risk_flags": [],
        },
        "details": {
            "records": [{"address": "192.168.50.10"}],
        },
    }
    with mock.patch(
        "core.home_edge.diagnostics.run_audited_home_edge_command",
        return_value=result,
    ):
        report = runner.home_edge_01_lan_inventory_read_only()

    assert runner.maintenance_report_status(report) == "DONE"
    assert "maintenance_task_id=home_edge_01_lan_inventory_read_only" in report
    assert "device_count=7" in report
    assert "responsive_count=5" in report
    assert "gateway_presence=present" in report
    assert "service_category_counts=home_automation:1,web:2" in report
    assert "192.168.50.10" not in report
    assert "records" not in report


def test_home_edge_lan_inventory_task_is_explicitly_dispatched() -> None:
    with mock.patch.object(
        runner,
        "home_edge_01_lan_inventory_read_only",
        return_value="DONE: test",
    ) as action:
        report = runner.dispatch_runtime_maintenance_task(
            runner.HOME_EDGE_01_LAN_INVENTORY_READ_ONLY,
            str(Path.cwd()),
        )

    assert report == "DONE: test"
    action.assert_called_once_with()



def test_validation_command_environment_strips_only_home_edge_runtime_values() -> None:
    environment = {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SKELETON_HOME_EDGE_01_HOSTNAME": "live-home-edge",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
    }

    filtered = runner._validation_command_environment(environment)

    assert filtered == {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
    }
    assert environment["SKELETON_HOME_EDGE_01_HOSTNAME"] == "live-home-edge"
    assert environment["SKELETON_RUNNER_MEMORY_DB"] == "/private/runner.sqlite"


def test_run_validation_profile_command_sanitizes_and_resets_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "SKELETON_HOME_EDGE_01_HOSTNAME",
        "live-home-edge",
    )
    monkeypatch.setenv(
        "SKELETON_RUNNER_MEMORY_DB",
        "/private/runner.sqlite",
    )
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/agent")

    completed = runner.subprocess.CompletedProcess(
        args=["python3"],
        returncode=0,
        stdout="ok\n",
        stderr="",
    )

    with mock.patch.object(
        runner.subprocess,
        "run",
        return_value=completed,
    ) as subprocess_run:
        code, output = runner._run_validation_profile_command(
            ["python3", "-m", "pytest", "-q"],
            cwd=tmp_path,
        )
        runner.run_command(
            ["python3", "--version"],
            cwd=tmp_path,
        )

    assert code == 0
    assert output == "ok\n"
    assert subprocess_run.call_count == 2

    validation_kwargs = subprocess_run.call_args_list[0].kwargs
    normal_kwargs = subprocess_run.call_args_list[1].kwargs
    validation_environment = validation_kwargs["env"]

    assert validation_environment["PATH"] == "/usr/bin"
    assert validation_environment["HOME"] == "/home/agent"
    assert "SKELETON_HOME_EDGE_01_HOSTNAME" not in validation_environment
    assert validation_environment["SKELETON_RUNNER_MEMORY_DB"] == "/private/runner.sqlite"
    assert "env" not in normal_kwargs

    assert (
        os.environ["SKELETON_HOME_EDGE_01_HOSTNAME"]
        == "live-home-edge"
    )
    assert (
        os.environ["SKELETON_RUNNER_MEMORY_DB"]
        == "/private/runner.sqlite"
    )


def test_run_validation_profile_command_resets_environment_after_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "live-home-edge")
    completed = runner.subprocess.CompletedProcess(
        args=["python3"],
        returncode=0,
        stdout="ok\n",
        stderr="",
    )

    with mock.patch.object(
        runner.subprocess,
        "run",
        side_effect=(RuntimeError("launch failed"), completed),
    ) as subprocess_run:
        with pytest.raises(RuntimeError, match="launch failed"):
            runner._run_validation_profile_command(
                ["python3", "-m", "pytest", "-q"],
                cwd=tmp_path,
            )
        code, output = runner.run_command(["git", "status", "--short"], cwd=tmp_path)

    assert code == 0
    assert output == "ok\n"
    assert "env" in subprocess_run.call_args_list[0].kwargs
    assert "env" not in subprocess_run.call_args_list[1].kwargs


def test_finalize_success_validation_subprocesses_use_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "live-home-edge")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_TAILSCALE_IP", "100.64.0.1")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_CONTROLLER_HOST", "controller")
    monkeypatch.setenv("SKELETON_HOME_EDGE_TEST_FIXTURE", "public-fixture")
    monkeypatch.setenv("SKELETON_RUNNER_MEMORY_DB", "/private/runner.sqlite")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/agent")
    monkeypatch.setenv("LANG", "C.UTF-8")

    issue = {"number": 123, "title": "Sanitize validation"}
    changed = ["scripts/runner_poll_github_tasks.py"]
    captured_calls: list[tuple[list[str], dict[str, object]]] = []

    def run(
        args: list[str],
        **kwargs: object,
    ) -> runner.subprocess.CompletedProcess[str]:
        captured_calls.append((args, kwargs))
        if args == ["git", "diff", "--check"]:
            return runner.subprocess.CompletedProcess(args, 0, "", "")
        if args == ["python3", "-m", "pytest", "-q"]:
            child_environment = kwargs["env"]
            assert isinstance(child_environment, dict)
            assert not any(
                key.startswith("SKELETON_HOME_EDGE_01_")
                for key in child_environment
            )
            assert child_environment["SKELETON_HOME_EDGE_TEST_FIXTURE"] == "public-fixture"
            assert child_environment["SKELETON_RUNNER_MEMORY_DB"] == "/private/runner.sqlite"
            assert child_environment["TELEGRAM_BOT_TOKEN"] == "telegram-token"
            assert child_environment["GITHUB_TOKEN"] == "github-token"
            assert child_environment["GH_TOKEN"] == "gh-token"
            assert child_environment["PATH"] == "/usr/bin"
            assert child_environment["HOME"] == "/home/agent"
            assert child_environment["LANG"] == "C.UTF-8"
            return runner.subprocess.CompletedProcess(args, 0, "99 passed\n", "")
        if args == ["git", "add", *changed]:
            return runner.subprocess.CompletedProcess(args, 0, "", "")
        if args == ["git", "diff", "--cached", "--check"]:
            return runner.subprocess.CompletedProcess(args, 0, "", "")
        if args == ["git", "commit", "-m", "runner: issue #123 task"]:
            return runner.subprocess.CompletedProcess(args, 0, "", "")
        if args == [
            "git",
            "push",
            "--force-with-lease",
            "-u",
            "origin",
            "runner/issue-123",
        ]:
            return runner.subprocess.CompletedProcess(args, 0, "", "")
        if args == ["git", "rev-parse", "HEAD"]:
            return runner.subprocess.CompletedProcess(args, 0, f"{HEAD_SHA}\n", "")
        if args[:3] == ["gh", "pr", "create"]:
            return runner.subprocess.CompletedProcess(args, 0, f"{PR_URL}\n", "")
        raise AssertionError(f"unexpected command: {args!r}")

    with mock.patch.object(
        runner, "changed_files", side_effect=(changed, changed)
    ), mock.patch.object(runner, "cleanup_runtime_artifacts"), mock.patch.object(
        runner.subprocess, "run", side_effect=run
    ):
        report = runner.finalize_success(issue, "/tmp/worktree", "codex output")

    assert "99 passed" in report
    validation_calls = captured_calls[:2]
    publication_calls = captured_calls[2:]
    assert [args for args, _kwargs in validation_calls] == [
        ["git", "diff", "--check"],
        ["python3", "-m", "pytest", "-q"],
    ]
    assert all("env" in kwargs for _args, kwargs in validation_calls)
    assert all("env" not in kwargs for _args, kwargs in publication_calls)
    assert os.environ["SKELETON_HOME_EDGE_01_HOSTNAME"] == "live-home-edge"


def test_finalize_success_resets_validation_override_before_publish_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "live-home-edge")
    changed = ["scripts/runner_poll_github_tasks.py"]

    with mock.patch.object(
        runner, "changed_files", return_value=changed
    ), mock.patch.object(runner, "cleanup_runtime_artifacts"), mock.patch.object(
        runner.subprocess,
        "run",
        side_effect=RuntimeError("pytest launch failed"),
    ):
        with pytest.raises(RuntimeError, match="pytest launch failed"):
            runner.finalize_success(
                {"number": 123, "title": "Sanitize validation"},
                "/tmp/worktree",
                "codex output",
            )

    completed = runner.subprocess.CompletedProcess(
        args=["gh"],
        returncode=0,
        stdout="ok\n",
        stderr="",
    )
    with mock.patch.object(
        runner.subprocess,
        "run",
        return_value=completed,
    ) as subprocess_run:
        code, output = runner.run_command(["gh", "auth", "status"], cwd="/tmp/worktree")

    assert code == 0
    assert output == "ok\n"
    assert "env" not in subprocess_run.call_args.kwargs


def test_local_target_finalization_validation_helper_uses_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "live-home-edge")
    monkeypatch.setenv("SKELETON_RUNNER_MEMORY_DB", "/private/runner.sqlite")
    completed = runner.subprocess.CompletedProcess(
        args=["python3"],
        returncode=0,
        stdout="ok\n",
        stderr="",
    )

    with mock.patch.object(
        runner.subprocess,
        "run",
        return_value=completed,
    ) as subprocess_run:
        code, output = runner._run_finalization_validation_command(
            ["python3", "-m", "pytest", "-q"],
            cwd=tmp_path,
        )

    assert code == 0
    assert output == "ok\n"
    child_environment = subprocess_run.call_args.kwargs["env"]
    assert "SKELETON_HOME_EDGE_01_HOSTNAME" not in child_environment
    assert child_environment["SKELETON_RUNNER_MEMORY_DB"] == "/private/runner.sqlite"

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

import scripts.runner_vnext_poll as poll
from core.runner_vnext_queue import (
    RunnerVNextQueueItem,
    get_registered_ready_issue_items,
    registered_source_for_repository,
    registered_runner_queue_sources,
    runner_vnext_queue_item_priority_key,
)


def _project_tree() -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "default_project": "skeleton",
        "projects": {
            "skeleton": {
                "repo": "alanua/Skeleton",
                "checkout_path": "/home/agent/agent-dev/repos/Skeleton",
                "worktree_root": "/home/agent/agent-dev/worktrees/skeleton",
                "public": True,
                "runner_enabled": True,
                "execution_modes": {"planning_only": False},
                "future_parallel_worktrees": True,
                "runtime_approval_required": False,
                "worktree_name_prefix": "skeleton",
            },
            "lavalamp": {
                "repo": "alanua/Lavalamp",
                "checkout_path": "/home/agent/agent-dev/repos/Lavalamp",
                "worktree_root": "/home/agent/agent-dev/worktrees/lavalamp",
                "public": True,
                "runner_enabled": True,
                "execution_modes": {"planning_only": False},
                "future_parallel_worktrees": True,
                "runtime_approval_required": True,
                "worktree_name_prefix": "lavalamp",
            },
            "private_repo": {
                "repo": "private/Secret",
                "checkout_path": "/home/agent/agent-dev/repos/Secret",
                "worktree_root": "/home/agent/agent-dev/worktrees/secret",
                "public": False,
                "runner_enabled": True,
                "execution_modes": {"planning_only": False},
                "future_parallel_worktrees": False,
                "runtime_approval_required": True,
                "worktree_name_prefix": "secret",
            },
            "planning_only": {
                "repo": "alanua/Planning",
                "checkout_path": "/home/agent/agent-dev/repos/Planning",
                "worktree_root": "/home/agent/agent-dev/worktrees/planning",
                "public": True,
                "runner_enabled": True,
                "execution_modes": {"planning_only": True},
                "future_parallel_worktrees": False,
                "runtime_approval_required": True,
                "worktree_name_prefix": "planning",
            },
            "disabled": {
                "repo": "alanua/Disabled",
                "checkout_path": "/home/agent/agent-dev/repos/Disabled",
                "worktree_root": "/home/agent/agent-dev/worktrees/disabled",
                "public": True,
                "runner_enabled": False,
                "execution_modes": {"planning_only": False},
                "future_parallel_worktrees": False,
                "runtime_approval_required": True,
                "worktree_name_prefix": "disabled",
            },
        },
    }


def test_registered_runner_queue_sources_fail_closed() -> None:
    sources = registered_runner_queue_sources(_project_tree())

    assert [source.repository for source in sources] == [
        "alanua/Lavalamp",
        "alanua/Skeleton",
    ]


def test_unregistered_and_paused_sources_are_not_dispatchable() -> None:
    project_tree = _project_tree()

    assert registered_source_for_repository(project_tree, "alanua/Skeleton") is not None
    assert registered_source_for_repository(project_tree, "private/Secret") is None
    assert registered_source_for_repository(project_tree, "alanua/Planning") is None
    assert registered_source_for_repository(project_tree, "alanua/Disabled") is None
    assert registered_source_for_repository(project_tree, "alanua/Unregistered") is None


def test_get_registered_ready_issue_items_keeps_repository_identity() -> None:
    calls: list[str] = []

    def run_command(command: Sequence[str]) -> tuple[int, str]:
        repo = command[command.index("--repo") + 1]
        calls.append(repo)
        return 0, json.dumps(
            [
                {
                    "number": 7,
                    "title": repo,
                    "body": "```task\nDo it.\n```",
                    "state": "OPEN",
                    "closed": False,
                    "url": f"https://github.com/{repo}/issues/7",
                    "labels": ["runner:ready"],
                }
            ]
        )

    items = get_registered_ready_issue_items(
        _project_tree(),
        run_command,
        ready_label="runner:ready",
    )

    assert calls == ["alanua/Lavalamp", "alanua/Skeleton"]
    assert [item.identity for item in items] == [
        ("alanua/Lavalamp", 7),
        ("alanua/Skeleton", 7),
    ]
    assert len({item.identity for item in items}) == 2


def test_get_registered_ready_issue_items_sorts_p0_before_p2_across_sources() -> None:
    calls: list[str] = []

    def run_command(command: Sequence[str]) -> tuple[int, str]:
        repo = command[command.index("--repo") + 1]
        calls.append(repo)
        payload_by_repo = {
            "alanua/Lavalamp": [
                {
                    "number": 20,
                    "title": "p2 first from api",
                    "body": "```task\nP2.\n```",
                    "state": "OPEN",
                    "closed": False,
                    "url": "https://github.com/alanua/Lavalamp/issues/20",
                    "labels": ["runner:ready"],
                }
            ],
            "alanua/Skeleton": [
                {
                    "number": 30,
                    "title": "p0 second from api",
                    "body": "```task\nP0.\n```",
                    "state": "OPEN",
                    "closed": False,
                    "url": "https://github.com/alanua/Skeleton/issues/30",
                    "labels": [{"name": "queue:RUN_NOW"}],
                }
            ],
        }
        return 0, json.dumps(payload_by_repo[repo])

    items = get_registered_ready_issue_items(
        _project_tree(),
        run_command,
        ready_label="runner:ready",
    )

    assert calls == ["alanua/Lavalamp", "alanua/Skeleton"]
    assert [item.identity for item in items] == [
        ("alanua/Skeleton", 30),
        ("alanua/Lavalamp", 20),
    ]


def test_runner_vnext_queue_item_priority_orders_deterministically() -> None:
    source_a = registered_runner_queue_sources(_project_tree())[0]
    source_b = registered_runner_queue_sources(_project_tree())[1]

    items = sorted(
        [
            RunnerVNextQueueItem(source_b, {"number": 2, "labels": []}),
            RunnerVNextQueueItem(source_a, {"number": 1, "labels": ["queue:RUN_NOW"]}),
            RunnerVNextQueueItem(
                source_b,
                {"number": 1, "labels": [{"name": "runner:priority-1"}]},
            ),
        ],
        key=runner_vnext_queue_item_priority_key,
    )

    assert [item.identity for item in items] == [
        ("alanua/Lavalamp", 1),
        ("alanua/Skeleton", 1),
        ("alanua/Skeleton", 2),
    ]


def test_exact_green_canary_blocks_main_authority_drift_before_private_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = f"""Mode: {poll.DIAGNOSTIC_MODE}
Repository: alanua/Skeleton
Expected Main SHA: {"a" * 40}
Profile: {poll.EXTERNAL_ATTESTATION_PROFILE}
Idempotency Key: diag-key
"""
    bound = poll._diagnostic_bound("alanua/Skeleton", 42, body, "diag-key")
    monkeypatch.setattr(
        poll,
        "_fetch_exact_issue",
        lambda repository, issue_number: {
            "number": issue_number,
            "body": body,
            "state": "OPEN",
        },
    )
    monkeypatch.setattr(poll, "_current_repo_head_ref", lambda: f"git:{'b' * 40}")
    monkeypatch.setattr(
        poll,
        "_attest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("private attestation must not be read after main drift")
        ),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll.run_exact_green_canary(
            repository="alanua/Skeleton",
            target_issue=42,
            expected_binding_sha256=bound.source_binding_hash,
            expected_idempotency_key="diag-key",
        )

    assert exc.value.reason_code == "VNEXT_DIAGNOSTIC_HEAD_STALE"


def test_block_dispatch_error_surfaces_reason_code_without_private_task_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = registered_runner_queue_sources(_project_tree())[1]
    item = RunnerVNextQueueItem(source, {"number": 44, "body": "PRIVATE_TOKEN=secret"})
    messages: list[str] = []

    monkeypatch.setattr(
        poll.legacy,
        "block_issue",
        lambda issue_number, message: messages.append(message),
    )

    error = RuntimeError("PRIVATE_TOKEN=secret /private/task/path")
    error.reason_code = "VNEXT_PUBLIC_REASON"  # type: ignore[attr-defined]
    poll._block_dispatch_error(item, error)

    assert messages == [
        "Runner vNext authoritative dispatch blocked before unsafe fallback. "
        "reason=VNEXT_PUBLIC_REASON"
    ]
    assert "PRIVATE_TOKEN" not in messages[0]
    assert "/private/task/path" not in messages[0]

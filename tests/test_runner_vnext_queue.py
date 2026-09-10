from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from core.runner_vnext_queue import (
    RunnerVNextQueueItem,
    get_registered_ready_issue_items,
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

from __future__ import annotations

import json

from core.runner_backlog_audit import audit_runner_backlog, compact_report
from scripts import runner_backlog_audit as cli


HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40


def _issue(
    number: int,
    *,
    title: str | None = None,
    labels: list[str] | None = None,
    metadata: str = "",
    task: str = "goal: do it",
) -> dict[str, object]:
    body = f"{metadata}\n```task\n{task}\n```"
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": body,
        "state": "OPEN",
        "closed": False,
        "url": f"https://github.com/alanua/Skeleton/issues/{number}",
        "labels": [{"name": label} for label in (labels or [])],
    }


def test_audit_classifies_active_superseded_duplicate_stale_and_operator() -> None:
    issues = [
        _issue(
            1,
            labels=["agent:task", "runner:ready"],
            metadata="Privacy Boundary: PUBLIC_SAFE\nAllowed Files:\n- docs/a.md\nIntent Key: alpha",
        ),
        _issue(
            2,
            labels=["agent:task", "runner:done", "runner:ready"],
            metadata="Privacy Boundary: PUBLIC_SAFE\nIntent Key: done",
        ),
        _issue(
            3,
            labels=["agent:task", "runner:backlog"],
            metadata="Privacy Boundary: PUBLIC_SAFE\nAllowed Files:\n- docs/dup.md\nIntent Key: duplicate",
        ),
        _issue(
            4,
            labels=["agent:task", "runner:backlog"],
            metadata="Privacy Boundary: PUBLIC_SAFE\nAllowed Files:\n- docs/dup.md\nIntent Key: duplicate",
        ),
        _issue(
            5,
            labels=["agent:task", "runner:backlog"],
            metadata=f"Privacy Boundary: PUBLIC_SAFE\nBase Branch: main\nBase SHA: {HEAD_A}\nIntent Key: stale",
        ),
        _issue(
            6,
            labels=["agent:task", "runner:backlog", "runner:needs-operator"],
            metadata="Privacy Boundary: PUBLIC_SAFE\nIntent Key: human",
        ),
        _issue(
            7,
            labels=["agent:task", "runner:backlog"],
            metadata="Privacy Boundary: PRIVATE\nIntent Key: private",
        ),
    ]

    audit = audit_runner_backlog(issues, base_heads={"main": HEAD_B})

    assert audit["counts"] == {
        "active": 1,
        "superseded": 1,
        "duplicate-candidate": 2,
        "stale-base": 1,
        "needs-operator": 2,
    }
    assert audit["items"]["active"][0]["number"] == 1
    assert audit["items"]["superseded"][0]["reasons"] == [
        "terminal_runner_label",
        "terminal_with_active_label",
    ]
    assert audit["items"]["duplicate-candidate"][0]["matching_issues"] == [4]
    assert audit["items"]["stale-base"][0]["reasons"] == ["base_head_mismatch"]
    assert audit["items"]["needs-operator"][1]["reasons"] == [
        "private_privacy_boundary"
    ]


def test_audit_ignores_pull_requests_closed_items_and_non_runner_issues() -> None:
    issues = [
        _issue(1, labels=["agent:task", "runner:ready"], metadata="Privacy Boundary: PUBLIC_SAFE"),
        {
            "number": 2,
            "title": "PR",
            "state": "OPEN",
            "closed": False,
            "url": "https://github.com/alanua/Skeleton/pull/2",
            "pull_request": {"url": "https://api.github.com/repos/alanua/Skeleton/pulls/2"},
            "labels": [{"name": "runner:ready"}],
        },
        _issue(3, labels=["runner:ready"], metadata="Privacy Boundary: PUBLIC_SAFE")
        | {"closed": True},
        {"number": 4, "title": "Plain", "state": "OPEN", "labels": []},
    ]

    audit = audit_runner_backlog(issues)

    assert audit["counts"]["active"] == 1
    assert sum(audit["counts"].values()) == 1


def test_missing_public_safe_boundary_and_missing_task_need_operator() -> None:
    audit = audit_runner_backlog(
        [
            _issue(1, labels=["agent:task", "runner:backlog"]),
            _issue(2, labels=["agent:task", "runner:backlog"], metadata="Privacy Boundary: PUBLIC_SAFE")
            | {"body": "Privacy Boundary: PUBLIC_SAFE\nNo task fence"},
        ]
    )

    assert audit["items"]["needs-operator"][0]["reasons"] == [
        "missing_public_safe_privacy_boundary"
    ]
    assert audit["items"]["needs-operator"][1]["reasons"] == ["missing_task_block"]


def test_pr_head_mismatch_is_stale_base() -> None:
    issue = _issue(
        9,
        labels=["agent:task", "runner:backlog"],
        metadata=(
            "Privacy Boundary: PUBLIC_SAFE\n"
            "Pull Request: 44\n"
            f"Expected Head SHA: {HEAD_A}\n"
            "Intent Key: pr-refresh"
        ),
    )

    audit = audit_runner_backlog([issue], pr_heads={44: HEAD_C})

    assert audit["counts"]["stale-base"] == 1
    assert audit["items"]["stale-base"][0]["reasons"] == ["pr_head_mismatch"]


def test_compact_report_is_deterministic_and_dashboard_sized() -> None:
    audit = audit_runner_backlog(
        [
            _issue(
                10,
                labels=["agent:task", "runner:ready"],
                metadata="Privacy Boundary: PUBLIC_SAFE\nIntent Key: ship-doc",
            )
        ],
        repo="alanua/Skeleton",
    )

    assert compact_report(audit) == (
        "RUNNER_BACKLOG_AUDIT alanua/Skeleton "
        "active=1 superseded=0 duplicate-candidate=0 stale-base=0 needs-operator=0\n"
        "active #10 queued intent=ship-doc"
    )


def test_cli_uses_read_only_gh_commands_and_json_output(monkeypatch) -> None:
    issue = _issue(
        20,
        labels=["agent:task", "runner:backlog"],
        metadata=f"Privacy Boundary: PUBLIC_SAFE\nBase SHA: {HEAD_A}\nIntent Key: stale",
    )
    calls: list[list[str]] = []

    def fake_run_command(command: list[str]) -> tuple[int, str]:
        calls.append(command)
        if command[:3] == ["gh", "issue", "list"]:
            return 0, json.dumps([issue])
        if command[:2] == ["gh", "api"]:
            return 0, HEAD_B + "\n"
        raise AssertionError(command)

    monkeypatch.setattr(cli, "run_command", fake_run_command)

    assert cli.main(["--format", "json"]) == 0

    assert calls[0][:3] == ["gh", "issue", "list"]
    assert all("edit" not in command and "close" not in command for command in calls)

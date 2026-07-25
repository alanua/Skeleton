from __future__ import annotations

# Synchronize the bounded protected-route patch workflow for PR #1944.

import json
from pathlib import Path
from typing import Mapping

from core.runner_five_layer_memory_activation import (
    OPERATOR_APPROVAL,
    TASK_ID,
    execute_five_layer_memory_activation,
)

SHA = "a" * 40


def _body(*, sha: str = SHA, approval: str = OPERATOR_APPROVAL) -> str:
    return (
        "Mode: RUNTIME_MAINTENANCE_TASK\n"
        f"Maintenance Task ID: {TASK_ID}\n"
        f"Expected Main SHA: {sha}\n"
        f"Operator Approval: {approval}\n"
    )


def _report(
    status: str, task_id: str, lines: list[str], success: str
) -> str:
    return "\n".join(
        [
            f"{status}: Runner host maintenance task completed.",
            f"maintenance_task_id={task_id}",
            *lines,
            f"success_criteria={success}",
        ]
    )


def _receipt(*, status: str = "DONE") -> str:
    booleans = {
        "gateway_canonical": True,
        "projection_queue": True,
        "cognee_selected": True,
        "mempalace_fallback": True,
        "graphify_fresh": True,
        "project_isolation": True,
        "revision_invalidation": True,
        "mandatory_bootstrap": True,
        "handoff_cleanup": True,
        "private_echo_blocked": True,
        "forget_verified": True,
        "live_status_checked": True,
        "private_leak_detected": False,
    }
    return json.dumps(
        {
            "schema": "skeleton.five_layer_memory_activation_receipt.v2",
            "status": status,
            "reason_codes": [status],
            "source_sha": SHA,
            "booleans": booleans,
            "aggregate_counts": {
                "canonical_count": 1,
                "semantic_count": 1,
                "graph_count": 1,
                "outbox_done_count": 1,
            },
            "resource_totals": {
                "elapsed_ms": 100,
                "disk_bytes": 2048,
                "peak_rss_bytes": 4096,
            },
            "rollback": {"verified": True, "status": "verified"},
        },
        sort_keys=True,
    )


def _runner(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout: int,
) -> tuple[int, str, str]:
    del cwd, timeout
    if command[:3] == ["git", "rev-parse", "HEAD"]:
        return 0, SHA + "\n", ""
    if command[:3] == ["git", "branch", "--show-current"]:
        return 0, "main\n", ""
    if command[:3] == ["git", "status", "--porcelain"]:
        return 0, "", ""
    if command[:4] == ["git", "remote", "get-url", "origin"]:
        return 0, "https://github.com/alanua/Skeleton.git\n", ""
    if command == ["ollama", "list"]:
        return (
            0,
            "NAME ID SIZE MODIFIED\nqwen2.5:3b id 2GB now\n"
            "nomic-embed-text:latest id 1GB now\n",
            "",
        )
    assert command[:3] == [command[0], "-m", "scripts.activate_five_layer_private_memory"]
    assert env is not None
    assert env["SKELETON_COGNEE_LLM_MODEL"] == "qwen2.5:3b"
    assert env["SKELETON_COGNEE_EMBEDDING_MODEL"] == "nomic-embed-text:latest"
    assert "OPENAI_API_KEY" not in env
    return 0, _receipt() + "\n", ""


def test_activation_executor_returns_public_safe_done(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SKELETON_RUNNER_PRIVATE_MEMORY_ROOT", str(tmp_path / "private"))
    report = execute_five_layer_memory_activation(
        _body(),
        workdir=tmp_path,
        maintenance_report=_report,
        command_runner=_runner,
    )
    assert report.startswith("DONE:")
    assert "head_sha=" + SHA in report
    assert "runtime_smoke_check_count=12" in report
    assert "disk_bytes=2048" in report
    assert str(tmp_path) not in report
    assert "success_criteria=met" in report


def test_activation_executor_rejects_wrong_approval(tmp_path: Path) -> None:
    report = execute_five_layer_memory_activation(
        _body(approval="wrong"),
        workdir=tmp_path,
        maintenance_report=_report,
        command_runner=_runner,
    )
    assert report.startswith("BLOCKED:")
    assert "reason=operator_approval_invalid" in report


def test_activation_executor_fails_closed_on_receipt_check(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SKELETON_RUNNER_PRIVATE_MEMORY_ROOT", str(tmp_path / "private"))

    def runner(
        command: list[str],
        cwd: Path,
        env: Mapping[str, str] | None,
        timeout: int,
    ) -> tuple[int, str, str]:
        code, stdout, stderr = _runner(command, cwd, env, timeout)
        if command[:3] == [command[0], "-m", "scripts.activate_five_layer_private_memory"]:
            payload = json.loads(stdout)
            payload["booleans"]["cognee_selected"] = False
            return 1, json.dumps(payload), "private details must not surface"
        return code, stdout, stderr

    report = execute_five_layer_memory_activation(
        _body(),
        workdir=tmp_path,
        maintenance_report=_report,
        command_runner=runner,
    )
    assert report.startswith("BLOCKED:")
    assert "reason=activation_receipt_checks_failed" in report
    assert "private details" not in report


def test_runner_poller_registers_activation_route() -> None:
    source = Path("scripts/runner_poll_github_tasks.py").read_text(encoding="utf-8")
    assert "ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY" in source
    assert "execute_five_layer_memory_activation" in source
    assert "if task_id == ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY:" in source

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from core import runner_diagnostic_executor as diagnostic


TASK_ID = "mempalace_synthetic_runtime_smoke"
MODE = "RUNTIME_MAINTENANCE_TASK"


@dataclass(frozen=True)
class Checkout:
    checkout_path: Path


def maintenance_report(
    status: str,
    task_id: str,
    lines: list[str],
    criteria: str,
) -> str:
    return "\n".join(
        [
            f"{status}: {task_id}",
            *lines,
            f"success_criteria={criteria}",
        ]
    )


def valid_benchmark_report() -> dict[str, object]:
    return {
        "schema": diagnostic.MEMPALACE_SYNTHETIC_BENCHMARK_SCHEMA,
        "namespace": diagnostic.MEMPALACE_SYNTHETIC_NAMESPACE,
        "project_id": diagnostic.MEMPALACE_SYNTHETIC_PROJECT_ID,
        "decision": "PASS",
        "quality_score": 1.0,
        "quality_threshold": 0.8,
        "checks": [{"name": "synthetic", "passed": True}],
        "stable_reasons": sorted(
            diagnostic.MEMPALACE_SYNTHETIC_REQUIRED_STABLE_REASONS
        ),
        "resource_report": {
            "aggregate_disk_bytes": 10,
            "aggregate_ram_bytes": 20,
            "aggregate_build_ms": 30,
        },
    }


def execute(
    body: str,
    *,
    tmp_path: Path,
    run_command,
    preflight=None,
) -> str:
    if preflight is None:
        preflight = lambda task_id: (
            Checkout(tmp_path),
            ["step=preflight status=done"],
            None,
        )

    return diagnostic.execute_mempalace_synthetic_runtime_smoke(
        body,
        task_id=TASK_ID,
        reject_issue_input=lambda value: (
            diagnostic.reject_mempalace_runtime_smoke_issue_input(
                value,
                runtime_maintenance_mode=MODE,
                task_id=TASK_ID,
            )
        ),
        preflight=preflight,
        output_has_private_marker=(
            diagnostic.mempalace_benchmark_output_has_private_marker
        ),
        parse_benchmark_json=(
            diagnostic.parse_mempalace_benchmark_json
        ),
        validate_benchmark_report=(
            diagnostic.validate_mempalace_benchmark_report
        ),
        run_command=run_command,
        maintenance_report=maintenance_report,
        timeout_seconds=60,
    )


def test_executor_happy_path_preserves_command_and_report(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path, int]] = []

    def run_command(
        args: list[str],
        cwd: str | Path | None = None,
        *,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        assert isinstance(cwd, Path)
        assert timeout is not None
        calls.append((args, cwd, timeout))
        return 0, json.dumps(valid_benchmark_report())

    report = execute(
        (
            f"Mode: {MODE}\n"
            f"Maintenance Task ID: {TASK_ID}\n"
        ),
        tmp_path=tmp_path,
        run_command=run_command,
    )

    assert report.startswith(f"DONE: {TASK_ID}")
    assert "runtime_smoke_decision=PASS" in report
    assert "quality_score=1.0" in report
    assert "quality_threshold=0.8" in report
    assert "runtime_smoke_check_count=1" in report
    assert "disk_bytes=10" in report
    assert "ram_bytes=20" in report
    assert "build_ms=30" in report
    assert "success_criteria=met" in report
    assert calls == [
        (
            [
                "python3",
                "scripts/mempalace_synthetic_benchmark.py",
            ],
            tmp_path,
            60,
        )
    ]


def test_executor_rejects_issue_controlled_input_before_preflight(
    tmp_path: Path,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("execution dependency must not be called")

    report = execute(
        (
            f"Mode: {MODE}\n"
            f"Maintenance Task ID: {TASK_ID}\n"
            "```task\ncommand: sudo env\n```\n"
        ),
        tmp_path=tmp_path,
        run_command=forbidden,
        preflight=forbidden,
    )

    assert report.startswith(f"BLOCKED: {TASK_ID}")
    assert "reason=issue_controlled_input_not_allowed" in report


def test_executor_preserves_preflight_block_report(
    tmp_path: Path,
) -> None:
    expected = (
        f"BLOCKED: {TASK_ID}\n"
        "reason=checkout_dirty\n"
        "success_criteria=not_met"
    )

    def forbidden(*args, **kwargs):
        pytest.fail("benchmark command must not run")

    report = execute(
        "",
        tmp_path=tmp_path,
        run_command=forbidden,
        preflight=lambda task_id: (
            None,
            ["step=preflight status=done"],
            expected,
        ),
    )

    assert report == expected


def test_executor_maps_timeout_and_launch_failures(
    tmp_path: Path,
) -> None:
    def timeout_command(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["python3"],
            timeout=60,
        )

    timeout_report = execute(
        "",
        tmp_path=tmp_path,
        run_command=timeout_command,
    )
    assert "reason=benchmark_timeout" in timeout_report

    def failed_command(*args, **kwargs):
        raise OSError("synthetic launch failure")

    failed_report = execute(
        "",
        tmp_path=tmp_path,
        run_command=failed_command,
    )
    assert "reason=benchmark_launch_failed" in failed_report


@pytest.mark.parametrize(
    ("exit_code", "output", "reason"),
    (
        (0, "token leaked", "private_like_benchmark_output"),
        (7, json.dumps(valid_benchmark_report()), "benchmark_nonzero_exit"),
        (0, "not-json", "malformed_benchmark_json"),
        (
            0,
            json.dumps(
                {
                    **valid_benchmark_report(),
                    "decision": "REJECT",
                }
            ),
            "benchmark_decision_not_pass",
        ),
    ),
)
def test_executor_preserves_failure_reason_tokens(
    tmp_path: Path,
    exit_code: int,
    output: str,
    reason: str,
) -> None:
    def run_command(*args, **kwargs):
        return exit_code, output

    report = execute(
        "",
        tmp_path=tmp_path,
        run_command=run_command,
    )

    assert report.startswith(f"BLOCKED: {TASK_ID}")
    assert f"reason={reason}" in report
    assert "success_criteria=not_met" in report


def test_executor_module_has_no_poller_or_queue_dependency() -> None:
    source = Path(diagnostic.__file__).read_text(
        encoding="utf-8"
    )

    assert "runner_poll_github_tasks" not in source
    assert "gh issue" not in source
    assert "runner:ready" not in source

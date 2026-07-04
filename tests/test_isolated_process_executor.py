from __future__ import annotations

import sys

from core.isolated_process_executor import isolated_local_process_executor
from core.task_envelope import parse_task_envelope


def test_process_receives_fixed_environment() -> None:
    envelope = parse_task_envelope(
        {
            "schema": "skeleton.runner.task_envelope.v1",
            "task_id": "isolated-process",
            "executor_class": "local.process",
            "target": None,
            "steps": [{"argv": [sys.executable, "-c", "print('ok')"]}],
            "timeout_seconds": 30,
            "environment_refs": [],
            "expected_assertions": [],
            "rollback_policy": {"mode": "none"},
            "privacy_class": "private",
            "risk_class": "yellow",
            "approval": {
                "operator_approved": True,
                "approval_id": "approval-001",
                "second_stage_approved": False,
            },
            "idempotency_key": "isolated-process-v1",
            "evidence_policy": {"public": "aggregate_only"},
        }
    )

    result = isolated_local_process_executor(
        envelope,
        {
            "argv": [
                sys.executable,
                "-c",
                "import os; print(sorted(os.environ))",
            ]
        },
    )

    assert result.status == "DONE"
    assert "PATH" in result.output
    assert "LANG" in result.output

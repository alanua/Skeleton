from __future__ import annotations

import copy
from pathlib import Path

from core import runner_private_memory_executor as executor
from scripts import runner_poll_github_tasks as runner


def _maintenance_report(
    status: str, task_id: str, status_lines: list[str], success_criteria: str
) -> str:
    return "\n".join(
        (
            f"{status}: test maintenance report",
            f"maintenance_task_id={task_id}",
            *status_lines,
            f"success_criteria={success_criteria}",
        )
    )


def test_executor_smoke_matches_poller_route_report() -> None:
    executor_report = executor.execute_hermes_memory_gateway_smoke(
        task_id=runner.HERMES_MEMORY_GATEWAY_SMOKE,
        maintenance_report=runner._maintenance_report,
        gateway_factory=runner.MemoryGateway,
        capability_token_factory=runner.capability_token,
        run_task_packet=runner.run_hermes_memory_task_packet,
    )

    assert executor_report == runner.hermes_memory_gateway_smoke()


def test_executor_worker_injection_preserves_contract_mismatch_token() -> None:
    original = runner.run_hermes_memory_task_packet

    def corrupting_worker(packet: dict[str, object], *, gateway: object) -> object:
        result = copy.deepcopy(original(packet, gateway=gateway))
        assert isinstance(result, dict)
        if packet.get("operation") == "memory.get_conflicts":
            result["operation"] = "memory.lookup_exact"
        return result

    report = executor.execute_hermes_memory_gateway_smoke(
        task_id=runner.HERMES_MEMORY_GATEWAY_SMOKE,
        maintenance_report=_maintenance_report,
        gateway_factory=runner.MemoryGateway,
        capability_token_factory=runner.capability_token,
        run_task_packet=corrupting_worker,
    )

    assert report.startswith("BLOCKED:")
    assert "status_token=hermes_result_operation_mismatch" in report
    assert "reason=hermes_result_operation_mismatch" in report
    assert "hermes_memory_smoke_status=blocked" in report


def test_poller_wrapper_uses_current_worker_monkeypatch(monkeypatch) -> None:
    def blocked_worker(packet: dict[str, object], *, gateway: object) -> object:
        return {
            "schema": "wrong.schema",
            "status": "DRY_RUN_OK",
            "operation": packet.get("operation"),
            "namespace": packet.get("namespace"),
            "project_id": packet.get("project_id"),
            "gateway": {},
            "payload": {},
        }

    monkeypatch.setattr(runner, "run_hermes_memory_task_packet", blocked_worker)

    report = runner.hermes_memory_gateway_smoke()

    assert report.startswith("BLOCKED:")
    assert "status_token=hermes_result_schema_mismatch" in report
    assert "reason=hermes_result_schema_mismatch" in report


def test_executor_does_not_import_poller() -> None:
    source = Path(executor.__file__).read_text(encoding="utf-8")
    assert "runner_poll_github_tasks" not in source
    assert "from scripts" not in source
    assert not hasattr(executor, "RUNTIME_MAINTENANCE_TASK_IDS")

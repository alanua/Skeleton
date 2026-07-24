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


def _activation_env() -> dict[str, str]:
    return {
        "SKELETON_COGNEE_LLM_ENDPOINT": "http://127.0.0.1:11434",
        "SKELETON_COGNEE_LLM_MODEL": "llama-local",
        "SKELETON_COGNEE_EMBEDDING_ENDPOINT": "http://localhost:11435",
        "SKELETON_COGNEE_EMBEDDING_MODEL": "embed-local",
    }


def test_activation_executor_enables_after_synthetic_success_and_public_receipt(tmp_path: Path) -> None:
    installed: list[list[str]] = []

    def installer(command: list[str], _env: object) -> tuple[int, str]:
        installed.append(command)
        return 0, "installed"

    report = executor.activate_five_layer_private_memory_runtime(
        private_root=str(tmp_path),
        expected_head_sha="a" * 40,
        actual_head_sha="a" * 40,
        canonical_origin="https://github.com/alanua/Skeleton.git",
        checkout_clean=True,
        operator_approval="EXPLICIT_FINISH_WORKING_MEMORY_20260724",
        env=_activation_env(),
        maintenance_report=_maintenance_report,
        installer=installer,
        synthetic_smoke=lambda _root, _config: executor._default_activation_smoke(_root, _config),  # type: ignore[attr-defined]
        live_status=lambda _root: {"canonical_count": 1, "semantic_count": 1, "graph_count": 1},
    )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=activate_five_layer_private_memory_runtime" in report
    assert "cognee_selected=true" in report
    assert "mempalace_fallback_proven=true" in report
    assert "graphify_confirmed=true" in report
    assert "live_status_checked=true" in report
    assert "rollback_status=verified" in report
    assert "cognee==1.4.0" in " ".join(installed[0])
    assert str(tmp_path) not in report


def test_activation_executor_restores_previous_marker_on_failed_smoke(tmp_path: Path) -> None:
    from core.cognee_local_runtime import CogneeProviderConfig, atomic_write_activation_marker, read_activation_marker

    provider = CogneeProviderConfig(
        llm_endpoint="http://127.0.0.1:11434",
        llm_model="old",
        embedding_endpoint="http://localhost:11435",
        embedding_model="old-embed",
    )
    previous = atomic_write_activation_marker(tmp_path, expected_head_sha="b" * 40, provider_config=provider)

    report = executor.activate_five_layer_private_memory_runtime(
        private_root=str(tmp_path),
        expected_head_sha="a" * 40,
        actual_head_sha="a" * 40,
        canonical_origin="https://github.com/alanua/Skeleton.git",
        checkout_clean=True,
        operator_approval="EXPLICIT_FINISH_WORKING_MEMORY_20260724",
        env=_activation_env(),
        maintenance_report=_maintenance_report,
        installer=lambda _cmd, _env: (0, "installed"),
        synthetic_smoke=lambda _root, _config: {**executor._default_activation_smoke(_root, _config), "graphify_fresh": False},  # type: ignore[attr-defined]
    )

    assert report.startswith("BLOCKED:")
    assert "reason=graphify_fresh_failed" in report
    assert read_activation_marker(tmp_path) == previous

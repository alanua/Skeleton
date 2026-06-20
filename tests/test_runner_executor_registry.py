from __future__ import annotations

from typing import Any, Mapping

from core.runner_executor_registry import (
    RegisteredCommandExecutor,
    RegistryHermesAdapter,
    RunnerExecutorRegistry,
)


def test_local_module_task_uses_registered_command_boundary() -> None:
    commands = RegisteredCommandExecutor(
        {"safe_command": lambda payload: {"status": "COMPLETED", "value": "ok"}}
    )

    result = commands.run("safe_command", {"ignored": "not shell text"})

    assert result.status == "COMPLETED"
    assert result.public == {"value": "ok"}


def test_local_module_task_rejects_unregistered_command_text() -> None:
    commands = RegisteredCommandExecutor()

    result = commands.run("rm_rf_everything", {"command": "rm -rf /"})

    assert result.status == "FAILED"
    assert result.public["reason"] == "registered_command_missing"


class MockHermesServerRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def dispatch(self, command_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((command_id, payload))
        return {
            "status": "COMPLETED",
            "summary": "aggregate ok",
        }


def test_hermes_adapter_uses_mocked_server_registry() -> None:
    server = MockHermesServerRegistry()
    adapter = RegistryHermesAdapter(server, allowed_commands={"summarize_private"})
    registry = RunnerExecutorRegistry(hermes=adapter)

    result = registry.run_hermes_task(
        "summarize_private", {"private_ref": "opaque-task-ref"}
    )

    assert result.status == "COMPLETED"
    assert server.calls == [("summarize_private", {"private_ref": "opaque-task-ref"})]
    assert result.public["summary"] == "aggregate ok"


def test_hermes_adapter_fails_closed_for_missing_registered_command() -> None:
    server = MockHermesServerRegistry()
    adapter = RegistryHermesAdapter(server, allowed_commands={"summarize_private"})

    result = adapter.dispatch("other_private_command", {})

    assert result.status == "NEEDS_OPERATOR"
    assert result.public["reason"] == "hermes_command_not_registered"
    assert server.calls == []


def test_hermes_adapter_never_expands_private_payload_publicly() -> None:
    class LeakyRegistry:
        def dispatch(
            self, command_id: str, payload: Mapping[str, Any]
        ) -> Mapping[str, Any]:
            return {"status": "COMPLETED", "private_payload": "raw secret"}

    adapter = RegistryHermesAdapter(
        LeakyRegistry(), allowed_commands={"summarize_private"}
    )

    result = adapter.dispatch("summarize_private", {"private_ref": "opaque"})

    assert result.status == "FAILED"
    assert result.public == {
        "reason": "hermes_registry_public_payload_unsafe",
        "command_id": "summarize_private",
    }

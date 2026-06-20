from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from typing import Any, Protocol

from core.audit_ledger import validate_public_safe_payload


RunnerHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class HermesServerRegistry(Protocol):
    def dispatch(self, command_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run a server-side Hermes command selected by opaque id."""


@dataclass(frozen=True)
class RegisteredCommandResult:
    status: str
    public: dict[str, Any]


class RegisteredCommandExecutor:
    def __init__(self, commands: Mapping[str, RunnerHandler] | None = None) -> None:
        self._commands: dict[str, RunnerHandler] = dict(commands or {})

    def register(self, command_id: str, handler: RunnerHandler) -> None:
        _validate_command_id(command_id)
        self._commands[command_id] = handler

    def run(self, command_id: str, payload: Mapping[str, Any]) -> RegisteredCommandResult:
        _validate_command_id(command_id)
        handler = self._commands.get(command_id)
        if handler is None:
            return RegisteredCommandResult(
                status="FAILED",
                public={"reason": "registered_command_missing", "command_id": command_id},
            )
        result = dict(handler(dict(payload)))
        status = str(result.pop("status", "COMPLETED")).upper()
        if status not in {"CHECKPOINTED", "NEEDS_OPERATOR", "RUNNING", "CANCELLED", "FAILED", "COMPLETED"}:
            status = "FAILED"
            result = {"reason": "registered_command_returned_invalid_status"}
        try:
            validate_public_safe_payload({"result": result})
        except ValueError:
            status = "FAILED"
            result = {"reason": "registered_command_public_payload_unsafe"}
        return RegisteredCommandResult(status=status, public=result)


class RegistryHermesAdapter:
    """Fail-closed Hermes boundary backed by a server-owned command registry."""

    def __init__(self, registry: HermesServerRegistry, *, allowed_commands: set[str] | frozenset[str]):
        self._registry = registry
        self._allowed_commands = frozenset(allowed_commands)

    def dispatch(self, command_id: str, payload: Mapping[str, Any]) -> RegisteredCommandResult:
        _validate_command_id(command_id)
        if command_id not in self._allowed_commands:
            return RegisteredCommandResult(
                status="NEEDS_OPERATOR",
                public={"reason": "hermes_command_not_registered", "command_id": command_id},
            )
        try:
            result = dict(self._registry.dispatch(command_id, dict(payload)))
        except Exception:
            return RegisteredCommandResult(
                status="FAILED",
                public={"reason": "hermes_registry_dispatch_failed", "command_id": command_id},
            )
        status = str(result.pop("status", "COMPLETED")).upper()
        if status not in {"CHECKPOINTED", "NEEDS_OPERATOR", "RUNNING", "CANCELLED", "FAILED", "COMPLETED"}:
            return RegisteredCommandResult(
                status="FAILED",
                public={"reason": "hermes_registry_invalid_status", "command_id": command_id},
            )
        if any("private" in key.lower() for key in result):
            return RegisteredCommandResult(
                status="FAILED",
                public={"reason": "hermes_registry_public_payload_unsafe", "command_id": command_id},
            )
        try:
            validate_public_safe_payload({"result": result})
        except ValueError:
            return RegisteredCommandResult(
                status="FAILED",
                public={"reason": "hermes_registry_public_payload_unsafe", "command_id": command_id},
            )
        return RegisteredCommandResult(status=status, public=result)


class RunnerExecutorRegistry:
    def __init__(
        self,
        *,
        local_commands: RegisteredCommandExecutor | None = None,
        hermes: RegistryHermesAdapter | None = None,
    ) -> None:
        self.local_commands = local_commands or RegisteredCommandExecutor()
        self.hermes = hermes

    def run_local_module_task(
        self, command_id: str, payload: Mapping[str, Any]
    ) -> RegisteredCommandResult:
        return self.local_commands.run(command_id, payload)

    def run_hermes_task(
        self, command_id: str, payload: Mapping[str, Any]
    ) -> RegisteredCommandResult:
        if self.hermes is None:
            return RegisteredCommandResult(
                status="NEEDS_OPERATOR",
                public={"reason": "hermes_adapter_missing", "command_id": command_id},
            )
        return self.hermes.dispatch(command_id, payload)


_COMMAND_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,80}$")


def _validate_command_id(command_id: str) -> None:
    if not isinstance(command_id, str) or _COMMAND_ID_RE.fullmatch(command_id) is None:
        raise ValueError("command_id must be a registered command token.")

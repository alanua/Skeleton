from __future__ import annotations

from collections.abc import Mapping

from adapters.credential_control import CredentialControlAdapter


MCP_SCHEMA = "skeleton.credential_mcp.v1"


def credential_mcp_tool_specs() -> tuple[dict[str, object], ...]:
    alias_input = {
        "type": "object",
        "properties": {"alias": {"type": "string"}},
        "required": ["alias"],
        "additionalProperties": False,
    }
    use_input = {
        "type": "object",
        "properties": {
            "alias": {"type": "string"},
            "action_id": {"type": "string"},
        },
        "required": ["alias", "action_id"],
        "additionalProperties": False,
    }
    return (
        {
            "name": "credential_probe",
            "description": "Probe one logical credential alias; returns status only.",
            "inputSchema": alias_input,
        },
        {
            "name": "credential_find",
            "description": "Find one registered logical credential alias; returns status only.",
            "inputSchema": alias_input,
        },
        {
            "name": "credential_use",
            "description": "Execute one pre-registered credential action; never returns the secret.",
            "inputSchema": use_input,
        },
    )


class CredentialMcpAdapter:
    """Transport-neutral MCP surface around one already service-bound control adapter."""

    def __init__(self, control: CredentialControlAdapter) -> None:
        if not isinstance(control, CredentialControlAdapter):
            raise TypeError("typed_credential_control_required")
        self._control = control

    @property
    def service_id(self) -> str:
        return self._control.service_id

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return credential_mcp_tool_specs()

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        if name not in {"credential_probe", "credential_find", "credential_use"}:
            return {
                "schema": MCP_SCHEMA,
                "service_id": self.service_id,
                "result": {
                    "status": "BLOCKED",
                    "reason_class": "UNSUPPORTED_TOOL",
                },
            }
        if not isinstance(arguments, Mapping):
            return {
                "schema": MCP_SCHEMA,
                "service_id": self.service_id,
                "result": {
                    "status": "BLOCKED",
                    "reason_class": "INVALID_ARGUMENTS",
                },
            }
        return self._control.invoke(name, arguments)

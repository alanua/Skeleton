from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatewayActionSpec:
    action_id: str
    domain: str
    risk: str
    description: str
    requires_observed_runtime: bool = True
    mutating: bool = False


_GATEWAY_ACTION_LIST = (
    GatewayActionSpec("diagnostic", "gateway", "read_only", "Collect the complete public-safe home-edge baseline."),
    GatewayActionSpec("identity", "gateway", "read_only", "Return registered node identity plus observed transport state."),
    GatewayActionSpec("gateway_capabilities", "gateway", "read_only", "Return the typed action and risk contract for the universal gate.", requires_observed_runtime=False),
    GatewayActionSpec("system_inventory", "system", "read_only", "Inspect OS, kernel, uptime, memory, storage and load."),
    GatewayActionSpec("network_inventory", "network", "read_only", "Inspect routes and network reachability."),
    GatewayActionSpec("service_inventory", "services", "read_only", "Inspect allowlisted service state."),
    GatewayActionSpec("container_inventory", "containers", "read_only", "Inspect container availability and aggregate running counts."),
    GatewayActionSpec("media_inventory", "media", "read_only", "Inspect media tooling and local display or audio runtime availability."),
    GatewayActionSpec("browser_diagnostic", "browser", "read_only", "Inspect Chrome or Chromium executables, process state and profile locks."),
    GatewayActionSpec("hardware_inventory", "hardware", "read_only", "Inspect public-safe USB and platform capability summaries."),
    GatewayActionSpec("home_automation_inventory", "home_automation", "read_only", "Inspect local home-automation runtime availability."),
    GatewayActionSpec("modem_diagnostic", "network", "read_only", "Inspect an attached modem when present; absence is not a gate failure."),
    GatewayActionSpec("tool_inventory", "system", "read_only", "Inspect the universal node toolchain."),
    GatewayActionSpec("prepare_runtime_bootstrap", "gateway", "approved_mutation", "Return a non-executing runtime preparation record.", requires_observed_runtime=False),
)

GATEWAY_ACTIONS = {item.action_id: item for item in _GATEWAY_ACTION_LIST}
AUDITED_GATEWAY_COMMANDS = frozenset(GATEWAY_ACTIONS)
PUBLIC_GATEWAY_DOMAINS = (
    "gateway",
    "system",
    "network",
    "services",
    "containers",
    "media",
    "browser",
    "hardware",
    "home_automation",
)
RISK_LANES = ("read_only", "approved_mutation", "destructive_manual")


def gateway_contract() -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.gateway_contract.v1",
        "node_id": "home-edge-01",
        "target": "valertos08@100.127.35.74",
        "transport": "openssh_over_tailscale_ip",
        "task_model": "typed_allowlisted_actions",
        "domains": list(PUBLIC_GATEWAY_DOMAINS),
        "risk_lanes": list(RISK_LANES),
        "raw_shell_from_issue_payload": "forbidden",
        "external_connection_fields_from_issue_payload": "forbidden",
        "actions": [
            {
                "action_id": item.action_id,
                "domain": item.domain,
                "risk": item.risk,
                "requires_observed_runtime": item.requires_observed_runtime,
                "mutating": item.mutating,
                "description": item.description,
            }
            for item in _GATEWAY_ACTION_LIST
        ],
    }


def prepared_runtime_bootstrap() -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.prepared_action.v2",
        "action_id": "bootstrap_home_edge_runner_transport",
        "node_id": "home-edge-01",
        "status": "prepared_not_executed",
        "risk": "approved_mutation",
        "operator_approval_required": True,
        "public_artifact_contains_secret_material": False,
    }

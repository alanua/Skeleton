from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diagnostics import DEFAULT_ARTIFACT_PATH, build_operator_report, run_home_edge_diagnostic
from .gateway import AUDITED_GATEWAY_COMMANDS, gateway_contract, prepared_runtime_bootstrap
from .profile import HomeEdgeProfile, load_home_edge_profile
from .transport import ProbeTransport


AUDITED_COMMANDS = AUDITED_GATEWAY_COMMANDS


class HomeEdgeRemoteError(ValueError):
    """Raised when a requested home-edge action is outside the audited gate."""


def run_audited_home_edge_command(
    command: str,
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
    transport: ProbeTransport | None = None,
) -> dict[str, Any]:
    if command not in AUDITED_COMMANDS:
        raise HomeEdgeRemoteError(f"home-edge action is not allowlisted: {command}")
    node = profile or load_home_edge_profile()

    if command == "gateway_capabilities":
        return gateway_contract()
    if command == "prepare_runtime_bootstrap":
        return prepared_runtime_bootstrap()

    artifact = run_home_edge_diagnostic(
        profile=node,
        artifact_path=artifact_path,
        transport=transport,
    )
    if command == "diagnostic":
        return artifact
    if command == "identity":
        return {
            "schema": "skeleton.home_edge.identity.v2",
            "node": artifact["node"],
            "controller": artifact["controller"],
            "runtime_evidence": artifact["evidence"]["runtime"],
            "gateway": artifact["summary"]["gateway"],
        }

    runtime = artifact.get("runtime")
    runtime_data = runtime if isinstance(runtime, dict) else {}
    projections: dict[str, tuple[str, Any]] = {
        "system_inventory": ("system", runtime_data.get("system")),
        "network_inventory": (
            "network",
            {
                "network": runtime_data.get("network"),
                "tailscale": runtime_data.get("tailscale"),
                "route_summary": artifact["summary"]["route"],
                "tailscale_summary": artifact["summary"]["tailscale"],
            },
        ),
        "service_inventory": ("services", runtime_data.get("services")),
        "container_inventory": ("containers", runtime_data.get("containers")),
        "media_inventory": ("media", runtime_data.get("media")),
        "browser_diagnostic": ("browser", runtime_data.get("browser")),
        "hardware_inventory": ("hardware", runtime_data.get("hardware")),
        "home_automation_inventory": (
            "home_automation",
            runtime_data.get("home_automation"),
        ),
        "modem_diagnostic": ("modem", artifact["summary"]["modem"]),
        "tool_inventory": ("tools", runtime_data.get("tools")),
    }
    domain, value = projections[command]
    evidence_state = artifact["evidence"]["runtime"]["state"]
    return {
        "schema": f"skeleton.home_edge.{command}.v1",
        "node_id": node.node_id,
        "action_id": command,
        "domain": domain,
        "evidence": evidence_state,
        "status": "observed" if evidence_state == "observed" else "unverified",
        "value": value if evidence_state == "observed" else None,
        "transport": artifact["evidence"]["runtime"]["transport"],
    }


def compact_status(artifact: dict[str, Any]) -> dict[str, str]:
    summary = artifact.get("summary", {})
    gateway = summary.get("gateway", {}) if isinstance(summary, dict) else {}
    modem = summary.get("modem", {}) if isinstance(summary, dict) else {}
    route = summary.get("route", {}) if isinstance(summary, dict) else {}
    tailscale = summary.get("tailscale", {}) if isinstance(summary, dict) else {}
    modem_observed = modem.get("observed") if isinstance(modem, dict) else None
    observed = modem_observed if isinstance(modem_observed, dict) else {}
    return {
        "node": str(artifact.get("node", {}).get("node_id", "home-edge-01")),
        "gateway_status": str(gateway.get("status", "unverified")),
        "route_status": str(route.get("status", "unverified")),
        "tailscale_status": str(tailscale.get("status", "unverified")),
        "modem_status": str(modem.get("status", "unverified")),
        "modem_lock_state": str(observed.get("state", "unverified")).replace(" ", "_"),
        "connection_mode": str(observed.get("connection_mode", "unverified")),
    }


def dumps_public_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


__all__ = [
    "AUDITED_COMMANDS",
    "HomeEdgeRemoteError",
    "build_operator_report",
    "compact_status",
    "dumps_public_json",
    "run_audited_home_edge_command",
]

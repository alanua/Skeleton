from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diagnostics import DEFAULT_ARTIFACT_PATH, build_operator_report, run_home_edge_diagnostic
from .profile import HomeEdgeProfile, load_home_edge_profile


AUDITED_COMMANDS = frozenset(
    {
        "diagnostic",
        "identity",
        "tool_inventory",
        "modem_diagnostic",
        "prepare_private_unlock_plan",
    }
)


class HomeEdgeRemoteError(ValueError):
    """Raised when a requested home-edge command is outside the audited route."""


def run_audited_home_edge_command(
    command: str,
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
) -> dict[str, Any]:
    if command not in AUDITED_COMMANDS:
        raise HomeEdgeRemoteError(f"home-edge command is not allowlisted: {command}")
    node = profile or load_home_edge_profile()
    if command == "prepare_private_unlock_plan":
        return {
            "schema": "skeleton.home_edge.prepared_action.v1",
            "node_id": node.node_id,
            "status": "prepared_not_executed",
            "requires_private_secret_route": True,
            "operator_approval_required": True,
            "commands_to_prepare_privately": [
                "mmcli --modem 0 --pin-file <private-secret-file>",
                "mmcli --modem 0 --simple-connect='apn=internet,ip-type=ipv4v6'",
            ],
        }

    artifact = run_home_edge_diagnostic(profile=node, artifact_path=artifact_path)
    if command == "diagnostic":
        return artifact
    if command == "identity":
        return {
            "schema": "skeleton.home_edge.identity.v1",
            "node": artifact["node"],
            "controller": artifact["controller"],
            "summary": {
                "route": artifact["summary"]["route"],
                "tailscale": artifact["summary"]["tailscale"],
            },
        }
    if command == "tool_inventory":
        return {
            "schema": "skeleton.home_edge.tool_inventory.v1",
            "node_id": node.node_id,
            "tools": artifact["remote"].get("tools", {}),
        }
    return {
        "schema": "skeleton.home_edge.modem_diagnostic.v1",
        "node_id": node.node_id,
        "modem": artifact["summary"]["modem"],
        "huawei_diag_profile": artifact["summary"]["huawei_diag_profile"],
        "operator_report": build_operator_report(artifact),
    }


def compact_status(artifact: dict[str, Any]) -> dict[str, str]:
    summary = artifact.get("summary", {})
    modem = summary.get("modem", {}) if isinstance(summary, dict) else {}
    route = summary.get("route", {}) if isinstance(summary, dict) else {}
    tailscale = summary.get("tailscale", {}) if isinstance(summary, dict) else {}
    return {
        "node": str(artifact.get("node", {}).get("node_id", "home-edge-01")),
        "route_status": str(route.get("status", "unknown")),
        "tailscale_status": str(tailscale.get("status", "unknown")),
        "modem_status": str(modem.get("status", "unknown")),
        "modem_lock_state": str(modem.get("sim_lock_state", "unknown")).replace(" ", "_"),
        "connection_mode": str(modem.get("connection_mode", "unknown")),
    }


def dumps_public_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)

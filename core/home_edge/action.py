from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import tempfile
from typing import Any


HOME_EDGE_ACTION_SCHEMA = "skeleton.home_edge.action.v1"
HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE = "de_pc_read_only_v1"
HOME_EDGE_NODE_ID = "home-edge-01"

_ALLOWED_OPERATIONS = frozenset({"remote_read_only_diagnostic"})


class HomeEdgeActionError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def execute_home_edge_action(
    request: Mapping[str, Any],
    *,
    diagnostic_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, object]:
    """Execute a fixed, executor-owned Home Edge action profile."""

    normalized = _validate_request(request)
    if normalized["operation"] == "remote_read_only_diagnostic":
        return _remote_read_only_diagnostic(normalized, diagnostic_runner=diagnostic_runner)
    raise HomeEdgeActionError("UNKNOWN_HOME_EDGE_ACTION", "home-edge action is unknown")


def _validate_request(request: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(request, Mapping):
        raise HomeEdgeActionError(
            "INVALID_HOME_EDGE_ACTION_REQUEST",
            "home-edge action request must be an object",
        )
    allowed_keys = frozenset({"schema", "operation", "node_id", "probe_profile"})
    unknown = sorted(set(request) - allowed_keys)
    if unknown:
        raise HomeEdgeActionError(
            "UNKNOWN_HOME_EDGE_ACTION_FIELD",
            f"unknown home-edge action field: {unknown[0]}",
        )
    required = sorted(allowed_keys - set(request))
    if required:
        raise HomeEdgeActionError(
            "MISSING_HOME_EDGE_ACTION_FIELD",
            f"missing home-edge action field: {required[0]}",
        )
    if request.get("schema") != HOME_EDGE_ACTION_SCHEMA:
        raise HomeEdgeActionError(
            "INVALID_HOME_EDGE_ACTION_SCHEMA",
            "home-edge action schema is invalid",
        )
    operation = request.get("operation")
    if operation not in _ALLOWED_OPERATIONS:
        raise HomeEdgeActionError(
            "UNKNOWN_HOME_EDGE_ACTION",
            "home-edge action operation is not allowlisted",
        )
    if request.get("node_id") != HOME_EDGE_NODE_ID:
        raise HomeEdgeActionError(
            "INVALID_HOME_EDGE_NODE",
            "home-edge action node is not allowlisted",
        )
    if request.get("probe_profile") != HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE:
        raise HomeEdgeActionError(
            "INVALID_HOME_EDGE_PROBE_PROFILE",
            "home-edge action probe profile is not allowlisted",
        )
    return {
        "schema": HOME_EDGE_ACTION_SCHEMA,
        "operation": str(operation),
        "node_id": HOME_EDGE_NODE_ID,
        "probe_profile": HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE,
    }


def _remote_read_only_diagnostic(
    request: Mapping[str, str],
    *,
    diagnostic_runner: Callable[..., Mapping[str, Any]] | None,
) -> dict[str, object]:
    runner = diagnostic_runner
    if runner is None:
        from core.home_edge.diagnostics import run_audited_home_edge_command

        runner = run_audited_home_edge_command

    artifact = runner(
        "diagnostic",
        artifact_path=Path(tempfile.gettempdir())
        / "skeleton-home-edge"
        / "home-edge-01-diagnostic.latest.json",
    )
    return _diagnostic_receipt(request, artifact if isinstance(artifact, Mapping) else {})


def _diagnostic_receipt(
    request: Mapping[str, str], artifact: Mapping[str, Any]
) -> dict[str, object]:
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), Mapping) else {}
    gateway = summary.get("gateway") if isinstance(summary.get("gateway"), Mapping) else {}
    route = summary.get("route") if isinstance(summary.get("route"), Mapping) else {}
    tailscale = summary.get("tailscale") if isinstance(summary.get("tailscale"), Mapping) else {}
    modem = summary.get("modem") if isinstance(summary.get("modem"), Mapping) else {}
    registered = (
        modem.get("registered_expectation")
        if isinstance(modem.get("registered_expectation"), Mapping)
        else {}
    )
    gateway_status = str(gateway.get("status", "unverified"))
    route_status = str(route.get("status", "unverified"))
    tailscale_status = str(tailscale.get("status", "unverified"))
    modem_status = str(modem.get("status", "unverified"))
    ready = (
        gateway_status == "ready"
        and route_status == "unchanged"
        and tailscale_status == "healthy"
    )
    return {
        "schema": "skeleton.home_edge.read_only_diagnostic.receipt.v1",
        "operation": request["operation"],
        "node_id": request["node_id"],
        "probe_profile": request["probe_profile"],
        "status": "observed" if ready else "needs_operator",
        "reason_code": "healthy_transport" if ready else "transport_unverified",
        "aggregate_classes": ["gateway", "route", "tailscale", "modem"],
        "counts": {"diagnostic_count": 1},
        "booleans": {
            "usb_modem_health_required": False,
            "gateway_ready": gateway_status == "ready",
            "route_unchanged": route_status == "unchanged",
            "tailscale_healthy": tailscale_status == "healthy",
        },
        "gateway_status": gateway_status,
        "route_status": route_status,
        "tailscale_status": tailscale_status,
        "modem_status": modem_status,
        "internet_path_expectation": str(
            registered.get("internet_path", "default_gateway")
        ),
        "gateway_modem_internals": str(
            registered.get("gateway_modem_internals", "not_observed_by_home_edge")
        ),
    }

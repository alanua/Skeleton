from __future__ import annotations

import pytest

from core.home_edge.action import (
    HOME_EDGE_ACTION_SCHEMA,
    HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE,
    HomeEdgeActionError,
    execute_home_edge_action,
)


def _artifact() -> dict[str, object]:
    return {
        "summary": {
            "gateway": {"status": "ready"},
            "route": {"status": "unchanged"},
            "tailscale": {"status": "healthy"},
            "modem": {
                "status": "optional_not_attached",
                "registered_expectation": {
                    "internet_path": "default_gateway",
                    "gateway_modem_internals": "not_observed_by_home_edge",
                },
            },
        }
    }


def test_home_edge_action_runs_fixed_read_only_profile() -> None:
    calls: list[tuple[object, object]] = []

    def runner(command: str, *, artifact_path: object) -> dict[str, object]:
        calls.append((command, artifact_path))
        return _artifact()

    receipt = execute_home_edge_action(
        {
            "schema": HOME_EDGE_ACTION_SCHEMA,
            "operation": "remote_read_only_diagnostic",
            "node_id": "home-edge-01",
            "probe_profile": HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE,
        },
        diagnostic_runner=runner,
    )

    assert calls[0][0] == "diagnostic"
    assert receipt["status"] == "observed"
    assert receipt["reason_code"] == "healthy_transport"
    assert receipt["aggregate_classes"] == ["gateway", "route", "tailscale", "modem"]
    assert receipt["booleans"]["usb_modem_health_required"] is False


def test_home_edge_action_rejects_extra_behavior_fields_before_probe() -> None:
    def runner(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("probe must not run")

    with pytest.raises(HomeEdgeActionError) as exc_info:
        execute_home_edge_action(
            {
                "schema": HOME_EDGE_ACTION_SCHEMA,
                "operation": "remote_read_only_diagnostic",
                "node_id": "home-edge-01",
                "probe_profile": HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE,
                "command": "uname -a",
            },
            diagnostic_runner=runner,
        )

    assert exc_info.value.reason_code == "UNKNOWN_HOME_EDGE_ACTION_FIELD"

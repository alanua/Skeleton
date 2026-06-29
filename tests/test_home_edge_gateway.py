from __future__ import annotations

import pytest

from core.home_edge.gateway import AUDITED_GATEWAY_COMMANDS, gateway_contract, prepared_runtime_bootstrap
from core.home_edge.remote import HomeEdgeRemoteError, run_audited_home_edge_command


def test_gateway_is_universal_by_typed_capability_not_raw_shell() -> None:
    contract = gateway_contract()

    assert contract["task_model"] == "typed_allowlisted_actions"
    assert contract["raw_shell_from_issue_payload"] == "forbidden"
    assert contract["external_connection_fields_from_issue_payload"] == "forbidden"
    assert {
        "system",
        "network",
        "services",
        "containers",
        "media",
        "browser",
        "hardware",
        "home_automation",
    }.issubset(set(contract["domains"]))
    assert "browser_diagnostic" in AUDITED_GATEWAY_COMMANDS


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(HomeEdgeRemoteError):
        run_audited_home_edge_command("unregistered-action")


def test_runtime_bootstrap_is_prepared_without_runtime_material() -> None:
    plan = prepared_runtime_bootstrap()

    assert plan["status"] == "prepared_not_executed"
    assert plan["operator_approval_required"] is True
    assert plan["public_artifact_contains_secret_material"] is False

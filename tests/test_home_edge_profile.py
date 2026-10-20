from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.home_edge.profile import (
    HOME_EDGE_AUDIT_PERSIST_OPERATION,
    HOME_EDGE_ROOT_AUDIT_REMEDIATION_OPERATION,
    load_home_edge_profile,
    synthetic_profile_mapping,
)


def test_home_edge_profile_registers_universal_fixed_node() -> None:
    profile = load_home_edge_profile()
    registry = synthetic_profile_mapping()
    registry_file = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "home_edge" / "home-edge-01.json").read_text(
            encoding="utf-8"
        )
    )

    assert profile.node_id == "home-edge-01"
    assert profile.hostname == "synthetic-home-edge"
    assert profile.tailscale_ip == "100.64.0.10"
    assert profile.target_user == "home-edge-runner"
    assert profile.transport == "openssh_over_tailscale_ip"
    assert profile.identity_env == "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE"
    assert profile.known_hosts_env == "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE"
    assert profile.task_model == "typed_allowlisted_actions"
    assert "browser_and_desktop_diagnostics" in profile.capabilities
    assert "home_automation" in profile.capabilities
    assert profile.primary_network["interface"] == "synthetic-lan"
    assert profile.is_template_identity
    assert registry["operations"] == [
        HOME_EDGE_AUDIT_PERSIST_OPERATION,
        HOME_EDGE_ROOT_AUDIT_REMEDIATION_OPERATION,
    ]
    operation = registry["operations"][0]
    assert operation["operation_id"] == "home_edge_audit_persist_v1"
    assert operation["device_id"] == "home_edge_01"
    assert operation["execution_node"] == "home-edge-01"
    assert operation["run_as"] == "desktop-user"
    assert operation["risk"] == "yellow"
    assert operation["approval_gate"] == "operator_approval_required"
    assert operation["last_success"] is None
    assert operation["last_failure"] is None
    remediation = registry["operations"][1]
    assert remediation["operation_id"] == "home_edge_root_audit_remediation_20260729_v1"
    assert remediation["risk"] == "high_staged"
    assert remediation["approval_gate"] == "per_mutation_operator_approval_required"
    assert "physical_brother_scan_to_pc_succeeds" in remediation["independent_verification"]
    assert (
        remediation["rollback"]["router_firmware_storage_destructive"]
        == "deferred_without_separate_approval"
    )
    assert registry_file["operations"] == [
        HOME_EDGE_AUDIT_PERSIST_OPERATION,
        HOME_EDGE_ROOT_AUDIT_REMEDIATION_OPERATION,
    ]


def test_profile_rejects_changed_target_identity(tmp_path: Path) -> None:
    data = synthetic_profile_mapping()
    data["ssh"]["transport"] = "raw_shell"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="fixed contract mismatch"):
        load_home_edge_profile(path)


def test_local_profile_file_loads_runtime_identity(tmp_path: Path) -> None:
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["controller"]["host"] = "runtime-controller"
    data["controller"]["tailscale_ip"] = "100.64.10.63"
    data["ssh"]["target_user"] = "runtime-user"
    data["primary_network"] = {"interface": "test-lan0", "gateway": "192.0.2.254"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    profile = load_home_edge_profile(path)

    assert profile.hostname == "runtime-host"
    assert profile.tailscale_ip == "100.64.10.74"
    assert profile.target_user == "runtime-user"
    assert not profile.is_template_identity


def test_environment_overrides_create_runtime_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "runtime-host")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_TAILSCALE_IP", "100.64.10.74")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_TARGET_USER", "runtime-user")

    profile = load_home_edge_profile()

    assert profile.hostname == "runtime-host"
    assert profile.tailscale_ip == "100.64.10.74"
    assert profile.target_user == "runtime-user"
    assert not profile.is_template_identity

from __future__ import annotations

import json

from core.home_edge.root_audit_remediation import (
    MAX_BROTHER_SOCKET_FDS,
    build_root_audit_remediation_plan,
    classify_device_observation,
    map_listener_owner,
    validate_registry_mutation_permissions,
)


def audit_packet() -> dict[str, object]:
    return {
        "audit_id": "home-device-root-audit-20260729T220114Z",
        "audit_receipt": "a04f8ab09ac6082df86da20341a1da595ded094468ed60608bfb9197a5bf2ed7",
        "confirmed_findings": [
            {"severity": "critical", "component": "Brother Scan Key"},
            {"severity": "high", "component": "network exposure/firewall"},
            {"severity": "medium", "component": "WLED fleet"},
        ],
    }


def test_root_audit_plan_encodes_brother_watchdog_and_physical_scan_verification() -> None:
    plan = build_root_audit_remediation_plan(audit_packet())
    brother = _operation(plan, "brother_scan_key_fd_leak_guard_v1")

    assert plan["schema"] == "skeleton.home_edge.root_audit_remediation_plan.v1"
    assert plan["safety"]["executor"] == "canonical_home_edge_exec"
    assert brother["watchdog"]["restart_threshold"] == MAX_BROTHER_SOCKET_FDS
    assert "socket_fd_count_below_100_for_two_refresh_cycles" in brother["verification"]
    assert "panel_scan_to_pc_physical_scan_succeeds" in brother["verification"]
    assert all(request["requires_runtime_signature"] for request in brother["executor_requests"])


def test_firewall_canary_has_rollback_and_required_connectivity_probes() -> None:
    firewall = _operation(build_root_audit_remediation_plan(audit_packet()), "least_privilege_firewall_canary_v1")

    assert firewall["lane"] == "privileged_mutation"
    assert firewall["approval_gate"] == "separate_operator_approval_required"
    assert firewall["canary"]["automatic_rollback"] is True
    assert set(firewall["canary"]["probe_paths"]) == {
        "ssh",
        "tailscale",
        "skeleton_home",
        "brother_scan_print",
        "wled",
        "runner",
    }
    assert "restore_ruleset_backup_on_probe_failure_or_timeout" in firewall["rollback"]


def test_router_and_firmware_mutations_are_deferred_until_separate_approval() -> None:
    plan = build_root_audit_remediation_plan(audit_packet())
    router = _operation(plan, "asus_gateway_least_privilege_review_v1")
    wled = _operation(plan, "wled_fleet_time_wifi_inventory_v1")
    rendered = json.dumps(plan, sort_keys=True)

    assert router["status"] == "deferred_requires_separate_approval"
    assert "router_mutation" in router["blocked_without_approval"]
    assert wled["status"] == "partial_deferred_requires_separate_approval"
    assert "firmware_update" in wled["blocked_without_approval"]
    assert "factory_reset" in rendered


def test_registry_permission_regression_check_preserves_owner_group_and_mode() -> None:
    before = {"uid": 1000, "gid": 1000, "mode": "0640"}

    assert validate_registry_mutation_permissions(before=before, after=dict(before))["status"] == "preserved"

    changed = validate_registry_mutation_permissions(
        before=before,
        after={"uid": 0, "gid": 1000, "mode": "0640"},
    )

    assert changed == {
        "status": "permission_regression",
        "changed_fields": ["uid"],
        "before": before,
        "after": {"uid": 0, "gid": 1000, "mode": "0640"},
    }


def test_expected_inactive_mobile_absent_and_fault_classification_are_distinct() -> None:
    waydroid = classify_device_observation(
        {"device_id": "waydroid", "expected_state": "inactive", "observed_state": "unreachable"}
    )
    iphone = classify_device_observation(
        {"device_id": "iphone", "expected_state": "mobile_absent", "observed_state": "unreachable"}
    )
    nas = classify_device_observation(
        {"device_id": "nas", "expected_state": "active", "observed_state": "unreachable", "serial": "serial-1"}
    )

    assert waydroid["status"] == "expected_inactive"
    assert waydroid["fault"] is False
    assert iphone["status"] == "expected_absent"
    assert iphone["fault"] is False
    assert nas["status"] == "actual_fault"
    assert nas["fault"] is True


def test_stale_tailscale_identity_reconciliation_does_not_use_ip_only_identity() -> None:
    redmi = classify_device_observation(
        {
            "device_id": "redmi",
            "expected_state": "active",
            "observed_state": "online",
            "tailscale_ip": "100.64.1.2",
            "tailscale_nodes": [
                {"node_id": "old-node", "state": "stale", "tailscale_ip": "100.64.1.2"},
                {"node_id": "new-node", "state": "online", "tailscale_ip": "100.64.1.3"},
            ],
        }
    )
    ip_only = classify_device_observation(
        {"expected_state": "active", "observed_state": "online", "tailscale_ip": "100.64.1.2"}
    )

    assert redmi["status"] == "identity_reconciliation_required"
    assert redmi["identity"]["ip_only"] is False
    assert redmi["stale_tailscale_identity_count"] == 1
    assert ip_only["identity"]["status"] == "insufficient_identity"
    assert ip_only["identity"]["ip_only"] is True


def test_listener_owner_mapping_distinguishes_known_and_unregistered_ports() -> None:
    assert map_listener_owner({"port": 8099}) == {
        "status": "registered",
        "port": 8099,
        "owner": "skeleton_home",
        "scope": "tailscale_or_lan_controller",
        "justification": "Skeleton Home UI/API",
        "required": True,
    }
    assert map_listener_owner({"port": 65535}) == {
        "status": "unregistered_listener",
        "port": 65535,
        "owner": None,
        "scope": "unknown",
        "justification": "requires operator mapping before firewall allow",
    }


def _operation(plan: dict[str, object], operation_id: str) -> dict[str, object]:
    operations = plan["operations"]
    assert isinstance(operations, list)
    for operation in operations:
        assert isinstance(operation, dict)
        if operation.get("operation_id") == operation_id:
            return operation
    raise AssertionError(f"missing operation {operation_id}")

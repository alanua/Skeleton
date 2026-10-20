from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.home_edge.executor import DEFAULT_NODE_ID


ROOT_AUDIT_REMEDIATION_SCHEMA = "skeleton.home_edge.root_audit_remediation_plan.v1"
ROOT_AUDIT_REMEDIATION_OPERATION_ID = "home_edge_root_audit_remediation_20260729_v1"
MAX_BROTHER_SOCKET_FDS = 100

_APPROVAL_REQUIRED = "separate_operator_approval_required"


@dataclass(frozen=True)
class ServiceOwner:
    owner: str
    scope: str
    justification: str
    required: bool = True

    def to_mapping(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "scope": self.scope,
            "justification": self.justification,
            "required": self.required,
        }


KNOWN_LISTENER_OWNERS: dict[int, ServiceOwner] = {
    22: ServiceOwner("ssh_recovery", "tailscale_and_lan_admin", "recovery access"),
    139: ServiceOwner("samba_family_lan", "lan_only", "family file sharing"),
    445: ServiceOwner("samba_family_lan", "lan_only", "family file sharing"),
    5201: ServiceOwner("iperf3_diagnostic", "disabled_by_default", "on-demand throughput testing", required=False),
    8099: ServiceOwner("skeleton_home", "tailscale_or_lan_controller", "Skeleton Home UI/API"),
    8100: ServiceOwner("skeleton_home", "tailscale_or_lan_controller", "Skeleton Home UI/API"),
    8101: ServiceOwner("skeleton_home", "tailscale_or_lan_controller", "Skeleton Home UI/API"),
    8878: ServiceOwner("skeleton_home", "tailscale_or_lan_controller", "Skeleton Home UI/API"),
    19400: ServiceOwner("hyperhdr", "lan_media_control", "HyperHDR control path"),
    19444: ServiceOwner("hyperhdr", "lan_media_control", "HyperHDR JSON API"),
    19445: ServiceOwner("hyperhdr", "lan_media_control", "HyperHDR flatbuffer API"),
}


def build_root_audit_remediation_plan(audit: Mapping[str, Any]) -> dict[str, object]:
    """Build a staged, approval-aware remediation plan from a private root audit."""

    finding_keys = _finding_keys(audit.get("confirmed_findings"))
    operations = [
        _brother_operation(),
        _registry_refresh_operation(),
        _tailscale_dns_operation(),
        _portal_hyperhdr_operation(),
        _listener_mapping_operation(),
        _firewall_canary_operation(),
        _smart_monitoring_operation(),
        _router_deferred_operation(),
        _wled_deferred_operation(),
    ]
    status = "ready_for_runner" if finding_keys else "empty_audit"
    return {
        "schema": ROOT_AUDIT_REMEDIATION_SCHEMA,
        "operation_id": ROOT_AUDIT_REMEDIATION_OPERATION_ID,
        "audit_id": _string_or_none(audit.get("audit_id")),
        "audit_receipt": _string_or_none(audit.get("audit_receipt")),
        "node_id": DEFAULT_NODE_ID,
        "status": status,
        "safety": {
            "executor": "canonical_home_edge_exec",
            "backup_before_mutation": True,
            "no_router_mutation": True,
            "no_firmware_mutation": True,
            "no_destructive_storage_action": True,
            "device_identity_policy": "stable_identity_required_not_ip_only",
        },
        "phases": [
            "repair_brother_and_registry_refresh",
            "map_services_and_repair_portal_tailscale_dns",
            "stage_firewall_canary_with_rollback",
            "defer_router_and_firmware_until_separate_approval",
            "rerun_full_root_audit_and_compare_evidence",
        ],
        "finding_keys": finding_keys,
        "operations": operations,
        "final_verification": [
            "brother_socket_fd_count_below_100_for_two_refresh_cycles",
            "physical_brother_scan_to_pc_succeeds",
            "device_refresh_timer_success_and_registry_doctor_pass",
            "portal_wlr_active_and_hyperhdr_systemgrabber_active_after_mode_transition",
            "tailscale_magicdns_public_dns_and_direct_runner_connection_classified",
            "all_external_listeners_have_registered_owner_and_scope",
            "firewall_canary_preserves_ssh_tailscale_skeleton_brother_wled_runner_paths",
            "smart_short_test_timer_installed_and_non_destructive_probe_verified",
        ],
    }


def classify_device_observation(device: Mapping[str, Any]) -> dict[str, object]:
    expected_state = _string_or_none(device.get("expected_state")) or "active"
    observed_state = _string_or_none(device.get("observed_state")) or "unreachable"
    identity = stable_device_identity(device)
    tailscale = list(_mapping_sequence(device.get("tailscale_nodes")))
    stale_nodes = [node for node in tailscale if _string_or_none(node.get("state")) == "stale"]
    online_nodes = [node for node in tailscale if _string_or_none(node.get("state")) == "online"]

    if expected_state in {"inactive", "powered_off"}:
        status = "expected_inactive"
        fault = False
    elif expected_state in {"sleeping", "mobile_absent"}:
        status = "expected_absent"
        fault = False
    elif observed_state in {"online", "reachable"}:
        status = "healthy"
        fault = False
    else:
        status = "actual_fault"
        fault = True
    if stale_nodes and online_nodes:
        status = "identity_reconciliation_required" if not fault else status
    return {
        "device_id": _string_or_none(device.get("device_id")),
        "status": status,
        "fault": fault,
        "identity": identity,
        "stale_tailscale_identity_count": len(stale_nodes),
        "online_tailscale_identity_count": len(online_nodes),
        "reason": _classification_reason(status),
    }


def stable_device_identity(device: Mapping[str, Any]) -> dict[str, object]:
    stable_keys = {
        key: value
        for key, value in {
            "device_id": device.get("device_id"),
            "serial": device.get("serial"),
            "tailscale_node_id": device.get("tailscale_node_id"),
            "tailscale_machine_key": device.get("tailscale_machine_key"),
            "mac": device.get("mac"),
            "hostname": device.get("hostname"),
        }.items()
        if isinstance(value, str) and value.strip()
    }
    ip_values = [value for value in (device.get("ip"), device.get("tailscale_ip")) if isinstance(value, str) and value.strip()]
    return {
        "status": "stable" if stable_keys else "insufficient_identity",
        "stable_keys": dict(sorted(stable_keys.items())),
        "ip_observed": bool(ip_values),
        "ip_only": bool(ip_values) and not stable_keys,
    }


def validate_registry_mutation_permissions(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, object]:
    tracked = ("uid", "gid", "mode")
    changed = [key for key in tracked if before.get(key) != after.get(key)]
    return {
        "status": "preserved" if not changed else "permission_regression",
        "changed_fields": changed,
        "before": {key: before.get(key) for key in tracked},
        "after": {key: after.get(key) for key in tracked},
    }


def map_listener_owner(listener: Mapping[str, Any]) -> dict[str, object]:
    port = _int_or_none(listener.get("port"))
    owner = KNOWN_LISTENER_OWNERS.get(port or -1)
    if owner is None:
        return {
            "status": "unregistered_listener",
            "port": port,
            "owner": None,
            "scope": "unknown",
            "justification": "requires operator mapping before firewall allow",
        }
    payload = owner.to_mapping()
    payload.update({"status": "registered", "port": port})
    return payload


def _brother_operation() -> dict[str, object]:
    return {
        "operation_id": "brother_scan_key_fd_leak_guard_v1",
        "component": "Brother Scan Key",
        "lane": "privileged_mutation",
        "approval_gate": _APPROVAL_REQUIRED,
        "preconditions": [
            "confirm_no_active_scan_job",
            "backup_systemd_override_and_brother_scan_config",
            "record_baseline_brscan_skey_fd_counts",
        ],
        "executor_requests": [
            _script_request(
                "read_only",
                "desktop-user",
                "pgrep -x brscan-skey-exe >/dev/null && ls -l /proc/$(pgrep -x brscan-skey-exe | head -n1)/fd | awk '{print $NF}' | grep -c '^socket:' || true",
                timeout=15,
            ),
            _script_request(
                "privileged_mutation",
                "root",
                "systemctl restart brscan-skey.service || systemctl --user restart brscan-skey.service",
                timeout=60,
            ),
        ],
        "watchdog": {
            "metric": "brscan_skey_socket_fd_count",
            "restart_threshold": MAX_BROTHER_SOCKET_FDS,
            "sample_window": "two_refresh_cycles",
            "action": "bounded_service_restart",
        },
        "verification": [
            "socket_fd_count_below_100_for_two_refresh_cycles",
            "panel_scan_to_pc_physical_scan_succeeds",
        ],
        "rollback": ["restore_systemd_override_backup", "restore_brother_scan_config_backup"],
    }


def _registry_refresh_operation() -> dict[str, object]:
    return {
        "operation_id": "device_registry_refresh_repair_v1",
        "component": "device registry refresh",
        "lane": "routine_mutation",
        "approval_gate": _APPROVAL_REQUIRED,
        "preconditions": ["backup_device_registry", "capture_registry_uid_gid_mode"],
        "verification": [
            "device_refresh_timer_completes_successfully",
            "registry_doctor_passes",
            "registry_uid_gid_mode_preserved",
        ],
        "rollback": ["restore_device_registry_backup"],
    }


def _tailscale_dns_operation() -> dict[str, object]:
    return {
        "operation_id": "tailscale_dns_derp_diagnostic_v1",
        "component": "Tailscale DNS/connectivity",
        "lane": "read_only",
        "approval_gate": "none",
        "probes": ["tailscale_status_json", "tailscale_netcheck", "resolvectl_status", "magicdns_lookup", "public_dns_lookup"],
        "verification": ["magicdns_classified", "public_dns_works", "direct_runner_connection_preserved"],
        "rollback": ["no_mutation"],
    }


def _portal_hyperhdr_operation() -> dict[str, object]:
    return {
        "operation_id": "portal_wlr_hyperhdr_capture_repair_v1",
        "component": "xdg-desktop-portal-wlr / HyperHDR capture",
        "lane": "routine_mutation",
        "approval_gate": _APPROVAL_REQUIRED,
        "preconditions": ["capture_desktop_session_environment", "backup_user_systemd_overrides", "do_not_interrupt_active_playback"],
        "verification": ["portal_wlr_active", "hyperhdr_systemgrabber_active", "wled_output_active_after_mode_transition"],
        "rollback": ["restore_user_systemd_overrides", "restart_previous_user_services"],
    }


def _listener_mapping_operation() -> dict[str, object]:
    return {
        "operation_id": "home_edge_listener_owner_mapping_v1",
        "component": "always-on services",
        "lane": "read_only",
        "approval_gate": "none",
        "known_owner_ports": sorted(KNOWN_LISTENER_OWNERS),
        "verification": ["every_external_listener_registered_or_blocked_from_firewall_allowlist"],
        "rollback": ["no_mutation"],
    }


def _firewall_canary_operation() -> dict[str, object]:
    return {
        "operation_id": "least_privilege_firewall_canary_v1",
        "component": "network exposure/firewall",
        "lane": "privileged_mutation",
        "approval_gate": _APPROVAL_REQUIRED,
        "preconditions": ["backup_current_ruleset", "map_listener_owners", "confirm_runner_recovery_path"],
        "canary": {
            "automatic_rollback": True,
            "probe_paths": ["ssh", "tailscale", "skeleton_home", "brother_scan_print", "wled", "runner"],
        },
        "verification": ["canary_probes_pass", "broad_input_accept_replaced_by_least_privilege_policy"],
        "rollback": ["restore_ruleset_backup_on_probe_failure_or_timeout"],
    }


def _smart_monitoring_operation() -> dict[str, object]:
    return {
        "operation_id": "ssd_smart_bounded_monitoring_v1",
        "component": "SSD health monitoring",
        "lane": "privileged_mutation",
        "approval_gate": _APPROVAL_REQUIRED,
        "preconditions": ["backup_smartd_and_timer_config", "confirm_no_destructive_disk_action"],
        "schedule": {"short_test": "daily_bounded", "extended_test": "periodic_off_hours"},
        "verification": ["smartctl_capability_probe", "short_test_timer_enabled", "alert_path_configured"],
        "rollback": ["restore_smartd_and_timer_config_backup"],
    }


def _router_deferred_operation() -> dict[str, object]:
    return {
        "operation_id": "asus_gateway_least_privilege_review_v1",
        "component": "ASUS gateway",
        "status": "deferred_requires_separate_approval",
        "allowed_now": ["read_only_port_mapping_audit", "dependency_report"],
        "blocked_without_approval": ["router_mutation", "upnp_disable", "nat_pmp_disable", "pcp_disable"],
    }


def _wled_deferred_operation() -> dict[str, object]:
    return {
        "operation_id": "wled_fleet_time_wifi_inventory_v1",
        "component": "WLED fleet",
        "status": "partial_deferred_requires_separate_approval",
        "allowed_now": ["read_only_inventory", "ntp_time_repair_if_non_firmware_and_approved"],
        "blocked_without_approval": ["firmware_update", "factory_reset"],
    }


def _script_request(lane: str, run_as: str, script: str, *, timeout: int) -> dict[str, object]:
    return {
        "schema": "skeleton.home_edge.exec_request.template.v1",
        "node_id": DEFAULT_NODE_ID,
        "execution_lane": lane,
        "run_as": run_as,
        "mode": "script",
        "script_interpreter": "bash",
        "script": script,
        "timeout_seconds": timeout,
        "requires_runtime_signature": True,
    }


def _finding_keys(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    keys: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        component = _string_or_none(item.get("component"))
        if component:
            keys.append(component)
    return sorted(set(keys))


def _mapping_sequence(value: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _classification_reason(status: str) -> str:
    return {
        "expected_inactive": "registered_expected_inactive_not_fault",
        "expected_absent": "sleeping_or_mobile_absent_not_fault",
        "healthy": "observed_reachable",
        "identity_reconciliation_required": "stale_and_current_tailscale_identities_need_stable_key_reconciliation",
    }.get(status, "unexpected_unreachable_requires_repair")


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def dumps_plan(plan: Mapping[str, Any]) -> str:
    return json.dumps(plan, indent=2, sort_keys=True)

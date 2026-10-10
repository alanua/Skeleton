from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "skeleton.family_dns.rollout_policy.v1"
PREFLIGHT_SCHEMA = "skeleton.family_dns.preflight.v1"
HOME_EDGE_NODE_ID = "home-edge-01"
HOME_EDGE_SERVER_ARGV = ("/usr/local/bin/home_edge_exec", "--server")
APPROVED_PRIVATE_DNS_SERVER_ROLE = "home_edge_secondary_adguard_tailnet"
PUBLIC_DNS_BINDING_FORBIDDEN = ("0.0.0.0", "::", "wan", "public_interface")


class Outcome(StrEnum):
    NOT_SENT = "not_sent"
    SENT = "sent"
    ACCEPTED = "accepted"
    APPLIED = "applied"
    PHYSICALLY_VERIFIED = "physically_verified"
    OBSERVED = "observed"
    BLOCKED = "blocked"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCKER = "blocker"


@dataclass(frozen=True)
class ClientEvidence:
    role: str
    enrolled_tailnet: bool = False
    consent_recorded: bool = False
    home_reachability_verified: bool = False
    away_reachability_verified: bool = False
    resolver_attested_independently: bool = False
    tailscale_dns_verified: bool = False
    local_dns_exception: bool = False
    ipv6_router_advertisement_bypass: bool = False
    doh_dot_bypass_possible: bool = False
    captive_portal_risk: bool = False


@dataclass(frozen=True)
class LanEvidence:
    primary_adguard_verified: bool = False
    secondary_adguard_verified: bool = False
    router_dhcp_dns_verified: bool = False
    outbound_dns_enforcement_verified: bool = False
    ipv6_ra_rdnss_verified: bool = False
    failover_verified: bool = False
    all_client_coverage_verified: bool = False
    local_static_or_manual_dns_exceptions: tuple[str, ...] = ()
    public_doh_dot_app_bypass_possible: bool = True
    private_doh_dot_app_bypass_possible: bool = True
    captive_portal_failure_cases: bool = True


def build_rollout_policy(
    *,
    lan: LanEvidence | None = None,
    clients: Sequence[ClientEvidence] = (),
    generated_at: str | None = None,
) -> dict[str, Any]:
    lan = lan or LanEvidence()
    stage_a = _stage_a(lan, clients)
    stage_b = _stage_b(stage_a)
    stage_c = _stage_c(stage_b, clients)
    stage_d = _stage_d(stage_c, clients, lan)
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "privacy": "PUBLIC_SAFE_DEVICE_ROLE_SUMMARIES_ONLY",
        "canonical_executor": {
            "node_id": HOME_EDGE_NODE_ID,
            "server_argv": list(HOME_EDGE_SERVER_ARGV),
            "lanes": ["read_only", "privileged_mutation"],
            "requires": [
                "request_id",
                "idempotency_key",
                "operator_approval_ref_for_mutation",
                "audit_receipt",
                "registry_record",
                "state_db_record",
                "memorygate_record",
            ],
            "forbids": [
                "root_gate_override",
                "raw_shell_control_plane",
                "new_dns_service",
                "parallel_resolver",
                "0.0.0.0_binding",
                "wan_exposure",
            ],
        },
        "expectations": _expectations(),
        "stages": [stage_a, stage_b, stage_c, stage_d],
    }


def build_preflight_packet(
    *,
    lan: LanEvidence | None = None,
    clients: Sequence[ClientEvidence] = (),
    request_prefix: str = "family-dns-stage-a",
    generated_at: str | None = None,
) -> dict[str, Any]:
    policy = build_rollout_policy(lan=lan, clients=clients, generated_at=generated_at)
    return {
        "schema": PREFLIGHT_SCHEMA,
        "generated_at": policy["generated_at"],
        "mode": "read_only_nonmutating",
        "node_id": HOME_EDGE_NODE_ID,
        "mutation_allowed": False,
        "runtime_switch_allowed": False,
        "preflight_requests": stage_a_preflight_requests(request_prefix=request_prefix),
        "policy": policy,
    }


def stage_a_preflight_requests(*, request_prefix: str = "family-dns-stage-a") -> list[dict[str, Any]]:
    checks = (
        (
            "home_edge_identity",
            ("/usr/bin/id", "-un"),
            "Verify canonical executor account and audit route only.",
        ),
        (
            "adguard_service_status",
            ("/usr/bin/systemctl", "is-active", "AdGuardHome.service"),
            "Observe existing AdGuard service without restart or config writes.",
        ),
        (
            "dns_listeners",
            ("/usr/bin/ss", "-H", "-luntp"),
            "Observe current DNS listeners and reject public/WAN exposure.",
        ),
        (
            "router_and_default_route",
            ("/usr/sbin/ip", "-json", "route", "show", "default"),
            "Observe existing router/failover path.",
        ),
        (
            "ipv6_addresses_and_ra_path",
            ("/usr/sbin/ip", "-json", "-6", "addr", "show"),
            "Observe IPv6 path; RA/RDNSS bypass is not assumed absent.",
        ),
        (
            "resolved_dns",
            ("/usr/bin/resolvectl", "dns"),
            "Observe local resolver inputs and manual/static DNS exceptions.",
        ),
        (
            "tailscale_status",
            ("/usr/bin/tailscale", "status", "--json"),
            "Verify current tailnet clients and DNS posture rather than assuming it.",
        ),
    )
    return [
        _exec_request(
            request_id=f"{request_prefix}-{index:02d}-{check_id}",
            idempotency_key=f"{request_prefix}-{check_id}",
            lane="read_only",
            argv=argv,
            purpose=purpose,
        )
        for index, (check_id, argv, purpose) in enumerate(checks, start=1)
    ]


def mutation_proposals() -> list[dict[str, Any]]:
    return [_stage_b_mutation(), _stage_c_mutation()]


def policy_blockers(policy: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for stage in policy.get("stages", []):
        if not isinstance(stage, Mapping):
            continue
        for gate in stage.get("gates", []):
            if isinstance(gate, Mapping) and gate.get("severity") == Severity.BLOCKER.value:
                blockers.append(str(gate.get("id")))
    return blockers


def public_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _stage_a(lan: LanEvidence, clients: Sequence[ClientEvidence]) -> dict[str, Any]:
    gates = [
        _gate("lan_primary_adguard", lan.primary_adguard_verified, "LAN primary AdGuard observed."),
        _gate("lan_secondary_adguard", lan.secondary_adguard_verified, "LAN secondary AdGuard observed."),
        _gate("router_dhcp_dns", lan.router_dhcp_dns_verified, "Router DHCP DNS settings observed."),
        _gate("outbound_dns_enforcement", lan.outbound_dns_enforcement_verified, "Outbound port 53 enforcement observed."),
        _gate("ipv6_ra_rdnss", lan.ipv6_ra_rdnss_verified, "IPv6 RA/RDNSS path observed; bypass is otherwise open."),
        _gate("router_failover", lan.failover_verified, "Existing router/failover behavior observed."),
        _gate("all_lan_clients", lan.all_client_coverage_verified, "All LAN client roles accounted for."),
    ]
    for role in lan.local_static_or_manual_dns_exceptions:
        gates.append(_gate(f"local_dns_exception_{role}", False, "Local static/manual DNS exception must be handled per client."))
    return {
        "stage": "A",
        "name": "read_only_lan_and_family_preflight",
        "outcome": Outcome.NOT_SENT.value,
        "mutation_allowed": False,
        "gates": gates + _client_gates(clients),
        "requests": stage_a_preflight_requests(),
    }


def _stage_b(stage_a: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stage": "B",
        "name": "proposed_tailnet_only_secondary_adguard_bind",
        "outcome": Outcome.NOT_SENT.value,
        "mutation_allowed": False,
        "blocked_until": ["stage_A_all_blockers_clear", "operator_approval", "registered_control_available"],
        "proposal": _stage_b_mutation(),
        "rollback_required_if": [
            "tailnet_listener_probe_fails",
            "normal_lan_dns_probe_fails",
            "public_or_wan_listener_detected",
            "service_order_or_restart_guard_fails",
        ],
        "ready": not policy_blockers({"stages": [stage_a]}),
    }


def _stage_c(stage_b: Mapping[str, Any], clients: Sequence[ClientEvidence]) -> dict[str, Any]:
    return {
        "stage": "C",
        "name": "proposed_tailscale_global_nameserver",
        "outcome": Outcome.NOT_SENT.value,
        "mutation_allowed": False,
        "blocked_until": [
            "stage_B_tailnet_dns_physically_verified",
            "home_edge_dns_settings_verified",
            "redmi_dns_settings_verified",
            "windows_dns_settings_verified",
            "family_client_dns_settings_verified",
            "operator_approval",
        ],
        "proposal": _stage_c_mutation(),
        "gates": [
            _gate("all_client_reachability", _all(clients, "away_reachability_verified"), "Every enrolled client can reach the private DNS server."),
            _gate("independent_resolver_attestation", _all(clients, "resolver_attested_independently"), "Each client has independent resolver attestation."),
        ],
        "fallback": "rollback_tailscale_dns_to_previous_settings_if_magicdns_or_resolution_fails",
        "ready": stage_b.get("outcome") == Outcome.PHYSICALLY_VERIFIED.value,
    }


def _stage_d(stage_c: Mapping[str, Any], clients: Sequence[ClientEvidence], lan: LanEvidence) -> dict[str, Any]:
    return {
        "stage": "D",
        "name": "pilot_then_family_and_lan_observation",
        "outcome": Outcome.NOT_SENT.value,
        "mutation_allowed": False,
        "pilot_order": [
            "Redmi pilot with DNS left off until readiness gates pass",
            "Family Samsung/iPhone enrollment only with their own consent",
            "LAN role checks for TV, tablet, Home Assistant, printer, Nest, Windows and other observed roles",
        ],
        "gates": [
            _gate("redmi_ready_before_dns_enabled", _client_role_ready(clients, "redmi"), "Redmi is physically verified before DNS is enabled."),
            _gate("family_consent", _family_consent(clients), "Family devices require opt-in tailnet enrollment and consent."),
            _gate("lan_tail_checked", lan.all_client_coverage_verified, "LAN tail devices checked after pilot."),
        ],
        "truthful_limitations": _expectations()["limitations"],
        "ready": stage_c.get("outcome") == Outcome.PHYSICALLY_VERIFIED.value,
    }


def _stage_b_mutation() -> dict[str, Any]:
    return {
        "operation_id": "family_dns_bind_secondary_adguard_tailnet_v1",
        "execution_node": HOME_EDGE_NODE_ID,
        "lane": "privileged_mutation",
        "run_as": "root",
        "request_id": "family-dns-stage-b-bind-secondary-adguard",
        "idempotency_key": "family-dns-stage-b-bind-secondary-adguard-v1",
        "argv": [
            "/usr/local/sbin/skeleton-home-edge-adguard-tailnet-bind",
            "--bind-existing-secondary",
            "--address-role",
            APPROVED_PRIVATE_DNS_SERVER_ROLE,
            "--no-public-bind",
            "--backup",
            "--probe-client-role",
            "redmi",
            "--rollback-on-listener-or-dns-failure",
        ],
        "safeguards": [
            "backup_existing_adguard_config",
            "service_order_guard",
            "restart_only_existing_adguard_service",
            "live_probe_from_redmi",
            "normal_lan_dns_probe",
            "rollback_on_failed_probe",
        ],
        "forbidden_bindings": list(PUBLIC_DNS_BINDING_FORBIDDEN),
        "status": "design_only_not_applied",
    }


def _stage_c_mutation() -> dict[str, Any]:
    return {
        "operation_id": "family_dns_tailscale_global_nameserver_v1",
        "execution_node": HOME_EDGE_NODE_ID,
        "lane": "privileged_mutation",
        "run_as": "root",
        "request_id": "family-dns-stage-c-tailscale-dns",
        "idempotency_key": "family-dns-stage-c-tailscale-global-nameserver-v1",
        "argv": [
            "/usr/local/sbin/skeleton-approved-tailscale-dns-control",
            "--set-global-nameserver-role",
            APPROVED_PRIVATE_DNS_SERVER_ROLE,
            "--override-dns",
            "--verify-home-edge",
            "--verify-redmi",
            "--verify-windows",
            "--verify-family-clients",
            "--preserve-magicdns",
            "--rollback-on-failed-reachability",
        ],
        "requires_registered_approved_control": True,
        "status": "proposal_only_not_applied",
    }


def _expectations() -> dict[str, Any]:
    return {
        "preferred_home_path": "existing_plain_dns_to_lan_adguard",
        "preferred_away_path": "existing_encrypted_tailscale_tunnel_to_private_dns",
        "android_system_dot_certificate_required": False,
        "limitations": [
            "DNS-only controls cannot guarantee blocking ads in native YouTube.",
            "DNS-only controls cannot block ads or telemetry served from the same hostname as wanted content.",
            "IPv6 RA/RDNSS, local static DNS, manual DNS, captive portals and DoH/DoT apps must be reported honestly instead of hidden.",
            "No MITM or certificate installation is part of this rollout.",
        ],
        "outcome_model": [item.value for item in Outcome],
    }


def _client_gates(clients: Sequence[ClientEvidence]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for client in clients:
        gates.extend(
            [
                _gate(f"{client.role}_tailnet_opt_in", client.enrolled_tailnet and client.consent_recorded, "Family tailnet enrollment is opt-in."),
                _gate(f"{client.role}_home_reachability", client.home_reachability_verified, "Home reachability observed."),
                _gate(f"{client.role}_resolver_attestation", client.resolver_attested_independently, "Resolver independently attested."),
            ]
        )
        if client.local_dns_exception:
            gates.append(_gate(f"{client.role}_local_dns_exception", False, "Local DNS exception requires per-client handling."))
        if client.ipv6_router_advertisement_bypass:
            gates.append(_gate(f"{client.role}_ipv6_bypass", False, "IPv6 router advertisement bypass remains possible."))
        if client.doh_dot_bypass_possible:
            gates.append(_gate(f"{client.role}_doh_dot_bypass", False, "DoH/DoT app bypass remains possible."))
        if client.captive_portal_risk:
            gates.append(_gate(f"{client.role}_captive_portal", False, "Captive portal failure path needs fallback instructions."))
    return gates


def _exec_request(
    *,
    request_id: str,
    idempotency_key: str,
    lane: str,
    argv: Iterable[str],
    purpose: str,
) -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.exec_request.template.v1",
        "request_id": request_id,
        "node_id": HOME_EDGE_NODE_ID,
        "execution_lane": lane,
        "run_as": "desktop-user",
        "mode": "argv",
        "argv": list(argv),
        "timeout_seconds": 30,
        "idempotency_key": idempotency_key,
        "public": True,
        "purpose": purpose,
        "send_via": {
            "node_id": HOME_EDGE_NODE_ID,
            "argv": list(HOME_EDGE_SERVER_ARGV),
        },
        "outcomes": {
            "sent": False,
            "accepted": False,
            "applied": False,
            "physically_verified": False,
            "observed": False,
        },
    }


def _gate(identifier: str, passed: bool, description: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "passed": bool(passed),
        "severity": Severity.INFO.value if passed else Severity.BLOCKER.value,
        "description": description,
    }


def _all(clients: Sequence[ClientEvidence], field: str) -> bool:
    return bool(clients) and all(bool(getattr(client, field)) for client in clients)


def _client_role_ready(clients: Sequence[ClientEvidence], role: str) -> bool:
    return any(
        client.role == role
        and client.consent_recorded
        and client.home_reachability_verified
        and client.away_reachability_verified
        and client.resolver_attested_independently
        for client in clients
    )


def _family_consent(clients: Sequence[ClientEvidence]) -> bool:
    family_clients = [client for client in clients if client.role != "redmi"]
    return bool(family_clients) and all(client.consent_recorded and client.enrolled_tailnet for client in family_clients)

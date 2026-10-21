from __future__ import annotations

from core.family_dns_policy import (
    HOME_EDGE_NODE_ID,
    APPROVED_PRIVATE_DNS_SERVER_ROLE,
    ClientEvidence,
    ControlEvidence,
    LanEvidence,
    build_rollout_policy,
    mutation_proposals,
    policy_blockers,
    stage_a_preflight_requests,
)


def test_stage_a_models_lan_ipv6_exceptions_and_bypass_blockers() -> None:
    policy = build_rollout_policy(
        lan=LanEvidence(
            primary_adguard_verified=True,
            secondary_adguard_verified=True,
            router_dhcp_dns_verified=True,
            outbound_dns_enforcement_verified=False,
            ipv6_ra_rdnss_verified=False,
            failover_verified=True,
            all_client_coverage_verified=False,
            local_static_or_manual_dns_exceptions=("printer",),
        ),
        clients=(
            ClientEvidence(
                role="redmi",
                enrolled_tailnet=True,
                consent_recorded=True,
                home_reachability_verified=True,
                resolver_attested_independently=False,
                doh_dot_bypass_possible=True,
                captive_portal_risk=True,
            ),
        ),
        generated_at="2026-10-10T00:00:00+00:00",
    )

    assert policy["schema"] == "skeleton.family_dns.rollout_policy.v1"
    assert policy["canonical_executor"]["node_id"] == HOME_EDGE_NODE_ID
    assert "root_gate_override" in policy["canonical_executor"]["forbids"]
    assert "0.0.0.0_binding" in policy["canonical_executor"]["forbids"]
    blockers = policy_blockers(policy)
    assert "outbound_dns_enforcement" in blockers
    assert "ipv6_ra_rdnss" in blockers
    assert "all_lan_clients" in blockers
    assert "local_dns_exception_printer" in blockers
    assert "redmi_resolver_attestation" in blockers
    assert "redmi_doh_dot_bypass" in blockers
    assert "redmi_captive_portal" in blockers


def test_preflight_requests_are_read_only_public_home_edge_exec_templates() -> None:
    requests = stage_a_preflight_requests(request_prefix="unit")

    assert requests
    assert {request["node_id"] for request in requests} == {HOME_EDGE_NODE_ID}
    assert {request["execution_lane"] for request in requests} == {"read_only"}
    assert {request["run_as"] for request in requests} == {"desktop-user"}
    assert all(request["public"] is True for request in requests)
    assert all(request["send_via"]["argv"] == ["/usr/local/bin/home_edge_exec", "--server"] for request in requests)
    assert any(request["argv"] == ["/usr/bin/tailscale", "status", "--json"] for request in requests)
    assert any(request["argv"] == ["/usr/bin/ss", "-H", "-luntp"] for request in requests)
    assert all(not request["outcomes"]["sent"] for request in requests)


def test_mutation_proposals_are_design_only_tailnet_private_and_rollback_gated() -> None:
    stage_b, stage_c = mutation_proposals()

    assert stage_b["execution_node"] == HOME_EDGE_NODE_ID
    assert stage_b["lane"] == "privileged_mutation"
    assert stage_b["run_as"] == "root"
    assert stage_b["status"] == "design_only_not_applied"
    assert "--no-public-bind" in stage_b["argv"]
    assert "0.0.0.0" in stage_b["forbidden_bindings"]
    assert stage_b["requires_existing_secondary_adguard"] is True
    assert stage_b["requires_registered_approved_control"] is True
    assert "fail_closed_if_secondary_unavailable" in stage_b["safeguards"]
    assert "live_probe_from_redmi" in stage_b["safeguards"]
    assert "rollback_on_failed_probe" in stage_b["safeguards"]
    assert APPROVED_PRIVATE_DNS_SERVER_ROLE in stage_b["argv"]

    assert stage_c["requires_registered_approved_control"] is True
    assert stage_c["status"] == "proposal_only_not_applied"
    assert "--override-dns" in stage_c["argv"]
    assert "--preserve-magicdns" in stage_c["argv"]
    assert "--rollback-on-failed-reachability" in stage_c["argv"]


def test_stage_b_fails_closed_without_registered_control_even_after_preflight_passes() -> None:
    policy = build_rollout_policy(
        lan=LanEvidence(
            primary_adguard_verified=True,
            secondary_adguard_verified=True,
            router_dhcp_dns_verified=True,
            outbound_dns_enforcement_verified=True,
            ipv6_ra_rdnss_verified=True,
            failover_verified=True,
            all_client_coverage_verified=True,
        )
    )

    stage_b = next(stage for stage in policy["stages"] if stage["stage"] == "B")
    stage_b_gates = {gate["id"]: gate for gate in stage_b["gates"]}

    assert stage_b["ready"] is False
    assert "stage_b_registered_approved_control" in stage_b["blocked_until"]
    assert stage_b_gates["stage_b_registered_approved_control"]["passed"] is False
    assert "stage_b_registered_approved_control" in policy_blockers(policy)


def test_stage_b_fails_closed_when_secondary_adguard_is_unavailable() -> None:
    policy = build_rollout_policy(
        lan=LanEvidence(
            primary_adguard_verified=True,
            secondary_adguard_verified=False,
            router_dhcp_dns_verified=True,
            outbound_dns_enforcement_verified=True,
            ipv6_ra_rdnss_verified=True,
            failover_verified=True,
            all_client_coverage_verified=True,
        ),
        controls=ControlEvidence(stage_b_registered_approved_control=True),
    )

    stage_b = next(stage for stage in policy["stages"] if stage["stage"] == "B")

    assert stage_b["ready"] is False
    assert "existing_secondary_adguard_available" in stage_b["blocked_until"]
    assert "lan_secondary_adguard" in policy_blockers(policy)


def test_stage_c_and_d_require_independent_per_client_attestation_and_consent() -> None:
    policy = build_rollout_policy(
        lan=LanEvidence(all_client_coverage_verified=True),
        clients=(
            ClientEvidence(
                role="redmi",
                enrolled_tailnet=True,
                consent_recorded=True,
                home_reachability_verified=True,
                away_reachability_verified=True,
                resolver_attested_independently=True,
                tailscale_dns_verified=True,
            ),
            ClientEvidence(
                role="family_iphone",
                enrolled_tailnet=False,
                consent_recorded=False,
                home_reachability_verified=True,
                away_reachability_verified=True,
                resolver_attested_independently=True,
            ),
        ),
    )

    stage_c = next(stage for stage in policy["stages"] if stage["stage"] == "C")
    stage_d = next(stage for stage in policy["stages"] if stage["stage"] == "D")
    stage_c_gates = {gate["id"]: gate for gate in stage_c["gates"]}
    stage_d_gates = {gate["id"]: gate for gate in stage_d["gates"]}

    assert stage_c_gates["independent_resolver_attestation"]["passed"] is True
    assert stage_d_gates["redmi_ready_before_dns_enabled"]["passed"] is True
    assert stage_d_gates["family_consent"]["passed"] is False
    assert any("YouTube" in item for item in stage_d["truthful_limitations"])
    assert any("same hostname" in item for item in stage_d["truthful_limitations"])

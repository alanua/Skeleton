from __future__ import annotations

import pytest

from core.phone_dns_policy import (
    AndroidResolverState,
    DnsEndpoint,
    DnsQueryResult,
    PhoneDnsRuntimeState,
    ProbeStatus,
    build_phone_dns_report,
)


def endpoint(label: str = "primary") -> DnsEndpoint:
    return DnsEndpoint(label=label, host=f"{label}.private.invalid")


def state(**overrides: object) -> PhoneDnsRuntimeState:
    values = {
        "phone_node_id": "redmi-registered-fixture",
        "endpoints": (endpoint(),),
        "allow_domain": "allowed.example.test",
        "blocked_domain": "ads.example.test",
        "android_resolver": AndroidResolverState(
            active_resolver="private_dns",
            private_dns_mode="hostname",
            private_dns_hostname_set=True,
            vpn_active=True,
            tailscale_vpn_active=True,
            magicdns_enabled=True,
            verified=True,
        ),
        "tailscale_reachable": True,
        "app_rendering_verified": False,
    }
    values.update(overrides)
    return PhoneDnsRuntimeState(**values)


def fake_resolver(_endpoint: DnsEndpoint, domain: str) -> DnsQueryResult:
    if domain.startswith("ads."):
        return DnsQueryResult(status=ProbeStatus.BLOCKED, rcode="NXDOMAIN", filtered=True)
    return DnsQueryResult(status=ProbeStatus.OK, rcode="NOERROR", answer_count=1, filtered=False)


def test_report_distinguishes_dns_filtering_from_app_rendering_verification() -> None:
    report = build_phone_dns_report(state(), fake_resolver)

    assert report["checks"]["dns_reply_correctness"]["status"] == "ok"
    assert report["checks"]["filter_correctness"]["status"] == "blocked"
    assert report["checks"]["adguard_coverage"]["status"] == "ok"
    assert report["checks"]["app_ad_rendering"]["status"] == "unverified"
    assert report["rollout"]["decision"] == "ready_for_operator_approval"


def test_dns_server_unavailable_blocks_rollout() -> None:
    def unavailable(_endpoint: DnsEndpoint, _domain: str) -> DnsQueryResult:
        raise OSError("timeout")

    report = build_phone_dns_report(state(), unavailable)

    assert report["checks"]["dns_reply_correctness"]["status"] == "unavailable"
    assert "allow_dns_works" in report["rollout"]["blocking_postconditions"]
    assert report["rollout"]["decision"] == "blocked"


def test_fallback_to_secondary_is_reported_without_exposing_hosts() -> None:
    endpoints = (endpoint("primary"), endpoint("secondary"))

    def resolver(candidate: DnsEndpoint, domain: str) -> DnsQueryResult:
        if candidate.label == "primary":
            return DnsQueryResult(status=ProbeStatus.UNAVAILABLE, error="timeout")
        return fake_resolver(candidate, domain)

    report = build_phone_dns_report(state(endpoints=endpoints), resolver)

    assert report["checks"]["dns_reply_correctness"]["status"] == "fallback"
    assert report["checks"]["filter_correctness"]["status"] == "fallback"
    assert report["checks"]["dns_reply_correctness"]["resolver_label"] == "secondary"
    rendered = repr(report)
    assert "primary.private.invalid" not in rendered
    assert "secondary.private.invalid" not in rendered


def test_loss_of_tailscale_blocks_phone_only_rollout() -> None:
    report = build_phone_dns_report(state(tailscale_reachable=False), fake_resolver)

    assert report["checks"]["tailnet_reachability"]["status"] == "unavailable"
    assert "tailnet_reachable" in report["rollout"]["blocking_postconditions"]
    assert report["rollout"]["phone_only_supported"] is False


def test_android_verified_state_is_independent_gate() -> None:
    android = AndroidResolverState(active_resolver="termux_probe_only", verified=False)

    report = build_phone_dns_report(state(android_resolver=android), fake_resolver)

    assert report["checks"]["android_active_resolver"]["status"] == "unverified"
    assert "android_resolver_independently_verified" in report["rollout"]["blocking_postconditions"]


def test_tailnet_wide_mutation_is_never_allowed_by_phone_probe() -> None:
    report = build_phone_dns_report(state(tailnet_wide_mutation_requested=True), fake_resolver)

    assert report["tailnet_wide_mutation"] == {
        "requested": True,
        "allowed": False,
        "separate_approval_required": True,
    }
    assert "no_tailnet_wide_mutation" in report["rollout"]["blocking_postconditions"]


def test_requires_registered_identity_and_injected_endpoints() -> None:
    with pytest.raises(ValueError, match="registered phone node identity"):
        build_phone_dns_report(state(phone_node_id=""), fake_resolver)

    with pytest.raises(ValueError, match="runtime-injected DNS endpoint"):
        build_phone_dns_report(state(endpoints=()), fake_resolver)

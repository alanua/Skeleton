"""Read-only phone DNS diagnostics and rollout policy.

The module intentionally treats live addresses, private node names and resolver
targets as runtime-injected data. Public reports use endpoint labels and stable
registered device identities, never raw private network coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable, Mapping, Sequence


PHONE_DNS_REPORT_SCHEMA = "skeleton.phone_dns.report.v1"
PHONE_DNS_POLICY_VERSION = "1.0.0"


class ProbeStatus(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FALLBACK = "fallback"
    UNVERIFIED = "unverified"


class RolloutDecision(StrEnum):
    READY_FOR_OPERATOR_APPROVAL = "ready_for_operator_approval"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DnsEndpoint:
    label: str
    host: str
    port: int = 53
    transport: str = "udp"
    role: str = "adguard_primary"
    tailnet_required: bool = True

    def public_mapping(self) -> dict[str, object]:
        return {
            "label": self.label,
            "transport": self.transport,
            "role": self.role,
            "tailnet_required": self.tailnet_required,
        }


@dataclass(frozen=True)
class DnsQueryResult:
    status: ProbeStatus | str
    rcode: str | None = None
    answer_count: int = 0
    filtered: bool | None = None
    resolver_label: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AndroidResolverState:
    active_resolver: str
    private_dns_mode: str = "unknown"
    private_dns_hostname_set: bool = False
    vpn_active: bool = False
    tailscale_vpn_active: bool = False
    magicdns_enabled: bool | None = None
    verified: bool = False

    def public_mapping(self) -> dict[str, object]:
        return {
            "active_resolver": self.active_resolver,
            "private_dns_mode": self.private_dns_mode,
            "private_dns_hostname_set": self.private_dns_hostname_set,
            "vpn_active": self.vpn_active,
            "tailscale_vpn_active": self.tailscale_vpn_active,
            "magicdns_enabled": self.magicdns_enabled,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class PhoneDnsRuntimeState:
    phone_node_id: str
    endpoints: tuple[DnsEndpoint, ...]
    allow_domain: str
    blocked_domain: str
    android_resolver: AndroidResolverState
    tailscale_reachable: bool
    adguard_expected: bool = True
    app_rendering_verified: bool = False
    tailnet_wide_mutation_requested: bool = False


Resolver = Callable[[DnsEndpoint, str], DnsQueryResult]


def build_phone_dns_report(state: PhoneDnsRuntimeState, resolver: Resolver) -> dict[str, object]:
    """Run read-only DNS probes and return a public-safe report."""

    _validate_state(state)
    allow_results = _query_candidates(state.endpoints, state.allow_domain, resolver)
    blocked_results = _query_candidates(state.endpoints, state.blocked_domain, resolver)

    allow_summary = _summarize_allow(allow_results)
    blocked_summary = _summarize_blocked(blocked_results)
    adguard_coverage = _adguard_coverage(state, blocked_summary)
    android_gate = _android_verified_gate(state.android_resolver)
    postconditions = _postconditions(state, allow_summary, blocked_summary, adguard_coverage, android_gate)
    rollout = _rollout_decision(postconditions)

    return {
        "schema": PHONE_DNS_REPORT_SCHEMA,
        "policy_version": PHONE_DNS_POLICY_VERSION,
        "device": {"node_id": state.phone_node_id, "identity_source": "registered_device_identity"},
        "endpoints": [endpoint.public_mapping() for endpoint in state.endpoints],
        "probes": {
            "allow_domain": _public_probe_results(allow_results),
            "blocked_domain": _public_probe_results(blocked_results),
        },
        "checks": {
            "tailnet_reachability": {
                "status": ProbeStatus.OK.value if state.tailscale_reachable else ProbeStatus.UNAVAILABLE.value,
                "meaning": "phone can reach the injected resolver route over the tailnet",
            },
            "dns_reply_correctness": allow_summary,
            "filter_correctness": blocked_summary,
            "android_active_resolver": android_gate,
            "adguard_coverage": adguard_coverage,
            "app_ad_rendering": {
                "status": ProbeStatus.OK.value if state.app_rendering_verified else ProbeStatus.UNVERIFIED.value,
                "meaning": "DNS success is not physical proof that apps stopped rendering ads",
            },
        },
        "rollout": rollout,
        "postconditions": postconditions,
        "tailnet_wide_mutation": {
            "requested": state.tailnet_wide_mutation_requested,
            "allowed": False,
            "separate_approval_required": True,
        },
    }


def _query_candidates(endpoints: Sequence[DnsEndpoint], domain: str, resolver: Resolver) -> list[DnsQueryResult]:
    results: list[DnsQueryResult] = []
    for endpoint in endpoints:
        try:
            result = resolver(endpoint, domain)
        except OSError as exc:
            result = DnsQueryResult(
                status=ProbeStatus.UNAVAILABLE,
                resolver_label=endpoint.label,
                error=exc.__class__.__name__,
            )
        if result.resolver_label is None:
            result = DnsQueryResult(
                status=result.status,
                rcode=result.rcode,
                answer_count=result.answer_count,
                filtered=result.filtered,
                resolver_label=endpoint.label,
                error=result.error,
            )
        results.append(result)
        if ProbeStatus(result.status) in {ProbeStatus.OK, ProbeStatus.BLOCKED}:
            break
    return results


def _summarize_allow(results: Sequence[DnsQueryResult]) -> dict[str, object]:
    if not results:
        return {"status": ProbeStatus.UNAVAILABLE.value, "meaning": "no resolver endpoints were configured"}
    first_success = next(
        (item for item in results if ProbeStatus(item.status) == ProbeStatus.OK and item.answer_count > 0),
        None,
    )
    if first_success:
        status = ProbeStatus.FALLBACK if len(results) > 1 else ProbeStatus.OK
        return {
            "status": status.value,
            "resolver_label": first_success.resolver_label,
            "meaning": "allowed domain produced a DNS answer",
        }
    if any(ProbeStatus(item.status) == ProbeStatus.UNAVAILABLE for item in results):
        return {"status": ProbeStatus.UNAVAILABLE.value, "meaning": "no configured resolver returned an allowed answer"}
    return {"status": ProbeStatus.BLOCKED.value, "meaning": "allowed domain did not return a usable answer"}


def _summarize_blocked(results: Sequence[DnsQueryResult]) -> dict[str, object]:
    if not results:
        return {"status": ProbeStatus.UNAVAILABLE.value, "meaning": "no resolver endpoints were configured"}
    first_block = next(
        (item for item in results if ProbeStatus(item.status) == ProbeStatus.BLOCKED or item.filtered is True),
        None,
    )
    if first_block:
        status = ProbeStatus.FALLBACK if len(results) > 1 else ProbeStatus.BLOCKED
        return {
            "status": status.value,
            "resolver_label": first_block.resolver_label,
            "meaning": "blocked domain was filtered by DNS policy",
        }
    if any(ProbeStatus(item.status) == ProbeStatus.UNAVAILABLE for item in results):
        return {"status": ProbeStatus.UNAVAILABLE.value, "meaning": "no configured resolver returned a filter decision"}
    return {"status": ProbeStatus.OK.value, "meaning": "blocked domain resolved, so filtering is not proven"}


def _adguard_coverage(state: PhoneDnsRuntimeState, blocked_summary: Mapping[str, object]) -> dict[str, object]:
    if not state.adguard_expected:
        return {"status": ProbeStatus.UNVERIFIED.value, "meaning": "AdGuard was not declared in injected runtime state"}
    if blocked_summary.get("status") in {ProbeStatus.BLOCKED.value, ProbeStatus.FALLBACK.value}:
        return {"status": ProbeStatus.OK.value, "meaning": "AdGuard-style filter response was observed"}
    return {"status": ProbeStatus.UNVERIFIED.value, "meaning": "AdGuard coverage is not proven by this probe"}


def _android_verified_gate(android: AndroidResolverState) -> dict[str, object]:
    status = ProbeStatus.OK if android.verified else ProbeStatus.UNVERIFIED
    return {
        "status": status.value,
        "state": android.public_mapping(),
        "meaning": "independent Android resolver evidence is required; Termux DNS is not proof of system DNS",
    }


def _postconditions(
    state: PhoneDnsRuntimeState,
    allow_summary: Mapping[str, object],
    blocked_summary: Mapping[str, object],
    adguard_coverage: Mapping[str, object],
    android_gate: Mapping[str, object],
) -> dict[str, bool]:
    return {
        "phone_identity_registered": bool(state.phone_node_id),
        "tailnet_reachable": state.tailscale_reachable,
        "allow_dns_works": allow_summary.get("status") in {ProbeStatus.OK.value, ProbeStatus.FALLBACK.value},
        "blocked_dns_filtered": blocked_summary.get("status") in {ProbeStatus.BLOCKED.value, ProbeStatus.FALLBACK.value},
        "android_resolver_independently_verified": android_gate.get("status") == ProbeStatus.OK.value,
        "adguard_coverage_observed": adguard_coverage.get("status") == ProbeStatus.OK.value,
        "app_rendering_separately_verified": state.app_rendering_verified,
        "no_tailnet_wide_mutation": not state.tailnet_wide_mutation_requested,
    }


def _rollout_decision(postconditions: Mapping[str, bool]) -> dict[str, object]:
    blocking = [name for name, ok in postconditions.items() if name != "app_rendering_separately_verified" and not ok]
    if blocking:
        return {
            "decision": RolloutDecision.BLOCKED.value,
            "blocking_postconditions": blocking,
            "phone_only_supported": False,
            "operator_action": "do not deploy; prepare a separate approvable tailnet-wide DNS option if phone-only gates cannot pass",
        }
    return {
        "decision": RolloutDecision.READY_FOR_OPERATOR_APPROVAL.value,
        "blocking_postconditions": [],
        "phone_only_supported": True,
        "operator_action": "eligible for a future reversible staged deployment packet; this report does not deploy it",
    }


def _public_probe_results(results: Iterable[DnsQueryResult]) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    for result in results:
        item: dict[str, object] = {
            "resolver_label": result.resolver_label,
            "status": ProbeStatus(result.status).value,
            "rcode": result.rcode,
            "answer_count": result.answer_count,
            "filtered": result.filtered,
        }
        if result.error:
            item["error_class"] = result.error
        public.append(item)
    return public


def _validate_state(state: PhoneDnsRuntimeState) -> None:
    if not state.phone_node_id.strip():
        raise ValueError("registered phone node identity is required")
    if not state.endpoints:
        raise ValueError("at least one runtime-injected DNS endpoint is required")
    if not state.allow_domain.strip() or not state.blocked_domain.strip():
        raise ValueError("allow_domain and blocked_domain are required")
    for endpoint in state.endpoints:
        if not endpoint.label.strip() or not endpoint.host.strip():
            raise ValueError("DNS endpoint label and runtime host are required")
        if endpoint.port < 1 or endpoint.port > 65535:
            raise ValueError("DNS endpoint port is out of range")

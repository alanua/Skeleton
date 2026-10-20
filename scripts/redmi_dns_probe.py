#!/usr/bin/env python3
"""Read-only Redmi DNS diagnostic report."""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
from typing import Any

from core.phone_dns_policy import (
    AndroidResolverState,
    DnsEndpoint,
    DnsQueryResult,
    PhoneDnsRuntimeState,
    ProbeStatus,
    build_phone_dns_report,
)


def main_with_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a public-safe read-only phone DNS diagnostic report.")
    parser.add_argument("--phone-node-id", default=os.environ.get("SKELETON_PHONE_NODE_ID", ""))
    parser.add_argument("--endpoints-json", default=os.environ.get("SKELETON_PHONE_DNS_ENDPOINTS_JSON", ""))
    parser.add_argument("--allow-domain", required=True)
    parser.add_argument("--blocked-domain", required=True)
    parser.add_argument("--android-state-json", default=os.environ.get("SKELETON_ANDROID_RESOLVER_STATE_JSON", "{}"))
    parser.add_argument("--tailscale-reachable", action="store_true")
    parser.add_argument("--app-rendering-verified", action="store_true")
    args = parser.parse_args(argv)

    try:
        state = PhoneDnsRuntimeState(
            phone_node_id=args.phone_node_id,
            endpoints=_load_endpoints(args.endpoints_json),
            allow_domain=args.allow_domain,
            blocked_domain=args.blocked_domain,
            android_resolver=_load_android_state(args.android_state_json),
            tailscale_reachable=args.tailscale_reachable,
            app_rendering_verified=args.app_rendering_verified,
        )
        report = build_phone_dns_report(state, udp_dns_resolver)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(report, sort_keys=True))
    return 0


def _load_endpoints(raw: str) -> tuple[DnsEndpoint, ...]:
    if not raw.strip():
        raise ValueError("SKELETON_PHONE_DNS_ENDPOINTS_JSON or --endpoints-json is required")
    loaded = json.loads(raw)
    if not isinstance(loaded, list):
        raise ValueError("DNS endpoints JSON must be a list")
    endpoints = []
    for item in loaded:
        if not isinstance(item, dict):
            raise ValueError("DNS endpoint entries must be objects")
        endpoints.append(
            DnsEndpoint(
                label=str(item.get("label", "")).strip(),
                host=str(item.get("host", "")).strip(),
                port=int(item.get("port", 53)),
                transport=str(item.get("transport", "udp")),
                role=str(item.get("role", "adguard_primary")),
                tailnet_required=bool(item.get("tailnet_required", True)),
            )
        )
    return tuple(endpoints)


def _load_android_state(raw: str) -> AndroidResolverState:
    loaded: dict[str, Any] = json.loads(raw or "{}")
    if not isinstance(loaded, dict):
        raise ValueError("Android resolver state JSON must be an object")
    return AndroidResolverState(
        active_resolver=str(loaded.get("active_resolver", "unknown")),
        private_dns_mode=str(loaded.get("private_dns_mode", "unknown")),
        private_dns_hostname_set=bool(loaded.get("private_dns_hostname_set", False)),
        vpn_active=bool(loaded.get("vpn_active", False)),
        tailscale_vpn_active=bool(loaded.get("tailscale_vpn_active", False)),
        magicdns_enabled=loaded.get("magicdns_enabled"),
        verified=bool(loaded.get("verified", False)),
    )


def udp_dns_resolver(endpoint: DnsEndpoint, domain: str) -> DnsQueryResult:
    if endpoint.transport != "udp":
        raise OSError("only udp DNS probes are implemented by this read-only script")
    packet = _build_query(domain)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3.0)
        sock.sendto(packet, (endpoint.host, endpoint.port))
        data, _addr = sock.recvfrom(512)
    rcode, answer_count = _parse_response(data)
    filtered = rcode in {"NXDOMAIN", "REFUSED"} or answer_count == 0
    return DnsQueryResult(
        status=ProbeStatus.BLOCKED if filtered else ProbeStatus.OK,
        rcode=rcode,
        answer_count=answer_count,
        filtered=filtered,
        resolver_label=endpoint.label,
    )


def _build_query(domain: str) -> bytes:
    labels = [label.encode("ascii") for label in domain.rstrip(".").split(".") if label]
    if not labels or any(len(label) > 63 for label in labels):
        raise ValueError("invalid DNS domain")
    header = struct.pack("!HHHHHH", 0x534B, 0x0100, 1, 0, 0, 0)
    question = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    return header + question + struct.pack("!HH", 1, 1)


def _parse_response(data: bytes) -> tuple[str, int]:
    if len(data) < 12:
        raise OSError("short DNS response")
    _txid, flags, _qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", data[:12])
    rcode = flags & 0x000F
    rcode_name = {0: "NOERROR", 2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}.get(rcode, f"RCODE_{rcode}")
    return rcode_name, ancount


if __name__ == "__main__":
    raise SystemExit(main_with_args())

from __future__ import annotations

import json

import pytest

from core.phone_dns_policy import DnsEndpoint, DnsQueryResult, ProbeStatus
from scripts import redmi_dns_probe


def test_cli_emits_redacted_report(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def resolver(endpoint: DnsEndpoint, domain: str) -> DnsQueryResult:
        if domain == "ads.example.test":
            return DnsQueryResult(
                status=ProbeStatus.BLOCKED,
                rcode="NXDOMAIN",
                filtered=True,
                resolver_label=endpoint.label,
            )
        return DnsQueryResult(
            status=ProbeStatus.OK,
            rcode="NOERROR",
            answer_count=1,
            filtered=False,
            resolver_label=endpoint.label,
        )

    monkeypatch.setattr(redmi_dns_probe, "udp_dns_resolver", resolver)
    code = redmi_dns_probe.main_with_args(
        [
            "--phone-node-id",
            "redmi-registered-fixture",
            "--endpoints-json",
            json.dumps([{"label": "primary", "host": "100.64.0.10"}]),
            "--allow-domain",
            "allowed.example.test",
            "--blocked-domain",
            "ads.example.test",
            "--android-state-json",
            json.dumps({"active_resolver": "private_dns", "verified": True}),
            "--tailscale-reachable",
        ]
    )

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "skeleton.phone_dns.report.v1"
    assert report["endpoints"] == [
        {"label": "primary", "transport": "udp", "role": "adguard_primary", "tailnet_required": True}
    ]
    assert "100.64.0.10" not in repr(report)


def test_cli_fails_closed_without_injected_endpoints(capsys: pytest.CaptureFixture[str]) -> None:
    code = redmi_dns_probe.main_with_args(
        [
            "--phone-node-id",
            "redmi-registered-fixture",
            "--allow-domain",
            "allowed.example.test",
            "--blocked-domain",
            "ads.example.test",
        ]
    )

    assert code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"

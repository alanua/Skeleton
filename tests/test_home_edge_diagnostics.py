from __future__ import annotations

import json
from pathlib import Path

from core.home_edge.diagnostics import build_diagnostic_artifact, build_operator_report, run_home_edge_diagnostic
from core.home_edge.profile import load_home_edge_profile
from core.home_edge.remote import compact_status, run_audited_home_edge_command
from core.home_edge.transport import ProbeResult


class FakeTransport:
    adapter_name = "fake"

    def __init__(self, payload: dict | None) -> None:
        self.payload = payload

    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        if self.payload is None:
            return ProbeResult(state="unverified", adapter="fake", target="template", reason="test_unavailable")
        return ProbeResult(state="observed", adapter="fake", target="template", stdout=json.dumps(self.payload), exit_code=0)


def sample_remote(*, modem_present: bool = True) -> dict:
    return {
        "system": {"hostname": "template-observed", "kernel": "test"},
        "network": {"default_route": {"dev": "eth-template0", "gateway": "192.0.2.1"}},
        "tailscale": {"self_ips": ["100.64.0.10"], "json_available": True},
        "hardware": {"huawei_e3372": {"present": modem_present, "usb_id": "12d1:1506" if modem_present else None}},
        "modemmanager": {
            "modem": {"model": "E3372", "state": "locked", "sim_present": True},
            "ports": {"net": "wwx-test", "serial": ["port-a", "port-b"], "control": "control-a"},
            "signal": {"lte": {"rsrp": "test"}},
        },
        "tools": {"python3": {"present": True}, "docker": {"present": True}},
        "services": {"docker.service": "active"},
        "containers": {"available": True, "running_count": 2},
        "browser": {"executables": {"google-chrome-stable": True}, "process_count": 0, "profile_lock_count": 1},
        "media": {"ffmpeg": True},
        "home_automation": {"docker_available": True},
        "capability_inventory": {"system": True, "network": True, "services": True, "containers": True, "media": True, "browser": True, "hardware": True, "home_automation": True},
    }


def test_observed_diagnostic_writes_public_artifact(tmp_path: Path) -> None:
    artifact_path = tmp_path / "diag.json"
    artifact = run_home_edge_diagnostic(artifact_path=artifact_path, transport=FakeTransport(sample_remote()))

    assert artifact["evidence"]["registration"]["state"] == "template"
    assert artifact["evidence"]["runtime"]["state"] == "observed"
    assert artifact["summary"]["route"]["status"] == "unchanged"
    assert artifact["summary"]["tailscale"]["status"] == "healthy"
    assert artifact["summary"]["modem"]["status"] == "identified"
    assert json.loads(artifact_path.read_text(encoding="utf-8"))["schema"] == "skeleton.home_edge.diagnostic.v2"


def test_unavailable_runtime_has_no_profile_fallback(tmp_path: Path) -> None:
    artifact = run_home_edge_diagnostic(artifact_path=tmp_path / "diag.json", transport=FakeTransport(None))

    assert artifact["runtime"] is None
    assert artifact["evidence"]["runtime"]["state"] == "unverified"
    assert artifact["summary"]["route"]["observed"] == {"state": "unverified", "interface": None, "gateway": None}
    assert artifact["summary"]["tailscale"]["observed"] == {"state": "unverified", "ips": []}
    assert artifact["summary"]["modem"]["observed"] is None


def test_modem_absence_is_optional_observed_capability() -> None:
    artifact = build_diagnostic_artifact(load_home_edge_profile(environment={}), sample_remote(modem_present=False))

    assert artifact["summary"]["status"] == "observed"
    assert artifact["summary"]["modem"]["status"] == "not_present"
    assert artifact["summary"]["gateway"]["status"] == "ready"


def test_browser_projection_uses_same_observed_artifact(tmp_path: Path) -> None:
    result = run_audited_home_edge_command("browser_diagnostic", artifact_path=tmp_path / "diag.json", transport=FakeTransport(sample_remote()))

    assert result["action_id"] == "browser_diagnostic"
    assert result["status"] == "observed"
    assert result["value"]["profile_lock_count"] == 1


def test_operator_report_and_compact_status_are_stable() -> None:
    artifact = build_diagnostic_artifact(load_home_edge_profile(environment={}), sample_remote())
    report = build_operator_report(artifact)
    compact = compact_status(artifact)

    assert "gateway=ready" in report
    assert compact["route_status"] == "unchanged"
    assert compact["tailscale_status"] == "healthy"
    assert compact["modem_status"] == "identified"
    assert compact["modem_lock_state"] == "locked"

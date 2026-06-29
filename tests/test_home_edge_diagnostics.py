from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.home_edge.diagnostics import (
    HomeEdgeDiagnosticError,
    ProbeResult,
    SYNTHETIC_TEMPLATE_ARTIFACT_PATH,
    build_diagnostic_artifact,
    build_operator_report,
    compact_status,
    run_audited_home_edge_command,
    run_home_edge_diagnostic,
)
from core.home_edge.profile import load_home_edge_profile, synthetic_profile_mapping


class FakeTransport:
    adapter_name = "fake"

    def __init__(self, payload: dict | None) -> None:
        self.payload = payload

    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        if self.payload is None:
            return ProbeResult(state="unverified", adapter="fake", reason="test_unavailable")
        return ProbeResult(state="observed", adapter="fake", stdout=json.dumps(self.payload), exit_code=0)


def sample_remote(*, modem_present: bool = True) -> dict:
    return {
        "system": {"hostname": "home-edge-01", "kernel": "test"},
        "network": {"default_route": {"dev": "test-lan0", "gateway": "192.0.2.254"}},
        "tailscale": {"self_ips": ["100.64.10.74"], "json_available": True},
        "hardware": {"huawei_e3372": {"present": modem_present, "usb_id": "12d1:1506" if modem_present else None}},
        "modemmanager": {
            "modem": {"model": "E3372", "state": "locked", "sim_present": True},
            "ports": {"net": "test-net0", "serial": ["port-a", "port-b"], "control": "control-a"},
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


def test_observed_diagnostic_writes_private_artifact(tmp_path: Path) -> None:
    artifact_path = tmp_path / "diag.json"
    profile = load_home_edge_profile(_private_profile_path(tmp_path))
    artifact = run_home_edge_diagnostic(
        profile=profile,
        artifact_path=artifact_path,
        transport=FakeTransport(sample_remote()),
    )

    assert artifact["evidence"]["registration"]["state"] == "registered"
    assert artifact["evidence"]["runtime"]["state"] == "observed"
    assert artifact["summary"]["route"]["status"] == "unchanged"
    assert artifact["summary"]["tailscale"]["status"] == "healthy"
    assert artifact["summary"]["modem"]["status"] == "identified"
    assert artifact["node"]["target_user"] == "private"
    assert artifact["summary"]["gateway"]["target"]["value"] == "private_home_edge"
    assert json.loads(artifact_path.read_text(encoding="utf-8"))["schema"] == "skeleton.home_edge.diagnostic.v2"


def test_default_run_has_no_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKELETON_HOME_EDGE_01_DIAGNOSTIC_ARTIFACT", raising=False)
    artifact = run_home_edge_diagnostic(transport=FakeTransport(None))

    assert artifact["runtime"] is None
    assert artifact["evidence"]["runtime"]["state"] == "unverified"
    assert not (tmp_path / "diag.json").exists()


def test_unavailable_runtime_has_no_profile_fallback(tmp_path: Path) -> None:
    artifact = run_home_edge_diagnostic(artifact_path=tmp_path / "diag.json", transport=FakeTransport(None))

    assert artifact["runtime"] is None
    assert artifact["evidence"]["runtime"]["state"] == "unverified"
    assert artifact["summary"]["route"]["observed"] == {"state": "unverified"}
    assert artifact["summary"]["tailscale"]["observed"] == {"state": "unverified"}
    assert artifact["summary"]["modem"]["observed"] is None


def test_modem_absence_is_optional_observed_capability() -> None:
    artifact = build_diagnostic_artifact(load_home_edge_profile(), sample_remote(modem_present=False))

    assert artifact["summary"]["status"] == "observed"
    assert artifact["summary"]["modem"]["status"] == "not_present"
    assert artifact["summary"]["gateway"]["status"] == "ready"


def test_browser_projection_uses_same_observed_artifact(tmp_path: Path) -> None:
    result = run_audited_home_edge_command("browser_diagnostic", artifact_path=tmp_path / "diag.json", transport=FakeTransport(sample_remote()))

    assert result["action_id"] == "browser_diagnostic"
    assert result["status"] == "observed"
    assert result["value"]["status"] == "observed"


def test_real_runtime_profile_rejects_repository_artifact_path(tmp_path: Path) -> None:
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["ssh"]["target_user"] = "runtime-user"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(data), encoding="utf-8")
    profile = load_home_edge_profile(profile_path)

    with pytest.raises(HomeEdgeDiagnosticError, match="outside the public repository"):
        run_home_edge_diagnostic(
            profile=profile,
            artifact_path=Path("docs/home_edge/home-edge-01-diagnostic.latest.json"),
            transport=FakeTransport(sample_remote()),
        )


def test_local_profile_rejects_repository_artifact_path_even_with_template_identity(
    tmp_path: Path,
) -> None:
    profile = load_home_edge_profile(
        _write_profile(tmp_path, synthetic_profile_mapping())
    )

    assert profile.is_template_identity
    assert profile.source == "local_profile"
    with pytest.raises(HomeEdgeDiagnosticError, match="outside the public repository"):
        run_home_edge_diagnostic(
            profile=profile,
            artifact_path=SYNTHETIC_TEMPLATE_ARTIFACT_PATH,
            transport=FakeTransport(sample_remote()),
        )


def test_environment_override_rejects_repository_artifact_path_even_with_template_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "synthetic-home-edge")
    profile = load_home_edge_profile()

    assert profile.is_template_identity
    assert profile.source == "environment_overrides"
    with pytest.raises(HomeEdgeDiagnosticError, match="outside the public repository"):
        run_home_edge_diagnostic(
            profile=profile,
            artifact_path=SYNTHETIC_TEMPLATE_ARTIFACT_PATH,
            transport=FakeTransport(sample_remote()),
        )


def test_synthetic_template_rejects_other_repository_artifact_path() -> None:
    with pytest.raises(HomeEdgeDiagnosticError, match="synthetic diagnostic template"):
        run_home_edge_diagnostic(
            artifact_path=Path("docs/home_edge/archive/home-edge-01-diagnostic.latest.json"),
            transport=FakeTransport(sample_remote()),
        )


def test_real_runtime_profile_can_use_private_artifact_path(tmp_path: Path) -> None:
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["ssh"]["target_user"] = "runtime-user"
    profile = load_home_edge_profile(_write_profile(tmp_path, data))
    artifact_path = tmp_path / "private" / "diag.json"

    artifact = run_home_edge_diagnostic(
        profile=profile,
        artifact_path=artifact_path,
        transport=FakeTransport(sample_remote()),
    )

    assert artifact["summary"]["tailscale"]["status"] == "healthy"
    written = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert "runtime-host" not in json.dumps(written)
    assert "runtime-user" not in json.dumps(written)
    assert "100.64.10.74" not in json.dumps(written)


def test_public_artifact_excludes_raw_runtime_inventory(tmp_path: Path) -> None:
    artifact = run_home_edge_diagnostic(
        artifact_path=tmp_path / "diag.json",
        transport=FakeTransport(sample_remote()),
    )
    payload = json.dumps(artifact, sort_keys=True)

    assert "home-edge-01@" not in payload
    assert "100.64.10.74" not in payload
    assert "192.0.2.254" not in payload
    assert "test-lan0" not in payload
    assert "test-net0" not in payload


def test_operator_report_and_compact_status_are_stable() -> None:
    profile = load_home_edge_profile(_private_profile_path(Path("/tmp")))
    artifact = build_diagnostic_artifact(profile, sample_remote())
    report = build_operator_report(artifact)
    compact = compact_status(artifact)

    assert "gateway=ready" in report
    assert compact["route_status"] == "unchanged"
    assert compact["tailscale_status"] == "healthy"
    assert compact["modem_status"] == "identified"
    assert compact["modem_lock_state"] == "locked"


def _write_profile(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _private_profile_path(tmp_path: Path) -> Path:
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["ssh"]["target_user"] = "runtime-user"
    data["primary_network"] = {"interface": "test-lan0", "gateway": "192.0.2.254"}
    return _write_profile(tmp_path, data)

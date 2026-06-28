from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.home_edge.diagnostics import build_operator_report, run_home_edge_diagnostic
from core.home_edge.profile import load_home_edge_profile
from core.home_edge.remote import compact_status, run_audited_home_edge_command


def sample_remote() -> dict:
    return {
        "network": {"default_route": {"dev": "enp1s0", "gateway": "192.168.1.1"}},
        "tailscale": {"self_ips": ["100.127.35.74"], "json_available": True},
        "usb": {"huawei_e3372": {"present": True, "usb_id": "12d1:1506"}},
        "network_manager": {"huawei_diag": {"exists": True, "type": "802-3-ethernet"}},
        "modemmanager": {
            "modem": {
                "model": "E3372",
                "state": "locked",
                "sim_present": True,
                "firmware_revision": "21.315.01.00.00",
                "radio_capabilities": ["gsm-umts", "lte"],
                "supported_modes": ["2g", "3g", "4g"],
                "current_modes": ["4g"],
            },
            "ports": {
                "net": "wwx001e101f0000",
                "serial": ["/dev/ttyUSB0", "/dev/ttyUSB1"],
                "control": "/dev/cdc-wdm3",
            },
            "signal": {"lte": {"rsrp": "-91.00 dBm"}},
        },
        "tools": {"python3": {"present": True}, "docker": {"present": True}},
    }


def test_run_home_edge_diagnostic_writes_public_artifact(monkeypatch, tmp_path: Path) -> None:
    remote = sample_remote()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, json.dumps(remote), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    artifact = run_home_edge_diagnostic(artifact_path=tmp_path / "diag.json")

    assert artifact["summary"]["route"]["status"] == "unchanged"
    assert artifact["summary"]["tailscale"]["status"] == "healthy"
    assert artifact["summary"]["modem"]["usb_id"] == "12d1:1506"
    assert artifact["summary"]["modem"]["sim_lock_state"] == "locked"
    assert artifact["summary"]["huawei_diag_profile"]["status"] == "ignored"
    assert json.loads((tmp_path / "diag.json").read_text(encoding="utf-8"))["schema"]


def test_operator_report_and_compact_status_are_safe() -> None:
    profile = load_home_edge_profile()
    artifact = {
        "node": {"node_id": profile.node_id},
        "summary": {
            "route": {"status": "unchanged"},
            "tailscale": {"status": "healthy"},
            "modem": {
                "status": "identified",
                "model": "E3372",
                "usb_id": "12d1:1506",
                "sim_lock_state": "locked",
                "connection_mode": "ModemManager_NCM_not_generic_ethernet",
            },
        },
    }

    report = build_operator_report(artifact)
    compact = compact_status(artifact)

    assert "locked" in report
    assert "pin" not in report.lower()
    assert compact["modem_lock_state"] == "locked"


def test_prepare_private_unlock_plan_does_not_execute_remote() -> None:
    plan = run_audited_home_edge_command("prepare_private_unlock_plan")

    assert plan["status"] == "prepared_not_executed"
    assert plan["requires_private_secret_route"] is True


def test_runner_dispatches_home_edge_read_only_diagnostic(monkeypatch) -> None:
    import core.home_edge.remote
    import scripts.runner_poll_github_tasks as runner

    def fake_run(command, *, artifact_path):
        assert command == "diagnostic"
        return {
            "node": {"node_id": "home-edge-01"},
            "summary": {
                "route": {"status": "unchanged"},
                "tailscale": {"status": "healthy"},
                "modem": {
                    "status": "identified",
                    "sim_lock_state": "locked",
                    "connection_mode": "ModemManager_NCM_not_generic_ethernet",
                },
            },
        }

    monkeypatch.setattr(core.home_edge.remote, "run_audited_home_edge_command", fake_run)

    report = runner.dispatch_runtime_maintenance_task(
        runner.HOME_EDGE_01_READ_ONLY_DIAGNOSTIC,
        workdir=".",
    )

    assert "DONE: Runner host maintenance task completed." in report
    assert "maintenance_task_id=home_edge_01_read_only_diagnostic" in report
    assert "modem_lock_state=locked" in report
    assert "success_criteria=met" in report

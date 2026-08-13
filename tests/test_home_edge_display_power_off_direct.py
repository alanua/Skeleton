from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV


SIGNER = Path(__file__).resolve().parents[1] / "scripts/home_edge_display_power_off_signer.py"
SECRET = "display-off-private-secret"


def authority_body() -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            "Maintenance Task ID: home_edge_01_display_power_off_v1",
            "Risk: yellow",
            "Target Node: home-edge-01",
            "Operator Approval: EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR",
            "Privacy Boundary: PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS",
        )
    )


def test_signer_rejects_argv_before_credential_read() -> None:
    completed = subprocess.run(
        [sys.executable, str(SIGNER), "--unexpected"],
        input=json.dumps({"authority": {}}),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={},
    )

    assert completed.returncode == 2
    assert "argv not supported" in completed.stderr


def test_signer_rejects_malformed_stdin_before_credential_read() -> None:
    completed = subprocess.run(
        [sys.executable, str(SIGNER)],
        input="{not-json",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={},
    )

    assert completed.returncode == 2
    assert "invalid stdin" in completed.stderr


def test_signer_rejects_altered_authority_before_credential_read() -> None:
    payload = {
        "authority": {
            "Mode": "RUNTIME_MAINTENANCE_TASK",
            "Maintenance Task ID": "home_edge_01_display_power_on_v1",
            "Risk": "yellow",
            "Target Node": "home-edge-01",
            "Operator Approval": "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR",
            "Privacy Boundary": "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS",
        }
    }
    completed = subprocess.run(
        [sys.executable, str(SIGNER)],
        input=json.dumps(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={},
    )

    assert completed.returncode == 2
    assert "authority mismatch" in completed.stderr


def test_signer_accepts_literal_authority_and_signs_fixed_request() -> None:
    authority = {}
    for line in authority_body().splitlines():
        key, value = line.split(": ", 1)
        authority[key] = value
    completed = subprocess.run(
        [sys.executable, str(SIGNER)],
        input=json.dumps({"authority": authority}),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={EXEC_HMAC_SECRET_ENV: SECRET},
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    request = envelope["request"]
    assert request["node_id"] == "home-edge-01"
    assert request["execution_lane"] == "privileged_mutation"
    assert request["run_as"] == "root"
    assert request["mode"] == "script"
    assert request["argv"] == []
    assert request["signature"].startswith("sha256=")

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.home_edge.esp_lab import build_public_receipt
from core.home_edge import esp_lab_activation as activation


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_home_edge_esp_lab.sh"


def _packet(**updates: Any) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "schema": activation.ACTIVATION_SCHEMA,
        "operation_id": activation.TASK_ID,
        "node_id": "media-pc",
        "endpoint_kind": "home_edge_local_linux",
        "adapter_kind": "linux_tty",
        "operation": "identify_chip",
        "device_ref": "/dev/ttyUSB0",
        "timeout_seconds": 5,
        "idempotency_key": "esp-stage1-test",
        "execution_mode": "plan",
        "private_salt": "synthetic-private-salt",
    }
    packet.update(updates)
    return packet


def _observation(job: dict[str, Any], *, aggregate_status: str = "planned_not_executed") -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.esp_lab.v1.observation",
        "generated_at": "2026-08-24T00:00:00+00:00",
        "control_plane_id": job["control_plane_id"],
        "node_id": job["node_id"],
        "endpoint_kind": job["endpoint_kind"],
        "adapter_kind": job["adapter_kind"],
        "operation": job["operation"],
        "execution_mode": job["execution_mode"],
        "adapter": "fake",
        "adapter_version": "test",
        "detected": {
            "family": "esp32-s3",
            "revision": None,
            "flash_manufacturer_id": None,
            "flash_device_id": None,
            "flash_size": None,
            "mac_observed": False,
        },
        "private_device_metadata": {"device_ref": job["device_ref"]},
        "salted_device_fingerprint": "a" * 64,
        "raw_bounded_evidence": [{"stdout": "COM42 /dev/ttyUSB0 private"}],
        "probes": [{"duration_ms": 0, "exit_code": None, "name": "read-mac", "reason": None, "status": aggregate_status}],
        "capability_matrix": [
            {"capability": "identify_chip", "state": "supported"},
            {"capability": "esp8266", "state": "limited"},
            {"capability": "partition_table_reads", "state": "deferred"},
            {"capability": "firmware_flashing", "state": "unavailable"},
        ],
    }


def test_local_activation_controller_calls_real_inspect_contract_and_public_receipt() -> None:
    calls: list[dict[str, Any]] = []

    def fake_inspect(job: dict[str, Any], **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        calls.append({"job": job, **kwargs})
        observation = _observation(job)
        return observation, build_public_receipt(observation)

    result = activation.execute_activation_packet(_packet(), inspect=fake_inspect)

    assert result["dispatch_proof"] == "local_controller"
    assert calls[0]["job"]["schema"] == "skeleton.home_edge.esp_lab.v1.job"
    assert calls[0]["job"]["device_ref"] == "/dev/ttyUSB0"
    assert calls[0]["execute_read_only"] is True
    assert result["receipt"] == build_public_receipt(result["observation"])
    assert "raw_bounded_evidence" in result["observation"]


def test_windows_activation_uses_signed_dispatcher_shape_without_static_receipt() -> None:
    calls: list[dict[str, Any]] = []
    packet = _packet(
        node_id="desk-win",
        endpoint_kind="windows_workstation_connector",
        adapter_kind="windows_com",
        device_ref=r"\\.\COM42",
        connector_url="https://127.0.0.1:9443/v1/esp-lab/jobs",
        connector_secret_file="/private/esp-lab.secret",
        connector_pinned_cert_sha256="b" * 64,
    )

    def fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        job = dict(kwargs["job"])
        job["schema"] = "skeleton.home_edge.esp_lab.v1.job"
        observation = _observation(job, aggregate_status="observed")
        return {"observation": observation, "receipt": build_public_receipt(observation)}

    result = activation.execute_activation_packet(
        packet,
        dispatch=fake_dispatch,
        secret_loader=lambda path: b"synthetic-secret-value",
    )

    assert result["dispatch_proof"] == "signed_windows_connector"
    assert calls[0]["secret"] == b"synthetic-secret-value"
    assert calls[0]["job"]["schema"] == "skeleton.home_edge.esp_lab.connector.v1.job"
    assert calls[0]["job"]["device_ref"] == "COM42"
    assert calls[0]["pinned_cert_sha256"] == "b" * 64
    assert result["receipt"] == build_public_receipt(result["observation"])


def test_status_lines_are_public_safe_aggregates_only() -> None:
    job = activation.build_stage1_job(_packet())
    result = {
        "dispatch_proof": "local_controller",
        "observation": _observation(job),
        "receipt": build_public_receipt(_observation(job)),
    }

    text = "\n".join(activation.receipt_status_lines(result))

    assert "aggregate=CAUTION" in text
    assert "private_device_evidence=private_runtime_artifact_only" in text
    assert "/dev/ttyUSB0" not in text
    assert "COM42" not in text
    assert "raw_bounded_evidence" not in text
    assert "private_salt" not in text


def test_issue_body_fenced_packet_is_authoritative() -> None:
    body = "Mode: RUNTIME_MAINTENANCE_TASK\n```task\n" + json.dumps(_packet(node_id="node-1")) + "\n```"

    assert activation.task_packet_from_issue_body(body)["node_id"] == "node-1"


def test_installer_accepts_contract_only_on_stdin_and_writes_public_config(tmp_path: Path) -> None:
    contract = {
        "schema": "skeleton.home_edge.esp_lab.activation_installer.v1",
        "operation_id": activation.TASK_ID,
        "node_id": "media-pc",
        "endpoint_kind": "home_edge_local_linux",
        "adapter_kind": "linux_tty",
        "default_execution_mode": "plan",
        "connector_secret_file": "/private/secret-is-not-public",
    }

    completed = subprocess.run(
        [str(INSTALLER), "--root", str(tmp_path)],
        input=json.dumps(contract),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    config = json.loads((tmp_path / "etc/skeleton/home-edge-esp-lab-stage1.json").read_text(encoding="utf-8"))
    assert config["operation_id"] == activation.TASK_ID
    assert "connector_secret_file" not in config
    assert "secret-is-not-public" not in completed.stdout
    assert (tmp_path / "usr/local/bin/skeleton-home-edge-esp-lab").exists()


def test_installer_rejects_secret_or_config_as_argv(tmp_path: Path) -> None:
    completed = subprocess.run(
        [str(INSTALLER), "--root", str(tmp_path), "--connector-secret", "SECRET"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 2
    assert "SECRET" not in completed.stderr

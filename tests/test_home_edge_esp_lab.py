from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.home_edge.esp_lab import (
    CommandResult,
    EspLabError,
    SerialObservationResult,
    build_public_receipt,
    build_read_only_commands,
    canonicalize_windows_com,
    cli,
    discover_serial_candidates,
    discover_windows_serial_candidates,
    inspect_job,
    normalize_family,
    sanitized_child_env,
    validate_command,
    validate_device_path,
    validate_job,
    validate_output_target,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    adapter_name = "fake.esptool"
    adapter_version = "test"

    def __init__(self, results: list[CommandResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(self, argv: list[str], **kwargs: Any) -> CommandResult:
        self.calls.append((argv, kwargs))
        return self.results.pop(0)


class FakeSerialAdapter:
    adapter_name = "fake.serial"
    adapter_version = "test"

    def __init__(self, result: SerialObservationResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def observe(self, device_path: str, **kwargs: Any) -> SerialObservationResult:
        self.calls.append({"device_path": device_path, **kwargs})
        return self.result


class FakeRegistry:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def serial_comm_values(self) -> dict[str, str]:
        return dict(self.values)


def linux_job(operation: str = "identify_chip") -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.esp_lab.v1.job",
        "control_plane_id": "home-edge",
        "node_id": "media-pc",
        "endpoint_kind": "home_edge_local_linux",
        "adapter_kind": "linux_tty",
        "operation": operation,
        "device_ref": "/dev/ttyUSB0",
        "timeout_seconds": 5,
        "idempotency_key": "idem-linux-1",
        "execution_mode": "plan",
        "private_salt": "synthetic-private-salt",
    }


def windows_job(operation: str = "identify_chip") -> dict[str, Any]:
    job = linux_job(operation)
    job.update(
        {
            "node_id": "desk-win",
            "endpoint_kind": "windows_workstation_connector",
            "adapter_kind": "windows_com",
            "device_ref": r"\\.\COM42",
            "idempotency_key": "idem-win-1",
        }
    )
    return job


def test_schemas_reject_unknown_fields() -> None:
    for name in (
        "home_edge_esp_lab_job.schema.json",
        "home_edge_esp_lab_observation.schema.json",
        "home_edge_esp_lab_receipt.schema.json",
        "home_edge_esp_lab_connector_job.schema.json",
        "home_edge_esp_lab_connector_receipt.schema.json",
    ):
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
    bad = linux_job()
    bad["extra"] = True
    with pytest.raises(EspLabError, match="unknown job field"):
        validate_job(bad)


def test_linux_tty_and_windows_com_validation() -> None:
    assert validate_device_path("/dev/ttyUSB0") == "/dev/ttyUSB0"
    assert validate_device_path("/dev/ttyACM12") == "/dev/ttyACM12"
    assert canonicalize_windows_com("COM1") == "COM1"
    assert canonicalize_windows_com("com256") == "COM256"
    assert canonicalize_windows_com(r"\\.\COM42") == "COM42"
    for device in ["COM0", "COM257", "COM1 ", r"\\?\COM1", r"\\server\pipe", "COM1&calc", "COM1/../COM2"]:
        with pytest.raises(EspLabError):
            canonicalize_windows_com(device)


def test_fake_winreg_discovery_never_opens_port() -> None:
    candidates = discover_windows_serial_candidates(FakeRegistry({r"\Device\Serial0": "COM3", "bad": "LPT1"}))
    assert candidates == [
        {
            "adapter_kind": "windows_com",
            "device_ref": "COM3",
            "endpoint_kind": "windows_workstation_connector",
            "registry_value_name_hash": candidates[0]["registry_value_name_hash"],
        }
    ]


def test_supported_family_normalization_and_esp8266_limited_state() -> None:
    assert normalize_family("ESP32-S3") == "esp32-s3"
    assert normalize_family("ESP8266EX") == "esp8266-limited"
    job = linux_job()
    job["execution_mode"] = "read_only"
    observation, receipt = inspect_job(
        job,
        execute_read_only=True,
        adapter=FakeAdapter([CommandResult(status="observed", stdout=b"Chip is ESP8266EX\nMAC: 334455667788\n", exit_code=0)]),
        executable_finder=lambda _: "/usr/bin/esptool",
        generated_at="2026-07-09T00:00:00+00:00",
    )
    assert observation["detected"]["family"] == "esp8266-limited"
    assert any(item == {"capability": "esp8266", "state": "limited"} for item in observation["capability_matrix"])
    assert receipt["detected_family"] == "esp8266-limited"


def test_exact_command_allowlist_and_ordering_for_both_endpoints() -> None:
    assert build_read_only_commands(linux_job("identify_chip")) == [["esptool", "--port", "/dev/ttyUSB0", "read-mac"]]
    assert build_read_only_commands(linux_job("inspect_flash_identity")) == [["esptool", "--port", "/dev/ttyUSB0", "flash-id"]]
    assert build_read_only_commands(linux_job("observe_serial_bounded")) == []
    assert build_read_only_commands(windows_job("identify_chip"), esptool=["python.exe", "-m", "esptool"]) == [
        ["python.exe", "-m", "esptool", "--port", "COM42", "read-mac"]
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["esptool", "--port", "/dev/ttyUSB0", "write-flash"],
        ["esptool", "--port", "/dev/ttyUSB0", "erase-flash"],
        ["esptool", "--port", "/dev/ttyUSB0", "erase-region"],
        ["esptool", "--port", "/dev/ttyUSB0", "write-flash-status"],
        ["esptool", "--port", "/dev/ttyUSB0", "write-mem"],
        ["esptool", "--port", "/dev/ttyUSB0", "load-ram"],
        ["esptool", "--port", "/dev/ttyUSB0", "read-flash"],
        ["esptool", "--port", "/dev/ttyUSB0", "dump-mem"],
        ["esptool", "--port", "/dev/ttyUSB0", "verify-flash"],
        ["esptool", "--port", "/dev/ttyUSB0", "merge-bin"],
        ["esptool", "--port", "/dev/ttyUSB0", "read-mac", "--force"],
        ["esptool", "--port", "/dev/ttyUSB0", "flash-id", "--encrypt"],
        ["esptool", "--port", "/dev/ttyUSB0", "flash-id", "--erase-all"],
        ["esptool", "@/tmp/args", "--port", "/dev/ttyUSB0", "read-mac"],
        ["cmd.exe", "/c", "esptool", "--port", "COM1", "read-mac"],
        ["powershell.exe", "-c", "esptool --port COM1 read-mac"],
    ],
)
def test_forbidden_commands_rejected_before_adapter_call(argv: list[str]) -> None:
    with pytest.raises(EspLabError):
        validate_command(argv)


def test_shell_never_enabled_and_child_environment_sanitized() -> None:
    fake = FakeAdapter([CommandResult(status="observed", stdout=b"Chip is ESP32-C3\nMAC: 112233445566\n", exit_code=0)])
    job = linux_job()
    job["execution_mode"] = "read_only"
    inspect_job(job, execute_read_only=True, adapter=fake, executable_finder=lambda _: "/usr/bin/esptool")
    assert fake.calls[0][0] == ["/usr/bin/esptool", "--port", "/dev/ttyUSB0", "read-mac"]
    assert fake.calls[0][1]["env"] == sanitized_child_env()
    assert "HOME" not in fake.calls[0][1]["env"]
    assert "shell" not in fake.calls[0][1]


def test_both_startup_and_request_authorization_required_for_real_read_only() -> None:
    fake = FakeAdapter([CommandResult(status="observed", stdout=b"Chip is ESP32\n", exit_code=0)])
    plan = windows_job()
    inspect_job(plan, execute_read_only=True, adapter=fake, executable_finder=lambda _: "esptool.exe")
    assert fake.calls == []
    read = windows_job()
    read["execution_mode"] = "read_only"
    inspect_job(read, execute_read_only=True, adapter=fake, executable_finder=lambda _: "esptool.exe", esptool_command="esptool.exe")
    assert fake.calls[0][0] == ["esptool.exe", "--port", "COM42", "read-mac"]


def test_raw_private_values_never_appear_in_public_receipt_or_exception_text() -> None:
    fake = FakeAdapter([CommandResult(status="observed", stdout=b"Chip is ESP32\nMAC: aa:bb:cc:dd:ee:ff\n", stderr=b"serial SECRETUSB123 host user 192.168.1.2\n", exit_code=0)])
    job = windows_job()
    job["execution_mode"] = "read_only"
    observation, receipt = inspect_job(job, execute_read_only=True, adapter=fake, executable_finder=lambda _: "esptool.exe", esptool_command="esptool.exe")
    public = json.dumps(receipt, sort_keys=True)
    for token in ("aa:bb:cc:dd:ee:ff", "SECRETUSB123", "COM42", "/dev/ttyUSB0", "192.168.1.2", "desk-win"):
        assert token not in public
    assert "aa:bb:cc:dd:ee:ff" not in json.dumps(observation)
    with pytest.raises(EspLabError) as excinfo:
        validate_device_path("/dev/ttyUSB0;cat /etc/passwd")
    assert "ttyUSB0" not in str(excinfo.value)


def test_bounded_stdout_stderr_and_serial_observation_truncation() -> None:
    job = linux_job()
    job["execution_mode"] = "read_only"
    fake = FakeAdapter([CommandResult(status="failed", stdout=b"A" * 5000, stderr=b"B" * 5000, exit_code=2, reason="nonzero_exit")])
    observation, _ = inspect_job(job, execute_read_only=True, adapter=fake, executable_finder=lambda _: "/usr/bin/esptool")
    evidence = observation["raw_bounded_evidence"][0]
    assert len(evidence["stdout"]) == 4096
    assert len(evidence["stderr"]) == 4096
    serial_job = linux_job("observe_serial_bounded")
    serial_job["baud"] = 9600
    serial_job["max_bytes"] = 5
    serial_job["execution_mode"] = "read_only"
    serial = FakeSerialAdapter(SerialObservationResult(status="observed", data=b"one\ntwo\nthree", terminal_status="timeout"))
    observation, receipt = inspect_job(serial_job, execute_read_only=True, serial_adapter=serial)
    assert observation["raw_bounded_evidence"][0]["data"] == "one\nt"
    assert observation["raw_bounded_evidence"][0]["truncated"] is True
    assert "one" not in json.dumps(receipt)
    assert serial.calls[0]["baud"] == 9600


@pytest.mark.parametrize("device_path", ["/dev/ttyUSB0/../ttyUSB1", "/dev/ttyS0", "/tmp/ttyUSB0", "/dev/ttyUSB0 bad", "/dev/ttyUSB0;rm -rf /", "/dev/serial/by-id/synthetic"])
def test_malicious_device_paths_rejected(device_path: str) -> None:
    with pytest.raises(EspLabError):
        validate_device_path(device_path)


def test_discovery_reads_sysfs_only_and_filters_candidates(tmp_path: Path) -> None:
    tty = tmp_path / "ttyUSB0"
    tty.mkdir()
    (tty / "product").write_text("ESP32 USB Bridge", encoding="utf-8")
    (tty / "idVendor").write_text("303a", encoding="utf-8")
    (tty / "idProduct").write_text("1001", encoding="utf-8")
    (tmp_path / "ttyS0").mkdir()
    assert discover_serial_candidates(tmp_path) == [
        {
            "adapter_kind": "linux_tty",
            "device_ref": "/dev/ttyUSB0",
            "driver": None,
            "endpoint_kind": "home_edge_local_linux",
            "pid": "1001",
            "product": "ESP32 USB Bridge",
            "vid": "303a",
        }
    ]


def test_missing_esptool_returns_unsupported_dependency() -> None:
    job = linux_job()
    job["execution_mode"] = "read_only"
    observation, receipt = inspect_job(job, execute_read_only=True, adapter=FakeAdapter([]), executable_finder=lambda _: None)
    assert observation["adapter"] == "unsupported_dependency"
    assert observation["probes"][0]["status"] == "unsupported_dependency"
    assert receipt["aggregate"] == "CAUTION"


def test_default_cli_performs_no_subprocess_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(linux_job()), encoding="utf-8")
    private_out = tmp_path / "private.json"
    receipt_out = tmp_path / "receipt.json"

    def fail_run(*_: Any, **__: Any) -> None:
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert cli(["inspect", "--job", str(job_path), "--private-out", str(private_out), "--receipt-out", str(receipt_out), "--allowed-root", str(tmp_path)]) == 0
    written = json.loads(private_out.read_text(encoding="utf-8"))
    assert written["probes"][0]["status"] == "planned_not_executed"


def test_unsafe_output_paths_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    with pytest.raises(EspLabError):
        validate_output_target(outside, allowed_roots=[tmp_path])
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "target.json")
    with pytest.raises(EspLabError):
        validate_output_target(link, allowed_roots=[tmp_path])
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(EspLabError):
        validate_output_target(directory, allowed_roots=[tmp_path])


def test_deterministic_public_receipt() -> None:
    job = linux_job("inspect_flash_identity")
    job["execution_mode"] = "read_only"
    observation, receipt = inspect_job(job, execute_read_only=True, adapter=FakeAdapter([CommandResult(status="observed", stdout=b"Manufacturer: 20\nDevice: 4016\nDetected flash size: 4MB\n", exit_code=0)]), executable_finder=lambda _: "/usr/bin/esptool", generated_at="2026-07-09T00:00:00+00:00")
    assert json.dumps(receipt, sort_keys=True) == json.dumps(build_public_receipt(observation), sort_keys=True)
    assert list(receipt) == sorted(receipt)


def test_no_network_ssh_home_edge_or_package_installation_references() -> None:
    source = (ROOT / "core" / "home_edge" / "esp_lab.py").read_text(encoding="utf-8")
    forbidden = ["ssh ", "pip install", "apt install", "requests.", "urllib.request", "CAPABILITY_REGISTRY"]
    assert all(token not in source for token in forbidden)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.home_edge.firmware_action import (
    DEVICE_TARGET,
    REMOTE_TMP_PATH,
    FirmwareTransferRequest,
    HomeEdgeFirmwareAction,
    HomeEdgeFirmwareActionError,
)
from core.home_edge.profile import HomeEdgeProfile, synthetic_profile_mapping


def _profile() -> HomeEdgeProfile:
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["controller"]["host"] = "runtime-controller"
    data["controller"]["tailscale_ip"] = "100.64.10.63"
    data["ssh"]["target_user"] = "runtime-user"
    return HomeEdgeProfile.from_mapping(data, source="local_profile")


def _request(tmp_path: Path) -> FirmwareTransferRequest:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"firmware-image")
    return FirmwareTransferRequest(
        firmware_path=firmware,
        byte_size=len(b"firmware-image"),
        sha256="ec4d577ee88cfc72af6589309da85d67feaf32ffabc78e5e705d77c2a5712036",
    )


def test_synthetic_profile_blocks_before_transfer(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    action = HomeEdgeFirmwareAction(
        profile_loader=lambda: HomeEdgeProfile.from_mapping(synthetic_profile_mapping()),
        run_command=lambda args, stdin, timeout: (calls.append(args) or (0, "")),
    )

    with pytest.raises(HomeEdgeFirmwareActionError) as exc:
        action.execute(_request(tmp_path))

    assert exc.value.reason_code == "HOME_EDGE_PROFILE_NOT_PRIVATE"
    assert calls == []


def test_missing_secret_files_block_before_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE", raising=False)
    monkeypatch.delenv("SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE", raising=False)
    action = HomeEdgeFirmwareAction(profile_loader=_profile)

    with pytest.raises(HomeEdgeFirmwareActionError) as exc:
        action.execute(_request(tmp_path))

    assert exc.value.reason_code == "HOME_EDGE_SECRET_FILE_MISSING"


def test_strict_scp_and_ssh_are_fixed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("identity", encoding="utf-8")
    known_hosts.write_text("host key", encoding="utf-8")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE", str(identity))
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE", str(known_hosts))
    calls: list[tuple[list[str], str | bytes | None, int]] = []

    def fake_run(args: list[str], stdin: str | bytes | None, timeout: int) -> tuple[int, str]:
        calls.append((args, stdin, timeout))
        if args[0] == "scp":
            return 0, ""
        request = _request(tmp_path)
        return 0, json.dumps(
            {
                "target": DEVICE_TARGET,
                "sha256": request.sha256,
                "byte_size": request.byte_size,
                "preflight_state": "ok",
                "ota_http_class": "success",
                "ota_http_status": 200,
                "reboot_observed": True,
                "effects": {"CY Anemone": True, "CY Tidal Bloom": True},
                "final_status": "DONE",
            }
        )

    receipt = HomeEdgeFirmwareAction(profile_loader=_profile, run_command=fake_run).execute(
        _request(tmp_path)
    )

    scp_args = calls[0][0]
    ssh_args, ssh_stdin, _timeout = calls[1]
    assert scp_args[:6] == [
        "scp",
        "-q",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    assert f"UserKnownHostsFile={known_hosts}" in scp_args
    assert str(identity) in scp_args
    assert scp_args[-1] == f"runtime-user@100.64.10.74:{REMOTE_TMP_PATH}"
    assert ssh_args[-2:] == ["python3", "-"]
    assert 'TARGET = "192.168.1.164"' in str(ssh_stdin)
    assert '"/update"' in str(ssh_stdin)
    assert receipt["relay"] == "home-edge-01"
    assert receipt["target"] == DEVICE_TARGET
    assert receipt["no_direct_controller_lan_ota"] is True
    assert receipt["effects"] == {"CY Anemone": True, "CY Tidal Bloom": True}


def test_remote_unverified_blocks_without_private_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("identity", encoding="utf-8")
    known_hosts.write_text("host key", encoding="utf-8")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE", str(identity))
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE", str(known_hosts))

    def fake_run(args: list[str], stdin: str | bytes | None, timeout: int) -> tuple[int, str]:
        if args[0] == "scp":
            return 0, ""
        request = _request(tmp_path)
        return 0, json.dumps(
            {
                "target": DEVICE_TARGET,
                "sha256": request.sha256,
                "byte_size": request.byte_size,
                "effects": {"CY Anemone": True, "CY Tidal Bloom": False},
                "final_status": "OTA_UNVERIFIED",
                "raw_config": "private",
            }
        )

    with pytest.raises(HomeEdgeFirmwareActionError) as exc:
        HomeEdgeFirmwareAction(profile_loader=_profile, run_command=fake_run).execute(
            _request(tmp_path)
        )

    assert exc.value.reason_code == "OTA_UNVERIFIED"
    assert "runtime-user" not in str(exc.value)
    assert str(identity) not in str(exc.value)

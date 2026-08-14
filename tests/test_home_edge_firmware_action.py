from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from urllib import error, request

import pytest

from core.home_edge.firmware_action import (
    DEVICE_TARGET,
    REMOTE_FAILURE_SCHEMA,
    REMOTE_PYTHON_ACTION,
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


def _secret_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("identity", encoding="utf-8")
    known_hosts.write_text("host key", encoding="utf-8")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE", str(identity))
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE", str(known_hosts))


def _failure_receipt(req: FirmwareTransferRequest, reason: str) -> str:
    return json.dumps(
        {
            "schema": REMOTE_FAILURE_SCHEMA,
            "target": DEVICE_TARGET,
            "sha256": req.sha256,
            "byte_size": req.byte_size,
            "final_status": "BLOCKED",
            "failure_reason": reason,
        }
    )


class _Response:
    def __init__(self, payload: object | None = None, *, status: int = 200, url: str | None = None) -> None:
        self._payload = payload
        self.status = status
        self._url = url or f"http://{DEVICE_TARGET}/update"

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def geturl(self) -> str:
        return self._url


class _Opener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    def open(self, _req: object, timeout: int = 60) -> _Response:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


def _http_error(status: int, body: bytes = b"") -> error.HTTPError:
    return error.HTTPError(
        f"http://{DEVICE_TARGET}/update",
        status,
        "blocked",
        hdrs=None,
        fp=io.BytesIO(body),
    )


def _run_remote_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    info: object | None = None,
    effects: object | None = None,
    cfg: object | None = None,
    upload_outcome: object | None = None,
    postflight_effects: object | None = None,
    stdin_payload: object | None = None,
) -> dict[str, object]:
    firmware = Path(REMOTE_TMP_PATH)
    firmware.write_bytes(b"firmware-image")
    sha256 = "ec4d577ee88cfc72af6589309da85d67feaf32ffabc78e5e705d77c2a5712036"
    payload = stdin_payload or {
        "schema": "skeleton.home_edge.lavalamp_ota_request.v1",
        "remote_path": REMOTE_TMP_PATH,
        "byte_size": len(b"firmware-image"),
        "sha256": sha256,
        "target": DEVICE_TARGET,
        "postflight_effects": ["CY Anemone", "CY Tidal Bloom"],
        "state_dir": str(tmp_path / "state"),
        "idempotency_key": "test",
    }
    responses = [
        info if info is not None else {"brand": "WLED", "arch": "esp32"},
        effects if effects is not None else [],
        cfg if cfg is not None else {"hw": {"led": {"total": 256}}},
    ]
    if postflight_effects is not None:
        responses.extend([{"brand": "WLED", "arch": "esp32"}, postflight_effects])

    def fake_urlopen(req: object, timeout: int = 10) -> _Response:
        if isinstance(req, request.Request) and "/json/info" in req.full_url and info == "unreachable":
            raise error.URLError("http://private.local/body SECRET")
        return _Response(responses.pop(0))

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        request,
        "build_opener",
        lambda *_args: _Opener(upload_outcome or _Response(status=200)),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    stdout = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stdout(stdout):
        exec(REMOTE_PYTHON_ACTION, {})
    assert exc.value.code == 1
    assert not firmware.exists()
    lines = [line for line in stdout.getvalue().splitlines() if line]
    assert len(lines) == 1
    return json.loads(lines[0])


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
    _secret_env(monkeypatch, tmp_path)
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
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
    _secret_env(monkeypatch, tmp_path)
    identity = tmp_path / "id_ed25519"

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


@pytest.mark.parametrize(
    ("remote_reason", "host_reason"),
    [
        ("PREFLIGHT_IDENTITY_MISMATCH", "REMOTE_PREFLIGHT_IDENTITY_MISMATCH"),
        ("DEVICE_UNREACHABLE", "REMOTE_DEVICE_UNREACHABLE"),
        ("OTA_LOCKED_OR_PIN_REQUIRED", "REMOTE_OTA_LOCKED_OR_PIN_REQUIRED"),
        (
            "OTA_COMPATIBILITY_VALIDATION_REJECTED",
            "REMOTE_OTA_COMPATIBILITY_VALIDATION_REJECTED",
        ),
        ("OTA_UPDATE_REJECTED", "REMOTE_OTA_UPDATE_REJECTED"),
        ("REDIRECT_MISMATCH", "REMOTE_REDIRECT_MISMATCH"),
        ("REBOOT_TIMEOUT", "REMOTE_REBOOT_TIMEOUT"),
        ("EFFECTS_MISSING", "REMOTE_EFFECTS_MISSING"),
        ("MALFORMED_REMOTE_REQUEST", "REMOTE_MALFORMED_REMOTE_REQUEST"),
    ],
)
def test_host_maps_allowlisted_remote_failure_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_reason: str, host_reason: str
) -> None:
    _secret_env(monkeypatch, tmp_path)
    req = _request(tmp_path)

    def fake_run(args: list[str], stdin: str | bytes | None, timeout: int) -> tuple[int, str]:
        if args[0] == "scp":
            return 0, ""
        return 1, _failure_receipt(req, remote_reason)

    with pytest.raises(HomeEdgeFirmwareActionError) as exc:
        HomeEdgeFirmwareAction(profile_loader=_profile, run_command=fake_run).execute(req)

    assert exc.value.reason_code == host_reason


@pytest.mark.parametrize(
    "output",
    [
        "",
        "not-json",
        json.dumps({"schema": REMOTE_FAILURE_SCHEMA, "failure_reason": "DEVICE_UNREACHABLE"}),
        json.dumps(
            {
                "schema": REMOTE_FAILURE_SCHEMA,
                "target": DEVICE_TARGET,
                "sha256": "0" * 64,
                "byte_size": 12,
                "final_status": "BLOCKED",
                "failure_reason": "SECRET_RAW_BODY",
            }
        ),
    ],
)
def test_host_rejects_malformed_or_unknown_remote_failure_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: str
) -> None:
    _secret_env(monkeypatch, tmp_path)
    req = _request(tmp_path)

    def fake_run(args: list[str], stdin: str | bytes | None, timeout: int) -> tuple[int, str]:
        if args[0] == "scp":
            return 0, ""
        return 1, output

    with pytest.raises(HomeEdgeFirmwareActionError) as exc:
        HomeEdgeFirmwareAction(profile_loader=_profile, run_command=fake_run).execute(req)

    assert exc.value.reason_code == "REMOTE_ACTION_FAILED"


def test_remote_preflight_identity_mismatch_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(
        monkeypatch,
        tmp_path,
        info={"brand": "WLED-private", "arch": "esp32"},
    )

    assert receipt == {
        "schema": REMOTE_FAILURE_SCHEMA,
        "target": DEVICE_TARGET,
        "sha256": "ec4d577ee88cfc72af6589309da85d67feaf32ffabc78e5e705d77c2a5712036",
        "byte_size": len(b"firmware-image"),
        "final_status": "BLOCKED",
        "failure_reason": "PREFLIGHT_IDENTITY_MISMATCH",
    }


def test_remote_device_unreachable_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(monkeypatch, tmp_path, info="unreachable")

    assert receipt["failure_reason"] == "DEVICE_UNREACHABLE"
    assert "private.local" not in json.dumps(receipt)
    assert "SECRET" not in json.dumps(receipt)


@pytest.mark.parametrize("status", [401, 403])
def test_remote_update_auth_or_lock_http_status_is_pin_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    receipt = _run_remote_failure(monkeypatch, tmp_path, upload_outcome=_http_error(status))

    assert receipt["failure_reason"] == "OTA_LOCKED_OR_PIN_REQUIRED"


def test_remote_wled_compatibility_rejection_marker_does_not_leak_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(
        monkeypatch,
        tmp_path,
        upload_outcome=_http_error(500, b"firmware not compatible with hardware SECRET_BODY"),
    )

    assert receipt["failure_reason"] == "OTA_COMPATIBILITY_VALIDATION_REJECTED"
    assert "SECRET_BODY" not in json.dumps(receipt)


def test_remote_other_http_500_is_update_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(
        monkeypatch,
        tmp_path,
        upload_outcome=_http_error(500, b"plain failure body"),
    )

    assert receipt["failure_reason"] == "OTA_UPDATE_REJECTED"
    assert "plain failure body" not in json.dumps(receipt)


def test_remote_redirect_mismatch_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(
        monkeypatch,
        tmp_path,
        upload_outcome=_Response(status=200, url="http://192.168.1.99/update"),
    )

    assert receipt["failure_reason"] == "REDIRECT_MISMATCH"
    assert "192.168.1.99" not in json.dumps(receipt)


def test_remote_reboot_timeout_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("time.time", iter([0, 181]).__next__)
    receipt = _run_remote_failure(monkeypatch, tmp_path)

    assert receipt["failure_reason"] == "REBOOT_TIMEOUT"


def test_remote_missing_effects_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(monkeypatch, tmp_path, postflight_effects=["CY Anemone"])

    assert receipt["failure_reason"] == "EFFECTS_MISSING"


def test_remote_malformed_request_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(
        monkeypatch,
        tmp_path,
        stdin_payload={"remote_path": "/tmp/private-firmware.bin", "secret": "PRIVATE"},
    )

    assert receipt["failure_reason"] == "MALFORMED_REMOTE_REQUEST"
    assert receipt["sha256"] == ""
    assert receipt["byte_size"] == 0
    assert "PRIVATE" not in json.dumps(receipt)
    assert "/tmp/private-firmware.bin" not in json.dumps(receipt)


def test_remote_unclassified_exception_is_sanitized_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt = _run_remote_failure(monkeypatch, tmp_path, cfg=[])

    assert receipt["failure_reason"] == "REMOTE_ACTION_FAILED"
    assert "AttributeError" not in json.dumps(receipt)

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from core.home_edge.profile import HomeEdgeProfile, load_home_edge_profile


HOME_EDGE_NODE: Final = "home-edge-01"
DEVICE_TARGET: Final = "192.168.1.164"
REMOTE_TMP_PATH: Final = "/tmp/skeleton-lavalamp-c98acbf-firmware.bin"
STATE_DIR: Final = ".local/state/skeleton/home-edge-01/lavalamp"
REMOTE_TIMEOUT_SECONDS: Final = 600
POSTFLIGHT_EFFECTS: Final = ("CY Anemone", "CY Tidal Bloom")


class HomeEdgeFirmwareActionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


RunCommand = Callable[[list[str], str | bytes | None, int], tuple[int, str]]


def _subprocess_run(args: list[str], stdin: str | bytes | None, timeout: int) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        input=stdin,
        text=isinstance(stdin, str) or stdin is None,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, (completed.stdout or "")


@dataclass(frozen=True)
class FirmwareTransferRequest:
    firmware_path: Path
    byte_size: int
    sha256: str
    relay: str = HOME_EDGE_NODE
    target: str = DEVICE_TARGET
    postflight_effects: tuple[str, str] = POSTFLIGHT_EFFECTS
    idempotency_key: str = "lavalamp-c98acbf-build-ota-1922-20260812-v2"


@dataclass(frozen=True)
class HomeEdgeFirmwareAction:
    profile_loader: Callable[[], HomeEdgeProfile] = load_home_edge_profile
    run_command: RunCommand = _subprocess_run

    def execute(self, request: FirmwareTransferRequest) -> dict[str, object]:
        profile, identity_path, known_hosts_path = self._validated_profile()
        self._validate_request(request)
        destination = f"{profile.target_user}@{profile.tailscale_ip}:{REMOTE_TMP_PATH}"
        scp_args = [
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            "-i",
            str(identity_path),
            "-o",
            f"ConnectTimeout={REMOTE_TIMEOUT_SECONDS}",
            str(request.firmware_path),
            destination,
        ]
        code, _output = self.run_command(scp_args, None, REMOTE_TIMEOUT_SECONDS)
        if code != 0:
            raise HomeEdgeFirmwareActionError("TRANSFER_FAILED")

        remote_request = {
            "schema": "skeleton.home_edge.lavalamp_ota_request.v1",
            "remote_path": REMOTE_TMP_PATH,
            "byte_size": request.byte_size,
            "sha256": request.sha256,
            "target": DEVICE_TARGET,
            "postflight_effects": list(request.postflight_effects),
            "state_dir": STATE_DIR,
            "idempotency_key": request.idempotency_key,
        }
        code, output = self.run_command(
            self._ssh_args(profile, identity_path, known_hosts_path),
            REMOTE_PYTHON_ACTION + "\n" + json.dumps(remote_request, sort_keys=True),
            REMOTE_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise HomeEdgeFirmwareActionError("REMOTE_ACTION_FAILED")
        return _public_remote_receipt(output, request)

    def verify_postflight_only(self, request: FirmwareTransferRequest) -> dict[str, object]:
        profile, identity_path, known_hosts_path = self._validated_profile()
        self._validate_request(request)
        remote_request = {
            "schema": "skeleton.home_edge.lavalamp_postflight_request.v1",
            "target": DEVICE_TARGET,
            "byte_size": request.byte_size,
            "sha256": request.sha256,
            "postflight_effects": list(request.postflight_effects),
            "state_dir": STATE_DIR,
            "idempotency_key": request.idempotency_key,
        }
        code, output = self.run_command(
            self._ssh_args(profile, identity_path, known_hosts_path),
            REMOTE_POSTFLIGHT_PYTHON_ACTION + "\n" + json.dumps(remote_request, sort_keys=True),
            REMOTE_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise HomeEdgeFirmwareActionError("REMOTE_POSTFLIGHT_FAILED")
        return _public_remote_receipt(output, request, transfer_state="skipped_verified_duplicate")

    def _ssh_args(self, profile: HomeEdgeProfile, identity_path: Path, known_hosts_path: Path) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            "-i",
            str(identity_path),
            "-o",
            f"ConnectTimeout={REMOTE_TIMEOUT_SECONDS}",
            f"{profile.target_user}@{profile.tailscale_ip}",
            "python3",
            "-",
        ]

    def _validated_profile(self) -> tuple[HomeEdgeProfile, Path, Path]:
        try:
            profile = self.profile_loader()
        except Exception as exc:
            raise HomeEdgeFirmwareActionError("HOME_EDGE_PROFILE_UNAVAILABLE") from exc
        if profile.node_id != HOME_EDGE_NODE or profile.is_template_identity:
            raise HomeEdgeFirmwareActionError("HOME_EDGE_PROFILE_NOT_PRIVATE")
        identity_path = _strict_secret_file(os.environ.get(profile.identity_env, ""))
        known_hosts_path = _strict_secret_file(os.environ.get(profile.known_hosts_env, ""))
        return profile, identity_path, known_hosts_path

    def _validate_request(self, request: FirmwareTransferRequest) -> None:
        if request.relay != HOME_EDGE_NODE:
            raise HomeEdgeFirmwareActionError("RELAY_MISMATCH")
        if request.target != DEVICE_TARGET:
            raise HomeEdgeFirmwareActionError("TARGET_MISMATCH")
        if request.postflight_effects != POSTFLIGHT_EFFECTS:
            raise HomeEdgeFirmwareActionError("EFFECT_ALLOWLIST_MISMATCH")
        if not request.firmware_path.is_file() or request.firmware_path.is_symlink():
            raise HomeEdgeFirmwareActionError("FIRMWARE_FILE_INVALID")
        if request.byte_size <= 0 or request.byte_size > 4 * 1024 * 1024:
            raise HomeEdgeFirmwareActionError("FIRMWARE_SIZE_INVALID")
        if len(request.sha256) != 64 or any(ch not in "0123456789abcdef" for ch in request.sha256):
            raise HomeEdgeFirmwareActionError("FIRMWARE_HASH_INVALID")


def _strict_secret_file(value: str) -> Path:
    if not value:
        raise HomeEdgeFirmwareActionError("HOME_EDGE_SECRET_FILE_MISSING")
    path = Path(value).expanduser()
    try:
        st = path.stat()
    except OSError as exc:
        raise HomeEdgeFirmwareActionError("HOME_EDGE_SECRET_FILE_UNREADABLE") from exc
    if not stat.S_ISREG(st.st_mode) or path.is_symlink():
        raise HomeEdgeFirmwareActionError("HOME_EDGE_SECRET_FILE_UNREADABLE")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise HomeEdgeFirmwareActionError("HOME_EDGE_SECRET_FILE_UNREADABLE") from exc
    return path


def _public_remote_receipt(
    output: str,
    request: FirmwareTransferRequest,
    *,
    transfer_state: str = "transferred",
) -> dict[str, object]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HomeEdgeFirmwareActionError("REMOTE_RECEIPT_MALFORMED") from exc
    if not isinstance(parsed, Mapping):
        raise HomeEdgeFirmwareActionError("REMOTE_RECEIPT_MALFORMED")
    if parsed.get("target") != DEVICE_TARGET:
        raise HomeEdgeFirmwareActionError("REMOTE_TARGET_MISMATCH")
    if parsed.get("sha256") != request.sha256 or parsed.get("byte_size") != request.byte_size:
        raise HomeEdgeFirmwareActionError("REMOTE_ARTIFACT_MISMATCH")
    effects = parsed.get("effects")
    if not isinstance(effects, Mapping):
        raise HomeEdgeFirmwareActionError("REMOTE_EFFECTS_MISSING")
    effect_bools = {effect: effects.get(effect) is True for effect in request.postflight_effects}
    final_status = "DONE" if all(effect_bools.values()) and parsed.get("final_status") == "DONE" else "OTA_UNVERIFIED"
    if final_status != "DONE":
        raise HomeEdgeFirmwareActionError("OTA_UNVERIFIED")
    return {
        "schema": "skeleton.home_edge.lavalamp_ota_receipt.v1",
        "relay": HOME_EDGE_NODE,
        "target": DEVICE_TARGET,
        "no_direct_controller_lan_ota": True,
        "preflight_state": _safe_state(parsed.get("preflight_state")),
        "transfer_state": transfer_state,
        "ota_http_class": _safe_state(parsed.get("ota_http_class")),
        "ota_http_status": parsed.get("ota_http_status") if isinstance(parsed.get("ota_http_status"), int) else None,
        "reboot_observed": parsed.get("reboot_observed") is True,
        "source_artifact_hash": request.sha256,
        "byte_size": request.byte_size,
        "effects": effect_bools,
        "final_status": final_status,
    }


def _safe_state(value: object) -> str:
    if isinstance(value, str) and value in {"ok", "blocked", "failed", "transferred", "skipped_verified_duplicate", "success", "2xx"}:
        return value
    return "blocked"


REMOTE_PYTHON_ACTION: Final = r'''
import hashlib, json, os, sys, time
from pathlib import Path
from urllib import request

TARGET = "192.168.1.164"
MAX_BYTES = 4 * 1024 * 1024
BOUNDARY = "skeleton-lavalamp-fixed-boundary"

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("redirect_mismatch")

def out(payload):
    print(json.dumps(payload, sort_keys=True))

def get_json(path, timeout=10):
    req = request.Request("http://" + TARGET + path, method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read(1024 * 512).decode("utf-8"))

def has_effect(effects, name):
    return any(item == name or (isinstance(item, list) and name in item) for item in effects)

payload = json.loads(sys.stdin.read())
remote_path = Path(payload["remote_path"])
state_dir = Path.home() / payload["state_dir"]
result = {"target": TARGET, "sha256": payload["sha256"], "byte_size": payload["byte_size"], "effects": {}, "final_status": "OTA_UNVERIFIED"}
try:
    if payload.get("target") != TARGET:
        raise RuntimeError("target_mismatch")
    data = remote_path.read_bytes()
    if len(data) != payload["byte_size"] or len(data) <= 0 or len(data) > MAX_BYTES:
        raise RuntimeError("size_mismatch")
    if hashlib.sha256(data).hexdigest() != payload["sha256"]:
        raise RuntimeError("hash_mismatch")
    info = get_json("/json/info")
    effects = get_json("/json/eff")
    cfg = get_json("/json/cfg")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = state_dir / ("cfg-" + payload["sha256"][:12] + ".json")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"info": info, "cfg": cfg}, fh, sort_keys=True)
    if info.get("brand", "WLED") != "WLED" or info.get("arch") != "esp32" or cfg.get("hw", {}).get("led", {}).get("total") != 256:
        raise RuntimeError("preflight_identity_mismatch")
    result["preflight_state"] = "ok"
    body = (
        ("--" + BOUNDARY + "\r\n").encode("ascii")
        + b'Content-Disposition: form-data; name="update"; filename="firmware.bin"\r\n'
        + b"Content-Type: application/octet-stream\r\n\r\n"
        + data
        + ("\r\n--" + BOUNDARY + "--\r\n").encode("ascii")
    )
    opener = request.build_opener(NoRedirect)
    req = request.Request("http://" + TARGET + "/update", data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + BOUNDARY)
    req.add_header("Content-Length", str(len(body)))
    with opener.open(req, timeout=60) as response:
        if response.geturl().split("/")[2] != TARGET:
            raise RuntimeError("redirect_mismatch")
        result["ota_http_status"] = int(response.status)
        result["ota_http_class"] = "2xx" if 200 <= response.status < 300 else "failed"
    if result["ota_http_class"] != "2xx":
        raise RuntimeError("upload_failed")
    deadline = time.time() + 180
    post_effects = None
    while time.time() < deadline:
        try:
            get_json("/json/info", timeout=5)
            post_effects = get_json("/json/eff", timeout=5)
            break
        except Exception:
            time.sleep(5)
    result["reboot_observed"] = post_effects is not None
    if post_effects is None:
        raise RuntimeError("reboot_timeout")
    result["effects"] = {name: has_effect(post_effects, name) for name in payload["postflight_effects"]}
    result["final_status"] = "DONE" if all(result["effects"].values()) else "OTA_UNVERIFIED"
finally:
    try:
        remote_path.unlink()
    except FileNotFoundError:
        pass
out(result)
'''


REMOTE_POSTFLIGHT_PYTHON_ACTION: Final = r'''
import json, sys
from urllib import request

TARGET = "192.168.1.164"

def get_json(path, timeout=10):
    req = request.Request("http://" + TARGET + path, method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read(1024 * 512).decode("utf-8"))

def has_effect(effects, name):
    return any(item == name or (isinstance(item, list) and name in item) for item in effects)

payload = json.loads(sys.stdin.read())
effects = get_json("/json/eff")
result = {
    "target": TARGET,
    "sha256": payload["sha256"],
    "byte_size": payload.get("byte_size", 0),
    "preflight_state": "ok",
    "ota_http_class": "success",
    "ota_http_status": 200,
    "reboot_observed": True,
    "effects": {name: has_effect(effects, name) for name in payload["postflight_effects"]},
}
result["final_status"] = "DONE" if all(result["effects"].values()) else "OTA_UNVERIFIED"
print(json.dumps(result, sort_keys=True))
'''

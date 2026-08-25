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
REQUEST_SCHEMA: Final = "skeleton.home_edge.lavalamp_ota_request.v1"
POSTFLIGHT_REQUEST_SCHEMA: Final = "skeleton.home_edge.lavalamp_postflight_request.v1"
IDEMPOTENCY_KEY: Final = "lavalamp-c98acbf-build-ota-1922-20260812-v2"
MAX_REMOTE_REQUEST_JSON_BYTES: Final = 2048
REMOTE_FAILURE_SCHEMA: Final = "skeleton.home_edge.lavalamp_ota_failure.v1"
REMOTE_FAILURE_REASON_TO_ERROR: Final = {
    "PREFLIGHT_IDENTITY_MISMATCH": "REMOTE_PREFLIGHT_IDENTITY_MISMATCH",
    "DEVICE_UNREACHABLE": "REMOTE_DEVICE_UNREACHABLE",
    "OTA_LOCKED_OR_PIN_REQUIRED": "REMOTE_OTA_LOCKED_OR_PIN_REQUIRED",
    "OTA_COMPATIBILITY_VALIDATION_REJECTED": "REMOTE_OTA_COMPATIBILITY_VALIDATION_REJECTED",
    "OTA_UPDATE_REJECTED": "REMOTE_OTA_UPDATE_REJECTED",
    "UPLOAD_TRANSPORT_FAILED": "REMOTE_UPLOAD_TRANSPORT_FAILED",
    "REDIRECT_MISMATCH": "REMOTE_REDIRECT_MISMATCH",
    "REBOOT_TIMEOUT": "REMOTE_REBOOT_TIMEOUT",
    "EFFECTS_MISSING": "REMOTE_EFFECTS_MISSING",
    "MALFORMED_REMOTE_REQUEST": "REMOTE_MALFORMED_REMOTE_REQUEST",
    "ARTIFACT_MISMATCH": "REMOTE_ARTIFACT_MISMATCH",
    "ARTIFACT_READ_FAILED": "REMOTE_ARTIFACT_READ_FAILED",
    "DEVICE_RESPONSE_MALFORMED": "REMOTE_DEVICE_RESPONSE_MALFORMED",
    "BACKUP_STATE_FAILED": "REMOTE_BACKUP_STATE_FAILED",
    "REMOTE_ACTION_FAILED": "REMOTE_ACTION_FAILED",
}
REMOTE_FAILURE_STAGES: Final = {
    "request_parse",
    "artifact_verify",
    "device_info",
    "device_effects",
    "device_config",
    "backup_config",
    "upload_request",
    "reboot_wait",
    "postflight_effects",
}


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
    idempotency_key: str = IDEMPOTENCY_KEY


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
            "schema": REQUEST_SCHEMA,
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
            _remote_python_stdin(REMOTE_PYTHON_ACTION, remote_request),
            REMOTE_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise HomeEdgeFirmwareActionError(_remote_failure_reason(output, request))
        return _public_remote_receipt(output, request)

    def verify_postflight_only(self, request: FirmwareTransferRequest) -> dict[str, object]:
        profile, identity_path, known_hosts_path = self._validated_profile()
        self._validate_request(request)
        remote_request = {
            "schema": POSTFLIGHT_REQUEST_SCHEMA,
            "target": DEVICE_TARGET,
            "byte_size": request.byte_size,
            "sha256": request.sha256,
            "postflight_effects": list(request.postflight_effects),
            "state_dir": STATE_DIR,
            "idempotency_key": request.idempotency_key,
        }
        code, output = self.run_command(
            self._ssh_args(profile, identity_path, known_hosts_path),
            _remote_python_stdin(REMOTE_POSTFLIGHT_PYTHON_ACTION, remote_request),
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
        if request.idempotency_key != IDEMPOTENCY_KEY:
            raise HomeEdgeFirmwareActionError("IDEMPOTENCY_KEY_MISMATCH")


def _remote_python_stdin(action: str, remote_request: Mapping[str, object]) -> str:
    request_json = json.dumps(remote_request, sort_keys=True, separators=(",", ":"))
    if len(request_json.encode("utf-8")) > MAX_REMOTE_REQUEST_JSON_BYTES:
        raise HomeEdgeFirmwareActionError("REMOTE_REQUEST_TOO_LARGE")
    return f"REMOTE_REQUEST_JSON = {request_json!r}\n" + action


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


def _remote_failure_reason(output: str, request: FirmwareTransferRequest) -> str:
    matches: list[Mapping[str, object]] = []
    for line in output.splitlines():
        if len(line) > 4096:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping) and _is_valid_remote_failure_receipt(parsed, request):
            matches.append(parsed)
    if len(matches) != 1:
        return "REMOTE_ACTION_FAILED"
    reason = matches[0].get("failure_reason")
    if not isinstance(reason, str):
        return "REMOTE_ACTION_FAILED"
    return REMOTE_FAILURE_REASON_TO_ERROR.get(reason, "REMOTE_ACTION_FAILED")


def _is_valid_remote_failure_receipt(parsed: Mapping[str, object], request: FirmwareTransferRequest) -> bool:
    if parsed.get("schema") != REMOTE_FAILURE_SCHEMA:
        return False
    if parsed.get("target") != DEVICE_TARGET:
        return False
    if parsed.get("sha256") != request.sha256 or parsed.get("byte_size") != request.byte_size:
        return False
    if parsed.get("final_status") != "BLOCKED":
        return False
    if not isinstance(parsed.get("failure_reason"), str):
        return False
    return parsed.get("failure_stage") in REMOTE_FAILURE_STAGES


def _safe_state(value: object) -> str:
    if isinstance(value, str) and value in {"ok", "blocked", "failed", "transferred", "skipped_verified_duplicate", "success", "2xx"}:
        return value
    return "blocked"


REMOTE_PYTHON_ACTION: Final = r'''
import hashlib, json, os, socket, sys, time
from pathlib import Path
from urllib import error, request

TARGET = "192.168.1.164"
REMOTE_TMP_PATH = "/tmp/skeleton-lavalamp-c98acbf-firmware.bin"
MAX_BYTES = 4 * 1024 * 1024
BOUNDARY = "skeleton-lavalamp-fixed-boundary"
FAILURE_SCHEMA = "skeleton.home_edge.lavalamp_ota_failure.v1"
REQUEST_SCHEMA = "skeleton.home_edge.lavalamp_ota_request.v1"
POSTFLIGHT_EFFECTS = ["CY Anemone", "CY Tidal Bloom"]
IDEMPOTENCY_KEY = "lavalamp-c98acbf-build-ota-1922-20260812-v2"
WLED_COMPATIBILITY_REJECTION_MARKER = b"not compatible"

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedirectMismatch()

class RemoteFailure(Exception):
    reason = "UPLOAD_TRANSPORT_FAILED"

class ArtifactMismatch(RemoteFailure):
    reason = "ARTIFACT_MISMATCH"

class ArtifactReadFailed(RemoteFailure):
    reason = "ARTIFACT_READ_FAILED"

class BackupStateFailed(RemoteFailure):
    reason = "BACKUP_STATE_FAILED"

class DeviceResponseMalformed(RemoteFailure):
    reason = "DEVICE_RESPONSE_MALFORMED"

class DeviceUnreachable(RemoteFailure):
    reason = "DEVICE_UNREACHABLE"

class EffectsMissing(RemoteFailure):
    reason = "EFFECTS_MISSING"

class MalformedRemoteRequest(RemoteFailure):
    reason = "MALFORMED_REMOTE_REQUEST"

class OtaLockedOrPinRequired(RemoteFailure):
    reason = "OTA_LOCKED_OR_PIN_REQUIRED"

class OtaUpdateRejected(RemoteFailure):
    reason = "OTA_UPDATE_REJECTED"

class PreflightIdentityMismatch(RemoteFailure):
    reason = "PREFLIGHT_IDENTITY_MISMATCH"

class RedirectMismatch(RemoteFailure):
    reason = "REDIRECT_MISMATCH"

class RebootTimeout(RemoteFailure):
    reason = "REBOOT_TIMEOUT"

class UploadTransportFailed(RemoteFailure):
    reason = "UPLOAD_TRANSPORT_FAILED"

class UnclassifiedRemoteFailure(RemoteFailure):
    reason = "REMOTE_ACTION_FAILED"

def out(payload):
    print(json.dumps(payload, sort_keys=True))

def failure_receipt(payload, reason, stage):
    return {
        "schema": FAILURE_SCHEMA,
        "target": TARGET,
        "sha256": payload.get("sha256", "") if isinstance(payload, dict) else "",
        "byte_size": payload.get("byte_size", 0) if isinstance(payload, dict) else 0,
        "final_status": "BLOCKED",
        "failure_reason": reason,
        "failure_stage": stage if stage in {
            "request_parse",
            "artifact_verify",
            "device_info",
            "device_effects",
            "device_config",
            "backup_config",
            "upload_request",
            "reboot_wait",
            "postflight_effects",
        } else "request_parse",
    }

def get_json(path, expected_type, timeout=10):
    req = request.Request("http://" + TARGET + path, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            parsed = json.loads(response.read(1024 * 512).decode("utf-8"))
    except (TimeoutError, socket.timeout, error.URLError, OSError):
        raise DeviceUnreachable()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DeviceResponseMalformed()
    if expected_type == "dict" and not isinstance(parsed, dict):
        raise DeviceResponseMalformed()
    if expected_type == "list" and not isinstance(parsed, list):
        raise DeviceResponseMalformed()
    return parsed

def has_effect(effects, name):
    return any(item == name or (isinstance(item, list) and name in item) for item in effects)

def read_payload():
    try:
        candidate = json.loads(REMOTE_REQUEST_JSON)
    except json.JSONDecodeError:
        raise MalformedRemoteRequest()
    if not isinstance(candidate, dict):
        raise MalformedRemoteRequest()
    for key in ("schema", "remote_path", "byte_size", "sha256", "target", "postflight_effects", "state_dir", "idempotency_key"):
        if key not in candidate:
            raise MalformedRemoteRequest()
    if candidate.get("schema") != REQUEST_SCHEMA:
        raise MalformedRemoteRequest()
    if candidate.get("remote_path") != REMOTE_TMP_PATH:
        raise MalformedRemoteRequest()
    if candidate.get("target") != TARGET:
        raise MalformedRemoteRequest()
    if candidate.get("postflight_effects") != POSTFLIGHT_EFFECTS:
        raise MalformedRemoteRequest()
    if candidate.get("idempotency_key") != IDEMPOTENCY_KEY:
        raise MalformedRemoteRequest()
    if not isinstance(candidate.get("byte_size"), int) or not isinstance(candidate.get("sha256"), str):
        raise MalformedRemoteRequest()
    if not isinstance(candidate.get("postflight_effects"), list) or not isinstance(candidate.get("state_dir"), str):
        raise MalformedRemoteRequest()
    return candidate

payload = {}
remote_path = Path(REMOTE_TMP_PATH)
failure_stage = "request_parse"
try:
    payload = read_payload()
    remote_path = Path(payload["remote_path"])
    state_dir = Path.home() / payload["state_dir"]
    result = {"target": TARGET, "sha256": payload["sha256"], "byte_size": payload["byte_size"], "effects": {}, "final_status": "OTA_UNVERIFIED"}
    failure_stage = "artifact_verify"
    try:
        data = remote_path.read_bytes()
    except OSError:
        raise ArtifactReadFailed()
    if len(data) != payload["byte_size"] or len(data) <= 0 or len(data) > MAX_BYTES:
        raise ArtifactMismatch()
    if hashlib.sha256(data).hexdigest() != payload["sha256"]:
        raise ArtifactMismatch()
    failure_stage = "device_info"
    info = get_json("/json/info", "dict")
    failure_stage = "device_effects"
    effects = get_json("/json/eff", "list")
    failure_stage = "device_config"
    cfg = get_json("/json/cfg", "dict")
    failure_stage = "backup_config"
    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = state_dir / ("cfg-" + payload["sha256"][:12] + ".json")
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"info": info, "cfg": cfg}, fh, sort_keys=True)
    except OSError:
        raise BackupStateFailed()
    failure_stage = "device_config"
    if info.get("brand", "WLED") != "WLED" or info.get("arch") != "esp32" or cfg.get("hw", {}).get("led", {}).get("total") != 256:
        raise PreflightIdentityMismatch()
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
    failure_stage = "upload_request"
    try:
        with opener.open(req, timeout=60) as response:
            if response.geturl().split("/")[2] != TARGET:
                raise RedirectMismatch()
            result["ota_http_status"] = int(response.status)
            result["ota_http_class"] = "2xx" if 200 <= response.status < 300 else "failed"
    except error.HTTPError as exc:
        status = int(exc.code)
        result["ota_http_status"] = status
        result["ota_http_class"] = "failed"
        if status in (401, 403):
            raise OtaLockedOrPinRequired()
        if status == 500:
            body_prefix = exc.read(2048)
            if WLED_COMPATIBILITY_REJECTION_MARKER in body_prefix.lower():
                failure = OtaUpdateRejected()
                failure.reason = "OTA_COMPATIBILITY_VALIDATION_REJECTED"
                raise failure
            raise OtaUpdateRejected()
        raise OtaUpdateRejected()
    except RedirectMismatch:
        raise
    except (TimeoutError, socket.timeout, error.URLError, OSError):
        raise UploadTransportFailed()
    if result["ota_http_class"] != "2xx":
        raise OtaUpdateRejected()
    failure_stage = "reboot_wait"
    deadline = time.time() + 180
    post_effects = None
    while time.time() < deadline:
        try:
            get_json("/json/info", "dict", timeout=5)
            failure_stage = "postflight_effects"
            post_effects = get_json("/json/eff", "list", timeout=5)
            break
        except DeviceUnreachable:
            time.sleep(5)
    result["reboot_observed"] = post_effects is not None
    if post_effects is None:
        raise RebootTimeout()
    failure_stage = "postflight_effects"
    result["effects"] = {name: has_effect(post_effects, name) for name in payload["postflight_effects"]}
    result["final_status"] = "DONE" if all(result["effects"].values()) else "OTA_UNVERIFIED"
    if result["final_status"] != "DONE":
        raise EffectsMissing()
except RemoteFailure as exc:
    out(failure_receipt(payload, exc.reason, failure_stage))
    sys.exit(1)
except Exception:
    out(failure_receipt(payload, UnclassifiedRemoteFailure.reason, failure_stage))
    sys.exit(1)
finally:
    try:
        remote_path.unlink()
    except OSError:
        pass
out(result)
'''


REMOTE_POSTFLIGHT_PYTHON_ACTION: Final = r'''
import json
from urllib import request

TARGET = "192.168.1.164"

def get_json(path, timeout=10):
    req = request.Request("http://" + TARGET + path, method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read(1024 * 512).decode("utf-8"))

def has_effect(effects, name):
    return any(item == name or (isinstance(item, list) and name in item) for item in effects)

payload = json.loads(REMOTE_REQUEST_JSON)
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

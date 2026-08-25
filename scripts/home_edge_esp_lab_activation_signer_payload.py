#!/usr/bin/python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TASK_ID = "home_edge_01_esp_lab_stage1_activation_v1"
TARGET_NODE = "home-edge-01"
EXECUTION_LANE = "privileged_mutation"
RUN_AS = "root"
REQUEST_TIMEOUT_SECONDS = 300
MAX_EXECUTOR_OUTPUT_BYTES = 8192
OPERATOR_APPROVAL_REF = "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_APPROVED"
APPROVED_SOURCE_SHA = "725dfc3aedbce194c7afcc229eb44b1eec4f463a"
INSTALLER_GIT_BLOB_SHA = "e2c2378660df0cbaaf02e4556a1d1887a258b863"
INSTALLER_SHA256 = "8eeef1374af6dee0451890e7db4e37e7fe8f249ec7aff55687e0a196168dbcfe"
INIT_REPO_PATH = "core/__init__.py"
INIT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ESP_MODULE_REPO_PATH = "core/home_edge/esp_lab.py"
ESP_MODULE_GIT_BLOB_SHA = "82a9a007b880eb591f13618216fb9fd3a97d926e"
ESP_MODULE_SHA256 = "4a499602f4602b425ae4227cb297e685f072c8a4cef56d23d1dd2e3c91333fcb"
PAYLOAD_SCHEMA = "skeleton.home_edge.esp_lab_stage1_payload.v1"
REQUEST_ID_PREFIX = f"{TASK_ID}-{APPROVED_SOURCE_SHA}-attempt-"
NONCE_PREFIX = f"{TASK_ID}:{APPROVED_SOURCE_SHA}:attempt:"
IDEMPOTENCY_KEY_PREFIX = f"home-edge-01-esp-lab-stage1-activation-{APPROVED_SOURCE_SHA}-attempt-"
EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
EXEC_HMAC_SECRET_CONFIG_DIR = Path("/etc/skeleton")
EXEC_HMAC_SECRET_PROFILE_METADATA_PATH = Path("/etc/skeleton/home-edge-01.env")
EXEC_HMAC_SECRET_CONFIG_PATH = Path("/etc/skeleton/home-edge-executor-controller.env")
INSTALLED_INSTALLER_SOURCE = Path("/usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh")
MAX_EXEC_HMAC_SECRET_CONFIG_BYTES = 64 * 1024
MAX_INSTALLER_SOURCE_BYTES = 256 * 1024
SIGNER_STDIN_MAX_BYTES = 256 * 1024
CONFIG_ASSIGNMENT_RE = re.compile(r"^(?:export[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
AUTH_CONFIG_REASON_RE = re.compile(r"^executor_auth_config_(?:missing|unsafe|invalid)$")
ATTEMPT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
FRESHNESS_SECONDS = 300


def fail(reason: str = "activation_signer_rejected") -> None:
    if AUTH_CONFIG_REASON_RE.fullmatch(reason):
        print(json.dumps({"error": reason}, sort_keys=True, separators=(",", ":")))
    raise SystemExit(2)


def _file_id(st: os.stat_result) -> tuple[int, int | None, int | None, int | None, int | None, int, int]:
    return (
        st.st_mode,
        getattr(st, "st_dev", None),
        getattr(st, "st_ino", None),
        getattr(st, "st_uid", None),
        getattr(st, "st_gid", None),
        st.st_size,
        getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
    )


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _safe_regular(st: os.stat_result, *, max_bytes: int, require_root: bool = False, allow_empty: bool = False) -> bool:
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    if (st.st_size <= 0 and not allow_empty) or st.st_size > max_bytes:
        return False
    if stat.S_IMODE(st.st_mode) & 0o022:
        return False
    if require_root and getattr(st, "st_uid", None) != 0:
        return False
    return True


def _safe_config_boundary(directory_st: os.stat_result, profile_st: os.stat_result, controller_st: os.stat_result) -> bool:
    if stat.S_ISLNK(directory_st.st_mode) or not stat.S_ISDIR(directory_st.st_mode):
        return False
    if stat.S_IMODE(directory_st.st_mode) & 0o022:
        return False
    if not _safe_regular(profile_st, max_bytes=MAX_EXEC_HMAC_SECRET_CONFIG_BYTES):
        return False
    if not _safe_regular(controller_st, max_bytes=MAX_EXEC_HMAC_SECRET_CONFIG_BYTES):
        return False
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    trusted_owner = controller_st.st_uid == 0 or (current_uid is not None and controller_st.st_uid == current_uid)
    coherent_boundary = (
        directory_st.st_uid == profile_st.st_uid == controller_st.st_uid
        and directory_st.st_gid == profile_st.st_gid == controller_st.st_gid
    )
    return trusted_owner or coherent_boundary


def _parse_value(value: str) -> str:
    if "$" in value or "`" in value:
        fail("executor_auth_config_invalid")
    if value.startswith(("'", '"')):
        quote = value[0]
        if len(value) < 2 or not value.endswith(quote):
            fail("executor_auth_config_invalid")
        decoded = value[1:-1]
        if quote in decoded or "\\" in decoded:
            fail("executor_auth_config_invalid")
    elif "'" in value or '"' in value:
        fail("executor_auth_config_invalid")
    else:
        decoded = value
    if "\n" in decoded or "\r" in decoded or not decoded:
        fail("executor_auth_config_invalid")
    return decoded


def read_secret() -> str:
    try:
        directory_st = EXEC_HMAC_SECRET_CONFIG_DIR.lstat()
        profile_st = EXEC_HMAC_SECRET_PROFILE_METADATA_PATH.lstat()
        controller_st = EXEC_HMAC_SECRET_CONFIG_PATH.lstat()
    except FileNotFoundError:
        fail("executor_auth_config_missing")
    except OSError:
        fail("executor_auth_config_unsafe")
    if not _safe_config_boundary(directory_st, profile_st, controller_st):
        fail("executor_auth_config_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(EXEC_HMAC_SECRET_CONFIG_PATH, flags)
    except OSError:
        fail("executor_auth_config_unsafe")
    try:
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not _safe_regular(before, max_bytes=MAX_EXEC_HMAC_SECRET_CONFIG_BYTES):
                fail("executor_auth_config_unsafe")
            data = handle.read(MAX_EXEC_HMAC_SECRET_CONFIG_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError:
        fail("executor_auth_config_unsafe")
    if _file_id(controller_st) != _file_id(before) or _file_id(before) != _file_id(after):
        fail("executor_auth_config_unsafe")
    if len(data) > MAX_EXEC_HMAC_SECRET_CONFIG_BYTES or b"\x00" in data:
        fail("executor_auth_config_invalid")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("executor_auth_config_invalid")
    secret: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            fail("executor_auth_config_invalid")
        match = CONFIG_ASSIGNMENT_RE.fullmatch(line)
        if match is None:
            fail("executor_auth_config_invalid")
        if match.group("name") != EXEC_HMAC_SECRET_ENV:
            continue
        if secret is not None:
            fail("executor_auth_config_invalid")
        secret = _parse_value(match.group("value"))
    if not secret:
        fail("executor_auth_config_missing")
    return secret


def expected_installer_script() -> str:
    try:
        st_l = INSTALLED_INSTALLER_SOURCE.lstat()
    except OSError:
        fail()
    if not _safe_regular(st_l, max_bytes=MAX_INSTALLER_SOURCE_BYTES, require_root=True):
        fail()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(INSTALLED_INSTALLER_SOURCE, flags)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not _safe_regular(before, max_bytes=MAX_INSTALLER_SOURCE_BYTES, require_root=True):
                fail()
            data = handle.read(MAX_INSTALLER_SOURCE_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError:
        fail()
    if (
        len(data) > MAX_INSTALLER_SOURCE_BYTES
        or not _safe_regular(after, max_bytes=MAX_INSTALLER_SOURCE_BYTES, require_root=True)
        or _file_id(st_l) != _file_id(before)
        or _file_id(before) != _file_id(after)
        or _git_blob_sha1(data) != INSTALLER_GIT_BLOB_SHA
        or hashlib.sha256(data).hexdigest() != INSTALLER_SHA256
    ):
        fail()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        fail()
    raise AssertionError("unreachable")


def validate_payload_text(stdin_text: Any) -> None:
    if not isinstance(stdin_text, str) or len(stdin_text.encode("utf-8")) > 230_000:
        fail()
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        fail()
    if not isinstance(payload, dict) or list(payload.keys()) != ["schema", "source_sha", "files"]:
        fail()
    if payload.get("schema") != PAYLOAD_SCHEMA or payload.get("source_sha") != APPROVED_SOURCE_SHA:
        fail()
    files = payload.get("files")
    if not isinstance(files, list) or len(files) != 2:
        fail()
    init_body = _decode_file(files[0], INIT_REPO_PATH, INIT_SHA256)
    esp_body = _decode_file(files[1], ESP_MODULE_REPO_PATH, ESP_MODULE_SHA256)
    if init_body != b"":
        fail()
    if _git_blob_sha1(esp_body) != ESP_MODULE_GIT_BLOB_SHA:
        fail()


def _decode_file(item: Any, path: str, sha256: str) -> bytes:
    if not isinstance(item, dict) or list(item.keys()) != ["path", "sha256", "base64"]:
        fail()
    if item.get("path") != path or item.get("sha256") != sha256 or not isinstance(item.get("base64"), str):
        fail()
    try:
        body = base64.b64decode(item["base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        fail()
    if hashlib.sha256(body).hexdigest() != sha256:
        fail()
    return body


def validate_authority(request: dict[str, Any]) -> None:
    required = {
        "schema", "request_id", "node_id", "argv", "environment", "timeout_seconds",
        "execution_lane", "operator_approval_ref", "idempotency_key", "run_as", "mode",
        "script", "script_interpreter", "stdin_text", "timestamp", "nonce",
        "max_output_bytes", "public",
    }
    if set(request) != required:
        fail()
    if request.get("schema") != "skeleton.home_edge.exec_request.v1":
        fail()
    attempt_token = _attempt_token_from_authority(request)
    validate_timestamp(request.get("timestamp"))
    if request.get("node_id") != TARGET_NODE or request.get("execution_lane") != EXECUTION_LANE:
        fail()
    if request.get("operator_approval_ref") != OPERATOR_APPROVAL_REF:
        fail()
    if request.get("idempotency_key") != _idempotency_key(attempt_token):
        fail()
    if request.get("run_as") != RUN_AS or request.get("mode") != "script":
        fail()
    if request.get("script_interpreter") != "bash" or request.get("script") != expected_installer_script():
        fail()
    if request.get("timeout_seconds") != REQUEST_TIMEOUT_SECONDS or request.get("max_output_bytes") != MAX_EXECUTOR_OUTPUT_BYTES:
        fail()
    if request.get("argv") != [] or request.get("environment") != {} or request.get("public") is not False:
        fail()
    validate_payload_text(request.get("stdin_text"))


def _request_id(attempt_token: str) -> str:
    return f"{REQUEST_ID_PREFIX}{attempt_token}"


def _nonce(attempt_token: str) -> str:
    return f"{NONCE_PREFIX}{attempt_token}"


def _idempotency_key(attempt_token: str) -> str:
    return f"{IDEMPOTENCY_KEY_PREFIX}{attempt_token}"


def _attempt_token_from_authority(request: dict[str, Any]) -> str:
    request_id = request.get("request_id")
    nonce = request.get("nonce")
    if not isinstance(request_id, str) or not request_id.startswith(REQUEST_ID_PREFIX):
        fail()
    attempt_token = request_id.removeprefix(REQUEST_ID_PREFIX)
    if ATTEMPT_TOKEN_RE.fullmatch(attempt_token) is None:
        fail()
    if nonce != _nonce(attempt_token):
        fail()
    return attempt_token


def validate_timestamp(value: Any) -> None:
    if not isinstance(value, str):
        fail()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail()
    if parsed.tzinfo is None:
        fail()
    if abs((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()) > FRESHNESS_SECONDS:
        fail()


def sign(request: dict[str, Any], secret: str) -> str:
    canonical = dict(request)
    canonical.pop("public", None)
    message = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def main() -> None:
    if len(sys.argv) != 1:
        fail()
    data = sys.stdin.buffer.read(SIGNER_STDIN_MAX_BYTES + 1)
    if not data or len(data) > SIGNER_STDIN_MAX_BYTES:
        fail()
    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail()
    if not isinstance(request, dict):
        fail()
    validate_authority(request)
    secret = read_secret()
    signed = dict(request)
    signed["signature"] = sign(request, secret)
    print(json.dumps(signed, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

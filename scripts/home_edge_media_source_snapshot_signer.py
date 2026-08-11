#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path


TASK_ID = "home_edge_01_media_source_snapshot_v1"
TARGET_NODE = "home-edge-01"
EXECUTION_LANE = "read_only"
RUN_AS = "desktop-user"
OPERATOR_APPROVAL_REF = "EXPLICIT_MINIMAL_HOME_EDGE_SNAPSHOT_ACCESS_REPAIR_2026_08_09"
REQUEST_TIMEOUT_SECONDS = 30
MAX_EXECUTOR_OUTPUT_BYTES = 1_000_000
IDEMPOTENCY_KEY_PREFIX = "home-edge-01-media-source-snapshot-v1"
EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
EXEC_HMAC_SECRET_CONFIG_DIR = Path(
    os.environ.get("SKELETON_HOME_EDGE_EXEC_HMAC_CONFIG_DIR", "/etc/skeleton")
)
EXEC_HMAC_SECRET_CONFIG_PATH = Path(
    os.environ.get(
        "SKELETON_HOME_EDGE_EXEC_HMAC_CONFIG_PATH",
        "/etc/skeleton/home-edge-executor-controller.env",
    )
)
MAX_EXEC_HMAC_SECRET_CONFIG_BYTES = 64 * 1024
MAX_STDIN_BYTES = 8 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024
PAYLOAD_PATH_ENV = "SKELETON_HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PAYLOAD"
DEFAULT_PAYLOAD_PATH = Path(
    "/usr/local/lib/skeleton-home-edge-executor/scripts/home_edge_media_source_snapshot_payload.py"
)
CONFIG_ASSIGNMENT_RE = re.compile(
    r"^(?:export[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$"
)


def main(argv: list[str]) -> int:
    if argv:
        return reject("argv_rejected")
    stdin = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(stdin) > MAX_STDIN_BYTES:
        return reject("stdin_oversize")
    try:
        data = json.loads(stdin.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return reject("stdin_invalid")
    if not isinstance(data, dict) or sorted(data) != [
        "idempotency_key",
        "nonce",
        "operator_approval_ref",
        "request_id",
        "timestamp",
    ]:
        return reject("stdin_shape_mismatch")
    try:
        request = unsigned_request(data)
        secret = read_exec_hmac_secret_config()
        request["signature"] = sign_request(request, secret)
    except ValueError as exc:
        return reject(str(exc))
    print(json.dumps(request, sort_keys=True, separators=(",", ":")))
    return 0


def reject(reason: str) -> int:
    print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True), file=sys.stderr)
    return 2


def unsigned_request(data: dict[str, object]) -> dict[str, object]:
    if data.get("operator_approval_ref") != OPERATOR_APPROVAL_REF:
        raise ValueError("operator_approval_mismatch")
    request_id = required_prefixed(data.get("request_id"), TASK_ID + "-", "request_id_mismatch")
    idempotency_key = required_prefixed(
        data.get("idempotency_key"), IDEMPOTENCY_KEY_PREFIX + "-", "idempotency_mismatch"
    )
    nonce = required_prefixed(data.get("nonce"), TASK_ID + "-", "nonce_mismatch")
    timestamp = required_text(data.get("timestamp"), "timestamp_mismatch")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp_mismatch") from None
    script = read_payload()
    return {
        "schema": "skeleton.home_edge.exec_request.v1",
        "request_id": request_id,
        "node_id": TARGET_NODE,
        "argv": [],
        "environment": {},
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "execution_lane": EXECUTION_LANE,
        "operator_approval_ref": OPERATOR_APPROVAL_REF,
        "idempotency_key": idempotency_key,
        "run_as": RUN_AS,
        "mode": "script",
        "script": script,
        "script_interpreter": "python3",
        "timestamp": timestamp,
        "nonce": nonce,
        "max_output_bytes": MAX_EXECUTOR_OUTPUT_BYTES,
        "public": False,
    }


def required_text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(reason)
    return value


def required_prefixed(value: object, prefix: str, reason: str) -> str:
    text = required_text(value, reason)
    if not text.startswith(prefix) or len(text) > 160:
        raise ValueError(reason)
    return text


def read_payload() -> str:
    path = Path(os.environ.get(PAYLOAD_PATH_ENV, str(DEFAULT_PAYLOAD_PATH)))
    try:
        st_l = path.lstat()
    except OSError:
        raise ValueError("payload_missing") from None
    if stat.S_ISLNK(st_l.st_mode) or not stat.S_ISREG(st_l.st_mode):
        raise ValueError("payload_unsafe")
    if st_l.st_size <= 0 or st_l.st_size > MAX_PAYLOAD_BYTES:
        raise ValueError("payload_unsafe")
    if stat.S_IMODE(st_l.st_mode) & 0o022:
        raise ValueError("payload_unsafe")
    if hasattr(os, "getuid") and st_l.st_uid != 0 and st_l.st_uid != os.getuid():
        raise ValueError("payload_unsafe")
    data = path.read_bytes()
    st_after = path.lstat()
    if file_id(st_l) != file_id(st_after) or len(data) != st_l.st_size:
        raise ValueError("payload_unsafe")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("payload_unsafe") from None


def read_exec_hmac_secret_config() -> str:
    try:
        directory_st = EXEC_HMAC_SECRET_CONFIG_DIR.lstat()
        controller_st = EXEC_HMAC_SECRET_CONFIG_PATH.lstat()
    except FileNotFoundError:
        raise ValueError("executor_auth_config_missing") from None
    except OSError:
        raise ValueError("executor_auth_config_unsafe") from None
    if stat.S_ISLNK(directory_st.st_mode) or not stat.S_ISDIR(directory_st.st_mode):
        raise ValueError("executor_auth_config_unsafe")
    if stat.S_IMODE(directory_st.st_mode) & 0o022:
        raise ValueError("executor_auth_config_unsafe")
    if not safe_config_file_metadata(controller_st):
        raise ValueError("executor_auth_config_unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(EXEC_HMAC_SECRET_CONFIG_PATH, flags)
    except FileNotFoundError:
        raise ValueError("executor_auth_config_missing") from None
    except OSError:
        raise ValueError("executor_auth_config_unsafe") from None
    try:
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not safe_config_file_metadata(before):
                raise ValueError("executor_auth_config_unsafe")
            data = handle.read(MAX_EXEC_HMAC_SECRET_CONFIG_BYTES + 1)
            after = os.fstat(handle.fileno())
    except ValueError:
        raise
    except OSError:
        raise ValueError("executor_auth_config_unsafe") from None
    if file_id(controller_st) != file_id(before) or file_id(before) != file_id(after):
        raise ValueError("executor_auth_config_unsafe")
    if len(data) > MAX_EXEC_HMAC_SECRET_CONFIG_BYTES:
        raise ValueError("executor_auth_config_unsafe")
    return parse_exec_hmac_secret_config(data)


def safe_config_file_metadata(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    if st.st_size > MAX_EXEC_HMAC_SECRET_CONFIG_BYTES:
        return False
    if stat.S_IMODE(st.st_mode) & 0o022:
        return False
    return True


def parse_exec_hmac_secret_config(data: bytes) -> str:
    if b"\x00" in data:
        raise ValueError("executor_auth_config_invalid")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("executor_auth_config_invalid") from None
    secret = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            raise ValueError("executor_auth_config_invalid")
        match = CONFIG_ASSIGNMENT_RE.fullmatch(line)
        if match is None:
            raise ValueError("executor_auth_config_invalid")
        if match.group("name") != EXEC_HMAC_SECRET_ENV:
            continue
        if secret is not None:
            raise ValueError("executor_auth_config_invalid")
        secret = parse_config_value(match.group("value"))
    if not secret:
        raise ValueError("executor_auth_config_missing")
    return secret


def parse_config_value(value: str) -> str:
    if "$" in value or "`" in value:
        raise ValueError("executor_auth_config_invalid")
    if value.startswith(("'", '"')):
        quote = value[0]
        if len(value) < 2 or not value.endswith(quote):
            raise ValueError("executor_auth_config_invalid")
        decoded = value[1:-1]
        if quote in decoded or "\\" in decoded:
            raise ValueError("executor_auth_config_invalid")
    elif "'" in value or '"' in value:
        raise ValueError("executor_auth_config_invalid")
    else:
        decoded = value
    if "\n" in decoded or "\r" in decoded or not decoded:
        raise ValueError("executor_auth_config_invalid")
    return decoded


def sign_request(request: dict[str, object], secret: str) -> str:
    canonical = {key: value for key, value in request.items() if key not in {"signature", "public"}}
    message = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def file_id(st: os.stat_result) -> dict[str, int | None]:
    return {
        "mode": stat.S_IFMT(st.st_mode),
        "dev": getattr(st, "st_dev", None),
        "ino": getattr(st, "st_ino", None),
        "uid": getattr(st, "st_uid", None),
        "gid": getattr(st, "st_gid", None),
        "size": st.st_size,
        "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

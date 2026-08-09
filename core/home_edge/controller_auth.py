from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV


EXEC_HMAC_SECRET_CONFIG_DIR = Path("/etc/skeleton")
EXEC_HMAC_SECRET_PROFILE_METADATA_PATH = Path("/etc/skeleton/home-edge-01.env")
EXEC_HMAC_SECRET_CONFIG_PATH = Path("/etc/skeleton/home-edge-executor-controller.env")
MAX_EXEC_HMAC_SECRET_CONFIG_BYTES = 64 * 1024
CONFIG_ASSIGNMENT_RE = re.compile(
    r"^(?:export[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$"
)


def read_fixed_controller_hmac_secret() -> str:
    try:
        controller_st = _validate_exec_hmac_secret_config_path()
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(EXEC_HMAC_SECRET_CONFIG_PATH, flags)
    except FileNotFoundError:
        raise ValueError("executor_auth_config_missing") from None
    except OSError:
        raise ValueError("executor_auth_config_unsafe") from None

    try:
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not _safe_exec_hmac_secret_config_file_metadata(before):
                raise ValueError("executor_auth_config_unsafe")
            data = handle.read(MAX_EXEC_HMAC_SECRET_CONFIG_BYTES + 1)
            after = os.fstat(handle.fileno())
    except ValueError:
        raise
    except OSError:
        raise ValueError("executor_auth_config_unsafe") from None

    if (
        _file_id(controller_st) != _file_id(before)
        or _file_id(before) != _file_id(after)
        or len(data) > MAX_EXEC_HMAC_SECRET_CONFIG_BYTES
    ):
        raise ValueError("executor_auth_config_unsafe")
    return _parse_exec_hmac_secret_config(data)


def _validate_exec_hmac_secret_config_path() -> os.stat_result:
    try:
        directory_st = EXEC_HMAC_SECRET_CONFIG_DIR.lstat()
        profile_st = EXEC_HMAC_SECRET_PROFILE_METADATA_PATH.lstat()
        controller_st = EXEC_HMAC_SECRET_CONFIG_PATH.lstat()
    except FileNotFoundError:
        raise ValueError("executor_auth_config_missing") from None
    except OSError:
        raise ValueError("executor_auth_config_unsafe") from None
    if not _safe_exec_hmac_secret_config_boundary(
        directory_st=directory_st,
        profile_st=profile_st,
        controller_st=controller_st,
    ):
        raise ValueError("executor_auth_config_unsafe")
    return controller_st


def _safe_exec_hmac_secret_config_boundary(
    *,
    directory_st: os.stat_result,
    profile_st: os.stat_result,
    controller_st: os.stat_result,
) -> bool:
    if stat.S_ISLNK(directory_st.st_mode) or not stat.S_ISDIR(directory_st.st_mode):
        return False
    if not _safe_exec_hmac_secret_config_file_metadata(profile_st):
        return False
    if not _safe_exec_hmac_secret_config_file_metadata(controller_st):
        return False
    if stat.S_IMODE(directory_st.st_mode) & 0o022:
        return False

    current_uid = os.getuid() if hasattr(os, "getuid") else None
    controller_owned_by_trusted_process = (
        controller_st.st_uid == 0
        or (current_uid is not None and controller_st.st_uid == current_uid)
    )
    coherent_private_controller = (
        directory_st.st_uid == profile_st.st_uid == controller_st.st_uid
        and directory_st.st_gid == profile_st.st_gid == controller_st.st_gid
    )
    return controller_owned_by_trusted_process or coherent_private_controller


def _safe_exec_hmac_secret_config_file_metadata(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    if st.st_size > MAX_EXEC_HMAC_SECRET_CONFIG_BYTES:
        return False
    if stat.S_IMODE(st.st_mode) & 0o022:
        return False
    return True


def _parse_exec_hmac_secret_config(data: bytes) -> str:
    if b"\x00" in data:
        raise ValueError("executor_auth_config_invalid")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("executor_auth_config_invalid") from None
    secret: str | None = None
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
        secret = _parse_exec_hmac_secret_config_value(match.group("value"))
    if not secret:
        raise ValueError("executor_auth_config_missing")
    return secret


def _parse_exec_hmac_secret_config_value(value: str) -> str:
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


def _file_id(st: os.stat_result) -> dict[str, int | None]:
    return {
        "mode": stat.S_IFMT(st.st_mode),
        "dev": getattr(st, "st_dev", None),
        "ino": getattr(st, "st_ino", None),
        "uid": getattr(st, "st_uid", None),
        "gid": getattr(st, "st_gid", None),
        "size": st.st_size,
        "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
    }

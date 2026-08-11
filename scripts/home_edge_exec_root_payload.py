#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path


ENV_FILE = Path(os.environ.get("SKELETON_HOME_EDGE_EXEC_ENV_FILE", "/etc/skeleton/home_edge_executor.env"))
SERVER_SCRIPT = Path(
    os.environ.get(
        "SKELETON_HOME_EDGE_EXEC_SERVER_SCRIPT",
        "/usr/local/lib/skeleton-home-edge-executor/scripts/home_edge_exec.py",
    )
)
MAX_ENV_BYTES = 64 * 1024
ASSIGNMENT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
REQUIRED = (
    "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET",
    "SKELETON_HOME_EDGE_DESKTOP_USER",
    "SKELETON_HOME_EDGE_EXEC_AUDIT_LOG",
    "SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE",
    "SKELETON_HOME_EDGE_EXEC_CANCEL_DIR",
)


def main(argv: list[str]) -> int:
    if argv != ["--server"]:
        print("home_edge_exec_root supports only --server", file=sys.stderr)
        return 2
    try:
        env = parse_env_file(ENV_FILE)
        validate_regular(SERVER_SCRIPT, max_bytes=256 * 1024, executable=False)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    exec_env = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONSAFEPATH": "1",
    }
    for key in REQUIRED:
        value = env.get(key)
        if not value:
            print("home_edge_exec private environment is missing", file=sys.stderr)
            return 2
        exec_env[key] = value
    os.execve("/usr/bin/python3", ["/usr/bin/python3", str(SERVER_SCRIPT), "--server"], exec_env)
    raise AssertionError("unreachable")


def parse_env_file(path: Path) -> dict[str, str]:
    validate_regular(path, max_bytes=MAX_ENV_BYTES, executable=False)
    try:
        data = path.read_bytes()
    except OSError:
        raise ValueError("home_edge_exec private environment is missing") from None
    if len(data) > MAX_ENV_BYTES or b"\x00" in data:
        raise ValueError("home_edge_exec private environment is unsafe")
    result: dict[str, str] = {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("home_edge_exec private environment is unsafe") from None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT_RE.fullmatch(line)
        if match is None:
            raise ValueError("home_edge_exec private environment is unsafe")
        result[match.group("name")] = parse_value(match.group("value"))
    return result


def parse_value(value: str) -> str:
    if "$" in value or "`" in value:
        raise ValueError("home_edge_exec private environment is unsafe")
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("home_edge_exec private environment is unsafe")
        inner = value[1:-1]
        return inner.replace("'\\''", "'")
    if value.startswith('"') or "'" in value or '"' in value:
        raise ValueError("home_edge_exec private environment is unsafe")
    return value


def validate_regular(path: Path, *, max_bytes: int, executable: bool) -> None:
    try:
        st = path.lstat()
    except OSError:
        raise ValueError("home_edge_exec private environment is missing") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("home_edge_exec private environment is unsafe")
    if st.st_size <= 0 or st.st_size > max_bytes:
        raise ValueError("home_edge_exec private environment is unsafe")
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise ValueError("home_edge_exec private environment is unsafe")
    if executable and not (stat.S_IMODE(st.st_mode) & 0o111):
        raise ValueError("home_edge_exec server is missing")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge import media_source_snapshot

PROFILE_ENV_PATH = Path("/etc/skeleton/home-edge-01.env")
PROFILE_ENV_ALLOWLIST = frozenset(
    {
        "SKELETON_HOME_EDGE_01_PROFILE",
        "SKELETON_HOME_EDGE_01_HOSTNAME",
        "SKELETON_HOME_EDGE_01_TAILSCALE_IP",
        "SKELETON_HOME_EDGE_01_CONTROLLER_HOST",
        "SKELETON_HOME_EDGE_01_CONTROLLER_TAILSCALE_IP",
        "SKELETON_HOME_EDGE_01_TARGET_USER",
        "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
        "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
        "SKELETON_RUNNER_PRIVATE_MEMORY_ROOT",
    }
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        _write_blocked()
        return 2
    try:
        decoded = json.loads(sys.stdin.read())
        _load_profile_environment()
    except json.JSONDecodeError:
        _write_blocked()
        return 2
    except ValueError:
        _write_blocked()
        return 2
    receipt = media_source_snapshot.execute_privileged_fixed_snapshot_payload(decoded)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if media_source_snapshot.success_criteria_met(receipt) else 2


def _write_blocked() -> None:
    receipt = media_source_snapshot._blocked_receipt("privileged_snapshot_capability_rejected")
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))


def _load_profile_environment() -> None:
    try:
        data = PROFILE_ENV_PATH.read_bytes()
    except OSError as exc:
        raise ValueError("profile_env_unavailable") from exc
    if len(data) > media_source_snapshot.MAX_EXEC_HMAC_SECRET_CONFIG_BYTES:
        raise ValueError("profile_env_unsafe")
    text = data.decode("utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = media_source_snapshot.CONFIG_ASSIGNMENT_RE.fullmatch(line)
        if match is None or line.endswith("\\"):
            raise ValueError("profile_env_invalid")
        name = match.group("name")
        if name not in PROFILE_ENV_ALLOWLIST:
            continue
        value = media_source_snapshot._parse_exec_hmac_secret_config_value(match.group("value"))
        os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())

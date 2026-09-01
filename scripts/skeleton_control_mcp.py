#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


REPOSITORY = "alanua/Skeleton"
CHECKOUT_CONFIG_SCHEMA = "skeleton.runner_controller_checkout_config.v1"
INSTALL_CONFIG_RELATIVE = Path("usr/local/lib/skeleton/runner-controller/config/checkout.json")
PRODUCTION_CONFIG = Path("/") / INSTALL_CONFIG_RELATIVE


def _installed_prefix(script: Path) -> Path | None:
    parts = script.resolve().parts
    suffix = ("usr", "local", "bin", "skeleton-control-mcp")
    if len(parts) < len(suffix) or tuple(parts[-len(suffix) :]) != suffix:
        return None
    prefix_parts = parts[: -len(suffix)]
    return Path(*prefix_parts) if prefix_parts else Path("/")


def _load_registered_checkout(config_path: Path) -> Path | None:
    if config_path == PRODUCTION_CONFIG:
        try:
            st = os.lstat(config_path)
        except OSError:
            return None
        if (
            stat.S_ISLNK(st.st_mode)
            or not stat.S_ISREG(st.st_mode)
            or st.st_uid != 0
            or st.st_gid != 0
            or stat.S_IMODE(st.st_mode) & 0o022
        ):
            return None
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(loaded, dict)
        or loaded.get("schema") != CHECKOUT_CONFIG_SCHEMA
        or loaded.get("repository") != REPOSITORY
        or set(loaded) != {"schema", "repository", "checkout_path"}
        or not isinstance(loaded.get("checkout_path"), str)
    ):
        return None
    checkout = Path(loaded["checkout_path"])
    if not checkout.is_absolute() or "\x00" in loaded["checkout_path"]:
        return None
    return checkout


def _module_root() -> Path:
    script = Path(__file__).resolve()
    repo_root = script.parents[1]
    if (repo_root / "core/hetzner_control_mcp.py").is_file():
        return repo_root
    prefix = _installed_prefix(script)
    config_path = (prefix / INSTALL_CONFIG_RELATIVE) if prefix is not None else Path("/") / INSTALL_CONFIG_RELATIVE
    checkout = _load_registered_checkout(config_path)
    if checkout is not None and (checkout / "core/hetzner_control_mcp.py").is_file():
        return checkout
    raise RuntimeError("registered Skeleton checkout unavailable")


ROOT = _module_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.hetzner_control_mcp import handle_jsonrpc_message


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_jsonrpc_message(json.loads(line))
        if response is not None:
            print(json.dumps(response, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

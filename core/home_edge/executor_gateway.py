from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .executor import (
    DEFAULT_NODE_ID,
    HomeEdgeExecEngine,
    HomeEdgeExecError,
    HomeEdgeExecRequest,
    HomeEdgeExecReceipt,
    PUBLIC_ERROR_MESSAGE,
    receipt_from_mapping,
)
from .profile import HomeEdgeProfile, load_home_edge_profile


EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
NODE_AUDIT_LOG_ENV = "SKELETON_HOME_EDGE_EXEC_AUDIT_LOG"
NODE_IDEMPOTENCY_CACHE_ENV = "SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE"
NODE_CANCEL_DIR_ENV = "SKELETON_HOME_EDGE_EXEC_CANCEL_DIR"


class HomeEdgeExecTransport(Protocol):
    adapter_name: str

    def execute(self, request: Mapping[str, Any], *, timeout_seconds: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LocalExecTransport:
    adapter_name: str = "local_home_edge_exec"

    def execute(self, request: Mapping[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
        engine = build_node_engine()
        return engine.execute(request).to_mapping()


class OpenSSHExecTransport:
    adapter_name = "openssh_strict_host_key"

    def __init__(self, profile: HomeEdgeProfile) -> None:
        self.profile = profile

    def execute(self, request: Mapping[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
        identity = os.environ.get(self.profile.identity_env, "").strip()
        known_hosts = os.environ.get(self.profile.known_hosts_env, "").strip()
        if not identity or not known_hosts:
            raise HomeEdgeExecError("strict SSH runtime environment is missing")
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=3",
            "-i",
            identity,
            f"{self.profile.target_user}@{self.profile.tailscale_ip}",
            "/usr/local/bin/home_edge_exec",
            "--server",
        ]
        completed = subprocess.run(
            command,
            input=json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            stderr = _bounded_private_error(completed.stderr)
            if bool(request.get("public", False)):
                raise HomeEdgeExecError(PUBLIC_ERROR_MESSAGE)
            raise HomeEdgeExecError(f"remote home_edge_exec failed: {stderr}")
        try:
            decoded = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            if bool(request.get("public", False)):
                raise HomeEdgeExecError(PUBLIC_ERROR_MESSAGE) from exc
            raise HomeEdgeExecError("remote home_edge_exec returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            if bool(request.get("public", False)):
                raise HomeEdgeExecError(PUBLIC_ERROR_MESSAGE)
            raise HomeEdgeExecError("remote home_edge_exec response must be an object")
        return decoded


def execute_home_edge_request(
    request: Mapping[str, Any],
    *,
    profile: HomeEdgeProfile | None = None,
    transport: HomeEdgeExecTransport | None = None,
) -> HomeEdgeExecReceipt:
    parsed = HomeEdgeExecRequest.from_mapping(request)
    if parsed.node_id != DEFAULT_NODE_ID:
        raise HomeEdgeExecError("request node_id is not bound to home-edge-01")
    active_transport = transport or OpenSSHExecTransport(profile or load_home_edge_profile())
    try:
        response = active_transport.execute(parsed.to_mapping(), timeout_seconds=parsed.timeout_seconds + 30)
    except Exception as exc:
        if parsed.public:
            raise HomeEdgeExecError(PUBLIC_ERROR_MESSAGE) from exc
        raise
    return receipt_from_mapping(response)


def build_node_engine() -> HomeEdgeExecEngine:
    return HomeEdgeExecEngine(
        audit_log=_path_from_env(NODE_AUDIT_LOG_ENV),
        idempotency_cache=_path_from_env(NODE_IDEMPOTENCY_CACHE_ENV),
        hmac_secret=os.environ.get(EXEC_HMAC_SECRET_ENV),
        cancel_dir=_path_from_env(NODE_CANCEL_DIR_ENV),
    )


def server_once(stdin: str) -> dict[str, Any]:
    decoded = json.loads(stdin)
    if not isinstance(decoded, dict):
        raise HomeEdgeExecError("request must be a JSON object")
    try:
        return build_node_engine().execute(decoded).to_mapping()
    except Exception as exc:
        if bool(decoded.get("public", False)):
            raise HomeEdgeExecError(PUBLIC_ERROR_MESSAGE) from exc
        raise


def _path_from_env(name: str) -> Path | None:
    value = os.environ.get(name)
    if value == "":
        return None
    return Path(value) if value else None


def _bounded_private_error(value: str, *, limit: int = 2000) -> str:
    text = value.strip()
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text or "no stderr"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Home Edge universal executor gateway.")
    parser.add_argument("--server", action="store_true", help="Run one node-side JSON request from stdin.")
    args = parser.parse_args(argv)
    if not args.server:
        parser.error("only --server is supported by the gateway module")
    try:
        print(json.dumps(server_once(sys.stdin.read()), sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must return structured failure.
        error = PUBLIC_ERROR_MESSAGE if str(exc) == PUBLIC_ERROR_MESSAGE else f"{type(exc).__name__}: {exc}"
        print(json.dumps({"status": "blocked", "error": error}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

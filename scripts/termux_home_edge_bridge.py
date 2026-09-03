#!/usr/bin/env python3
"""Bounded Android Termux controller bridge for the canonical Home Edge executor.

This module deliberately delegates signing, approval propagation and receipt
handling to the existing Skeleton controller CLI. It never opens an arbitrary
remote shell and never invokes a second Home Edge executor.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_CONTROLLER = Path(__file__).with_name("home_edge_exec.py")
ALLOWED_PROBES = {
    "whoami": ("whoami",),
}


@dataclass(frozen=True)
class BridgeConfig:
    phone_node_id: str
    home_edge_node_id: str
    controller_path: Path

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        phone_node_id = os.environ.get("SKELETON_PHONE_NODE_ID", "").strip()
        home_edge_node_id = os.environ.get("SKELETON_HOME_EDGE_NODE_ID", "").strip()
        controller = Path(os.environ.get("SKELETON_HOME_EDGE_CONTROLLER", str(DEFAULT_CONTROLLER)))

        if not phone_node_id:
            raise RuntimeError("SKELETON_PHONE_NODE_ID is required")
        if home_edge_node_id != "home-edge-01":
            raise RuntimeError("SKELETON_HOME_EDGE_NODE_ID must be home-edge-01")
        if not controller.is_file():
            raise RuntimeError("Home Edge controller path is unavailable")
        return cls(phone_node_id, home_edge_node_id, controller)


def build_controller_argv(
    config: BridgeConfig,
    probe: str,
    *,
    request_id: str,
    idempotency_key: str,
    timeout: int,
) -> list[str]:
    if probe not in ALLOWED_PROBES:
        raise RuntimeError(f"unsupported probe: {probe}")
    if not request_id.strip() or not idempotency_key.strip():
        raise RuntimeError("request_id and idempotency_key are required")
    if timeout < 1 or timeout > 30:
        raise RuntimeError("timeout must be between 1 and 30 seconds")

    return [
        sys.executable,
        str(config.controller_path),
        "--node-id",
        config.home_edge_node_id,
        "--lane",
        "read_only",
        "--run-as",
        "desktop-user",
        "--timeout-seconds",
        str(timeout),
        "--request-id",
        request_id,
        "--idempotency-key",
        idempotency_key,
        "--",
        *ALLOWED_PROBES[probe],
    ]


def run_probe(config: BridgeConfig, argv: Sequence[str]) -> dict:
    completed = subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise RuntimeError("canonical Home Edge controller rejected the bridge probe")
    try:
        receipt = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical controller returned an invalid receipt") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("canonical controller returned a non-object receipt")
    if receipt.get("status") != "ok":
        raise RuntimeError("canonical Home Edge controller did not return an ok receipt")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=sorted(ALLOWED_PROBES))
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    try:
        config = BridgeConfig.from_env()
        argv = build_controller_argv(
            config,
            args.probe,
            request_id=args.request_id,
            idempotency_key=args.idempotency_key,
            timeout=args.timeout,
        )
        receipt = run_probe(config, argv)
    except RuntimeError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2

    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

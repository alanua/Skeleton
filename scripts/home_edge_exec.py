#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.executor import HomeEdgeExecRequest, PUBLIC_ERROR_MESSAGE, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request, server_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Submit one universal Home Edge exec request.")
    parser.add_argument("--server", action="store_true", help="Node-side mode: execute one JSON request from stdin.")
    parser.add_argument("--request-json", help="JSON file containing a complete exec request.")
    parser.add_argument("--node-id", default="home-edge-01")
    parser.add_argument("--lane", choices=["read_only", "routine_mutation", "privileged_mutation", "destructive"], default="read_only")
    parser.add_argument("--run-as", choices=["desktop-user", "root"], default="desktop-user")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--cwd")
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE environment override. Repeatable.")
    parser.add_argument("--stdin-text")
    parser.add_argument("--script", help="Run bounded script mode instead of argv.")
    parser.add_argument("--script-interpreter", choices=["bash", "python3"], default="bash")
    parser.add_argument("--operator-approval-ref")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--request-id")
    parser.add_argument("--sign-secret-env", default=EXEC_HMAC_SECRET_ENV)
    parser.add_argument("--public", action="store_true", help="Ask the node to redact public receipt fields.")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="Command argv after --.")
    args = parser.parse_args(argv)

    public_requested = _argv_requests_public(argv or sys.argv[1:])
    try:
        if args.server:
            print(json.dumps(server_once(sys.stdin.read()), indent=2, sort_keys=True))
            return 0
        request = _request_from_args(args)
        public_requested = bool(request.get("public", False))
        receipt = execute_home_edge_request(request)
        print(json.dumps(receipt.to_mapping(), indent=2, sort_keys=True))
        return 0 if receipt.status == "ok" else 2
    except Exception as exc:  # noqa: BLE001 - CLI returns a bounded JSON failure.
        error = PUBLIC_ERROR_MESSAGE if public_requested or str(exc) == PUBLIC_ERROR_MESSAGE else f"{type(exc).__name__}: {exc}"
        print(json.dumps({"status": "blocked", "error": error}, sort_keys=True), file=sys.stderr)
        return 2


def _request_from_args(args: argparse.Namespace) -> dict:
    if args.request_json:
        data = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request JSON must be an object")
    else:
        command_argv = list(args.argv)
        if command_argv[:1] == ["--"]:
            command_argv = command_argv[1:]
        env = {}
        for item in args.env:
            if "=" not in item:
                raise ValueError("--env values must be KEY=VALUE")
            key, value = item.split("=", 1)
            env[key] = value
        data = {
            "request_id": args.request_id,
            "node_id": args.node_id,
            "execution_lane": args.lane,
            "argv": [] if args.script else command_argv,
            "stdin_text": args.stdin_text,
            "cwd": args.cwd,
            "environment": env,
            "timeout_seconds": args.timeout_seconds,
            "operator_approval_ref": args.operator_approval_ref,
            "idempotency_key": args.idempotency_key,
            "run_as": args.run_as,
            "mode": "script" if args.script else "argv",
            "script": args.script,
            "script_interpreter": args.script_interpreter,
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": args.request_id or args.idempotency_key or datetime.now(UTC).isoformat(),
            "public": args.public,
        }
    data["timestamp"] = data.get("timestamp") or datetime.now(UTC).isoformat()
    data["nonce"] = data.get("nonce") or f"home-edge-exec-{uuid4()}"
    secret = os.environ.get(args.sign_secret_env, "").strip()
    if not secret:
        raise ValueError(f"missing required HMAC secret in {args.sign_secret_env}")
    request = HomeEdgeExecRequest.from_mapping({key: value for key, value in data.items() if value is not None})
    data["signature"] = sign_request(request, secret)
    return {key: value for key, value in data.items() if value is not None}


def _argv_requests_public(argv: list[str]) -> bool:
    if "--public" in argv:
        return True
    if "--request-json" not in argv:
        return False
    try:
        path = argv[argv.index("--request-json") + 1]
    except IndexError:
        return False
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - best-effort public-boundary detection.
        return False
    return isinstance(data, dict) and bool(data.get("public", False))


if __name__ == "__main__":
    raise SystemExit(main())

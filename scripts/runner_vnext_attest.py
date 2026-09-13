from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

NODE_IDS = {
    "codegen": "node:runner-vnext-codegen-runtime",
    "validate": "node:runner-vnext-validation-runtime",
    "publish": "node:runner-vnext-publication-runtime",
    "control": "node:runner-vnext-control-runtime",
    "merge": "node:runner-vnext-merge-runtime",
}
ROUTE_RANKS = {
    "codegen": 0,
    "validate": 10,
    "publish": 20,
    "control": 30,
    "merge": 40,
}
ALLOWED_ADAPTERS = {
    "codegen": frozenset(("adapter:repo-codegen",)),
    "validate": frozenset(("adapter:repo-validation",)),
    "publish": frozenset(("adapter:draft-publication",)),
    "control": frozenset(("adapter:runtime-control", "adapter:operation-recovery")),
    "merge": frozenset(("adapter:exact-operator-merge",)),
}
LANE_CAPABILITIES = {
    "codegen": frozenset(("repository_read", "repository_write_allowlisted", "test_execution")),
    "validate": frozenset(("repository_read", "repository_write_allowlisted", "test_execution")),
    "publish": frozenset(("repository_read", "repository_write_allowlisted", "test_execution", "publish_pull_request")),
    "control": frozenset(("repository_maintenance", "diagnostic_read", "subprocess_isolated")),
    "merge": frozenset(("publish_pull_request",)),
}
RESOURCE_NAMESPACES = {
    "codegen": frozenset(("repo",)),
    "validate": frozenset(("repo",)),
    "publish": frozenset(("repo",)),
    "control": frozenset(("control", "service-config", "repo")),
    "merge": frozenset(("protected", "repo")),
}
MAX_TTL_SECONDS = 60.0


def _fail(reason: str) -> int:
    sys.stderr.write(reason + "\n")
    return 2


def _safe_ref(value: str) -> bool:
    if not value or "\\" in value or ".." in value or any(ch.isspace() for ch in value):
        return False
    namespace, sep, tail = value.partition(":")
    return bool(sep and namespace and tail) and not tail.startswith(("/", "~"))


def _probe(command: list[str], *, cwd: Path = ROOT, timeout: int = 10) -> bool:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _git_head() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip().lower()
    if completed.returncode != 0 or len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        return None
    return value


def _probe_capabilities(lane: str, capabilities: tuple[str, ...]) -> tuple[bool, dict[str, bool], str | None]:
    probes: dict[str, bool] = {}
    probes["git"] = shutil.which("git") is not None and _probe(["git", "rev-parse", "--is-inside-work-tree"])
    probes["python"] = bool(sys.executable) and Path(sys.executable).is_absolute()
    head = _git_head()
    probes["git_head"] = head is not None
    if "repository_read" in capabilities or "diagnostic_read" in capabilities:
        probes["repository_read"] = os.access(ROOT, os.R_OK)
    if "repository_write_allowlisted" in capabilities:
        probes["repository_write"] = os.access(ROOT, os.W_OK)
    if "test_execution" in capabilities:
        probes["pytest"] = _probe([sys.executable, "-c", "import pytest"])
    if "publish_pull_request" in capabilities:
        gh = shutil.which("gh")
        probes["gh"] = gh is not None
        probes["gh_auth"] = bool(gh) and _probe([str(gh), "auth", "status", "--hostname", "github.com"])
    if "subprocess_isolated" in capabilities:
        probes["subprocess"] = _probe([sys.executable, "-c", "import subprocess; raise SystemExit(0)"])
    if "repository_maintenance" in capabilities:
        probes["maintenance_repo"] = probes["git"] and os.access(ROOT, os.R_OK | os.W_OK)
    if lane == "codegen":
        probes["codegen_executor"] = shutil.which("codex") is not None or shutil.which("openhands") is not None
    return all(probes.values()), probes, head


def build_snapshot(
    *,
    lane: str,
    adapter: str,
    capabilities: tuple[str, ...],
    resources: tuple[str, ...],
    privacy: str,
    ttl_seconds: float,
) -> dict[str, object]:
    if lane not in NODE_IDS:
        raise ValueError("attestor_lane_invalid")
    if adapter not in ALLOWED_ADAPTERS[lane]:
        raise ValueError("attestor_adapter_invalid")
    if not capabilities or len(set(capabilities)) != len(capabilities):
        raise ValueError("attestor_capabilities_invalid")
    if not set(capabilities).issubset(LANE_CAPABILITIES[lane]):
        raise ValueError("attestor_capability_not_allowed")
    if not resources or len(set(resources)) != len(resources):
        raise ValueError("attestor_resources_invalid")
    allowed_namespaces = RESOURCE_NAMESPACES[lane]
    for resource in resources:
        if not _safe_ref(resource) or resource.partition(":")[0] not in allowed_namespaces:
            raise ValueError("attestor_resource_not_allowed")
    if privacy != "PUBLIC_SAFE":
        raise ValueError("attestor_privacy_invalid")
    if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError("attestor_ttl_invalid")

    ok, probes, head = _probe_capabilities(lane, capabilities)
    if not ok or head is None:
        raise RuntimeError("attestor_runtime_probe_failed")
    observed_at = time.time()
    generation = time.time_ns()
    evidence_payload = {
        "lane": lane,
        "adapter": adapter,
        "capabilities": list(capabilities),
        "resources": list(resources),
        "privacy": privacy,
        "git_head": head,
        "probes": probes,
        "generation": generation,
    }
    digest = hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return {
        "node_id": NODE_IDS[lane],
        "generation": generation,
        "route_rank": ROUTE_RANKS[lane],
        "capabilities": list(capabilities),
        "supported_adapters": [adapter],
        "supported_lanes": [lane],
        "privacy_classes": [privacy],
        "resource_patterns": list(resources),
        "observed_at": observed_at,
        "expires_at": observed_at + ttl_seconds,
        "attestation_ref": f"attestation:runtime-probe:{digest}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--resource", action="append", default=[])
    parser.add_argument("--privacy", default="PUBLIC_SAFE")
    parser.add_argument("--ttl", type=float, default=45.0)
    args = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(
            lane=args.lane,
            adapter=args.adapter,
            capabilities=tuple(args.capability),
            resources=tuple(args.resource),
            privacy=args.privacy,
            ttl_seconds=args.ttl,
        )
    except (ValueError, RuntimeError) as exc:
        return _fail(str(exc))
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

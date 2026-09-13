from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runner_task import RunnerTask
from core.runner_vnext_authority import ROUTE_DIAGNOSTIC, bind_runner_operation

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

REPOSITORY = "alanua/Skeleton"
DIAGNOSTIC_PROFILE = "green_diagnostic_v1"
DIAGNOSTIC_MODE = "RUNNER_VNEXT_GREEN_DIAGNOSTIC"
DIAGNOSTIC_NODE_ID = "node:runner-vnext-harmless-diagnostic-runtime"
DIAGNOSTIC_ROUTE_RANK = 5
DIAGNOSTIC_TTL_SECONDS = 45.0
DIAGNOSTIC_ALLOWED_FILE = "docs/RUNNER_MAINTENANCE_TASKS.md"
DIAGNOSTIC_APPROVAL_REFERENCE = "runner_vnext_green_lifecycle_canary_v1"
STATE_ROOT_ENV = "SKELETON_RUNNER_VNEXT_STATE_ROOT"
SNAPSHOT_FILENAME = "runner-vnext-external-attestation.json"
SNAPSHOT_SCHEMA = "skeleton.runner_vnext_node_capability_snapshot.v1"
ENVELOPE_SCHEMA = "skeleton.runner_vnext_external_attestation_envelope.v1"
RECEIPT_SCHEMA = "skeleton.runner_vnext_external_attestation_receipt.v1"


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


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _exact_field(body: str, label: str) -> str:
    prefix = label + ":"
    values = [line[len(prefix):].strip() for line in body.splitlines() if line.startswith(prefix)]
    if len(values) != 1 or not values[0]:
        raise ValueError("attestor_target_issue_contract_invalid")
    return values[0]


def _safe_sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("attestor_expected_binding_invalid")
    return normalized


def _safe_git_sha(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 40 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("attestor_target_state_invalid")
    return normalized


def _fetch_issue_body(repository: str, issue_number: int) -> str:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("attestor_gh_unavailable")
    try:
        completed = subprocess.run(
            [str(gh), "issue", "view", str(issue_number), "--repo", repository, "--json", "body,state"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("attestor_target_issue_unavailable") from exc
    if completed.returncode != 0:
        raise RuntimeError("attestor_target_issue_unavailable")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("attestor_target_issue_invalid") from exc
    if set(payload) != {"body", "state"} or payload.get("state") != "OPEN" or not isinstance(payload.get("body"), str):
        raise RuntimeError("attestor_target_issue_invalid")
    return payload["body"]


def _diagnostic_bound(repository: str, issue_number: int, body: str, expected_idempotency_key: str):
    if repository != REPOSITORY:
        raise ValueError("attestor_repository_invalid")
    if _exact_field(body, "Mode") != DIAGNOSTIC_MODE:
        raise ValueError("attestor_target_issue_contract_invalid")
    if _exact_field(body, "Repository") != repository:
        raise ValueError("attestor_target_issue_contract_invalid")
    if _exact_field(body, "Profile") != DIAGNOSTIC_PROFILE:
        raise ValueError("attestor_target_issue_contract_invalid")
    if _exact_field(body, "Idempotency Key") != expected_idempotency_key:
        raise ValueError("attestor_idempotency_mismatch")
    main_sha = _safe_git_sha(_exact_field(body, "Expected Main SHA"))
    task = RunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": repository,
            "branch": "main",
            "base_sha": main_sha,
            "task_kind": "diagnostic",
            "payload": {
                "operation": "runner_vnext_green_diagnostic",
                "profile": DIAGNOSTIC_PROFILE,
                "target_issue": issue_number,
            },
            "requested_capabilities": [
                "repository_read",
                "repository_write_allowlisted",
                "test_execution",
            ],
            "allowed_files": [DIAGNOSTIC_ALLOWED_FILE],
            "forbidden_actions": [
                "codegen",
                "merge",
                "publication",
                "runtime_change",
                "secret_access",
            ],
            "validation_commands": [["python3", "-c", "raise SystemExit(0)"]],
            "validation_timeout_seconds": 60,
            "expected_output": ["harmless vNext diagnostic receipt"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": DIAGNOSTIC_APPROVAL_REFERENCE,
            "idempotency_key": expected_idempotency_key,
        }
    )
    return bind_runner_operation(
        runner_task=task,
        route=ROUTE_DIAGNOSTIC,
        operation="diagnostic",
        source_task_ref=f"issue:{issue_number}",
    )


def _safe_state_root() -> Path:
    raw = os.environ.get(STATE_ROOT_ENV, "")
    if not raw or raw == ":memory:":
        raise RuntimeError("attestor_state_root_invalid")
    root = Path(raw)
    if not root.is_absolute():
        raise RuntimeError("attestor_state_root_invalid")
    try:
        info = root.lstat()
    except OSError as exc:
        raise RuntimeError("attestor_state_root_invalid") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("attestor_state_root_invalid")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError("attestor_state_root_invalid")
    return root


def _snapshot_path(root: Path) -> Path:
    path = root / SNAPSHOT_FILENAME
    if path.parent != root:
        raise RuntimeError("attestor_snapshot_path_invalid")
    if path.exists() or path.is_symlink():
        try:
            info = path.lstat()
        except OSError as exc:
            raise RuntimeError("attestor_snapshot_path_invalid") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("attestor_snapshot_path_invalid")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("attestor_snapshot_path_invalid")
    return path


def _write_private_snapshot(root: Path, envelope: dict[str, object]) -> None:
    destination = _snapshot_path(root)
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".runner-vnext-attestation-", dir=str(root), text=True)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        info = temp.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("attestor_snapshot_temp_invalid")
        os.replace(temp, destination)
        final = destination.lstat()
        if not stat.S_ISREG(final.st_mode) or stat.S_ISLNK(final.st_mode):
            raise RuntimeError("attestor_snapshot_write_invalid")
        if final.st_uid != os.geteuid() or stat.S_IMODE(final.st_mode) & 0o077:
            raise RuntimeError("attestor_snapshot_write_invalid")
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def produce_external_attestation(
    *,
    repository: str,
    target_issue: int,
    expected_binding_sha256: str,
    expected_idempotency_key: str,
    profile: str,
) -> dict[str, object]:
    if repository != REPOSITORY or target_issue < 1 or profile != DIAGNOSTIC_PROFILE:
        raise ValueError("attestor_selector_invalid")
    expected_binding = _safe_sha256(expected_binding_sha256)
    if not expected_idempotency_key or len(expected_idempotency_key) > 128:
        raise ValueError("attestor_idempotency_invalid")
    body = _fetch_issue_body(repository, target_issue)
    bound = _diagnostic_bound(repository, target_issue, body, expected_idempotency_key)
    if bound.source_binding_hash != expected_binding:
        raise ValueError("attestor_binding_mismatch")

    capabilities = tuple(bound.universal_task.required_capabilities)
    ok, probes, head = _probe_capabilities(bound.binding.lane.value, capabilities)
    gh = shutil.which("gh")
    probes["gh"] = gh is not None
    probes["gh_auth"] = bool(gh) and _probe([str(gh), "auth", "status", "--hostname", "github.com"])
    ok = ok and probes["gh"] and probes["gh_auth"]
    if not ok or head is None or f"git:{head}" != bound.target_state_ref:
        raise RuntimeError("attestor_runtime_probe_failed")

    observed_at = time.time()
    generation = time.time_ns()
    snapshot_base = {
        "schema": SNAPSHOT_SCHEMA,
        "node_id": DIAGNOSTIC_NODE_ID,
        "generation": generation,
        "route_rank": DIAGNOSTIC_ROUTE_RANK,
        "capabilities": list(capabilities),
        "supported_adapters": [bound.binding.adapter_id],
        "supported_lanes": [bound.binding.lane.value],
        "privacy_classes": [bound.universal_task.privacy.value],
        "resource_patterns": list(bound.universal_task.target_resources),
        "observed_at": observed_at,
        "expires_at": observed_at + DIAGNOSTIC_TTL_SECONDS,
    }
    snapshot_digest = _sha256_json(snapshot_base)
    snapshot = {
        **snapshot_base,
        "attestation_ref": f"attestation:external-boundary:{snapshot_digest[:32]}",
    }
    envelope_base = {
        "schema": ENVELOPE_SCHEMA,
        "repository": repository,
        "target_issue": target_issue,
        "profile": profile,
        "route": ROUTE_DIAGNOSTIC,
        "binding_sha256": bound.source_binding_hash,
        "idempotency_sha256": _sha256_text(expected_idempotency_key),
        "generation": generation,
        "snapshot": snapshot,
    }
    envelope = {**envelope_base, "payload_sha256": _sha256_json(envelope_base)}
    root = _safe_state_root()
    _write_private_snapshot(root, envelope)
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "DONE",
        "binding_sha256": bound.source_binding_hash,
        "idempotency_sha256": _sha256_text(expected_idempotency_key),
        "snapshot_sha256": _sha256_json(snapshot),
        "generation": generation,
        "observed_at": observed_at,
        "expires_at": observed_at + DIAGNOSTIC_TTL_SECONDS,
        "ttl_seconds": DIAGNOSTIC_TTL_SECONDS,
        "probes": {key: bool(value) for key, value in sorted(probes.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target-issue", type=int, required=True)
    parser.add_argument("--expected-binding-sha256", required=True)
    parser.add_argument("--expected-idempotency-key", required=True)
    parser.add_argument("--profile", required=True, choices=(DIAGNOSTIC_PROFILE,))
    args = parser.parse_args(argv)
    try:
        receipt = produce_external_attestation(
            repository=args.repository,
            target_issue=args.target_issue,
            expected_binding_sha256=args.expected_binding_sha256,
            expected_idempotency_key=args.expected_idempotency_key,
            profile=args.profile,
        )
    except (ValueError, RuntimeError) as exc:
        return _fail(str(exc))
    sys.stdout.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

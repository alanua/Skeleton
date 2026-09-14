from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.runner_poll_github_tasks as legacy
from core.runner_task import RunnerTask as CoreRunnerTask
from core.runner_vnext_authoritative_dispatch import (
    MechanicalResult,
    PrivilegedExecutionGrant,
    RunnerVNextDispatchError,
    run_green_authoritative_dispatch,
    run_privileged_authoritative_dispatch,
)
from core.runner_vnext_authority import (
    PrivilegedAuthorityInput,
    ROUTE_CODE_GENERATION,
    ROUTE_DIAGNOSTIC,
    ROUTE_MERGE,
    ROUTE_PUBLISH_ONLY,
    ROUTE_RECOVERY,
    ROUTE_RUNTIME_ONLY,
    ROUTE_VALIDATION,
    RunnerVNextAuthorityError,
    RunnerVNextRuntimeConfig,
    bind_privileged_operation,
    bind_runner_operation,
    build_authoritative_stores,
)
from core.runner_vnext_contracts import PrivacyClass
from core.runner_vnext_execution_gate import GreenExecutionGrant
from core.runner_vnext_leases import Lane
from core.runner_vnext_routing import NodeCapabilitySnapshot

VNEXT_MODE = "authoritative"
ATTESTATION_TTL_SECONDS = 45.0
EXTERNAL_NODE_SNAPSHOT_SCHEMA = "skeleton.runner_vnext_node_capability_snapshot.v1"
EXTERNAL_ATTESTATION_ENVELOPE_SCHEMA = "skeleton.runner_vnext_external_attestation_envelope.v1"
EXTERNAL_ATTESTATION_PROFILE = "green_diagnostic_v1"
EXTERNAL_ATTESTATION_FILENAME = "runner-vnext-external-attestation.json"
DIAGNOSTIC_MODE = "RUNNER_VNEXT_GREEN_DIAGNOSTIC"
DIAGNOSTIC_NODE_ID = "node:runner-vnext-harmless-diagnostic-runtime"
DIAGNOSTIC_ROUTE_RANK = 5
DIAGNOSTIC_ALLOWED_FILE = "docs/RUNNER_MAINTENANCE_TASKS.md"
DIAGNOSTIC_APPROVAL_REFERENCE = "runner_vnext_green_lifecycle_canary_v1"
_EXTERNAL_NODE_SNAPSHOT_KEYS = frozenset(
    (
        "schema",
        "node_id",
        "generation",
        "route_rank",
        "capabilities",
        "supported_adapters",
        "supported_lanes",
        "privacy_classes",
        "resource_patterns",
        "observed_at",
        "expires_at",
        "attestation_ref",
    )
)
_EXTERNAL_ATTESTATION_ENVELOPE_KEYS = frozenset(
    (
        "schema",
        "repository",
        "target_issue",
        "profile",
        "route",
        "binding_sha256",
        "idempotency_sha256",
        "generation",
        "snapshot",
        "payload_sha256",
    )
)


class VNextPollerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@contextmanager
def _temporary_environment(values: dict[str, str | None]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _CallableTargetVerifier:
    def __init__(self, current_ref: Callable[[], str]) -> None:
        self._current_ref = current_ref

    def current_ref(self, _handoff: object) -> str:
        return self._current_ref()


class _LegacyGreenBackend:
    def __init__(
        self,
        *,
        item: object,
        workdir: str | None,
        state_ref: Callable[[], str],
    ) -> None:
        self._item = item
        self._workdir = workdir
        self._state_ref = state_ref

    def execute(self, *, bound: object, grant: GreenExecutionGrant) -> MechanicalResult:
        before = self._state_ref()
        with _temporary_environment({legacy.RUNNER_VNEXT_MODE_ENV: "off"}):
            legacy.process_issue(
                self._item.issue,
                workdir=self._workdir,
                source_repository=self._item.source_repository,
            )
        labels = legacy.get_issue_labels(
            self._item.issue_number,
            self._item.source_repository,
        )
        succeeded = legacy.LABEL_DONE in labels and legacy.LABEL_BLOCKED not in labels
        after = self._state_ref()
        touched = tuple(grant.planner_envelope.resources) if succeeded or before != after else ()
        if succeeded and after == before and getattr(bound, "operation") == "publication":
            after = f"issue:{self._item.issue_number}:published"
        return MechanicalResult(
            succeeded=succeeded,
            reason_code="LEGACY_MECHANICS_PASS" if succeeded else "LEGACY_MECHANICS_FAIL",
            touched_resources=touched,
            before_state_ref=before,
            after_state_ref=after,
            validation_status="PASS" if succeeded else "FAIL",
            rollback_requested=(not succeeded and before != after),
        )


class _LegacyPrivilegedBackend:
    def __init__(
        self,
        *,
        item: object,
        workdir: str | None,
        state_ref: Callable[[], str],
    ) -> None:
        self._item = item
        self._workdir = workdir
        self._state_ref = state_ref

    def execute(
        self,
        *,
        bound: object,
        broker_request: PrivilegedExecutionGrant,
    ) -> MechanicalResult:
        if not broker_request.execution_authorized:
            raise VNextPollerError("VNEXT_PRIVILEGED_GRANT_REQUIRED")
        before = self._state_ref()
        with _temporary_environment({legacy.RUNNER_VNEXT_MODE_ENV: "off"}):
            legacy.process_issue(
                self._item.issue,
                workdir=self._workdir,
                source_repository=self._item.source_repository,
            )
        labels = legacy.get_issue_labels(
            self._item.issue_number,
            self._item.source_repository,
        )
        succeeded = legacy.LABEL_DONE in labels and legacy.LABEL_BLOCKED not in labels
        after = self._state_ref()
        touched = tuple(broker_request.resources) if succeeded or before != after else ()
        if succeeded and after == before:
            after = f"issue:{self._item.issue_number}:completed"
        return MechanicalResult(
            succeeded=succeeded,
            reason_code="LEGACY_MECHANICS_PASS" if succeeded else "LEGACY_MECHANICS_FAIL",
            touched_resources=touched,
            before_state_ref=before,
            after_state_ref=after,
            validation_status="PASS" if succeeded else "FAIL",
            rollback_requested=(not succeeded and before != after),
        )


class _HarmlessDiagnosticBackend:
    def execute(self, *, bound: object, grant: GreenExecutionGrant) -> MechanicalResult:
        if grant.adapter_id != "adapter:harmless-diagnostic":
            raise VNextPollerError("VNEXT_DIAGNOSTIC_ADAPTER_MISMATCH")
        return MechanicalResult(
            succeeded=True,
            reason_code="EXECUTION_HARMLESS_DIAGNOSTIC_PASS",
            touched_resources=tuple(grant.planner_envelope.resources),
            before_state_ref=bound.target_state_ref,
            after_state_ref=bound.target_state_ref,
            validation_status="PASS",
            rollback_requested=False,
        )


def _body_field(body: str, field: str) -> str | None:
    return legacy._body_field((body or "").split("```task", 1)[0], field)


def _exact_body_field(body: str, field: str) -> str:
    prefix = field + ":"
    values = [line[len(prefix):].strip() for line in (body or "").splitlines() if line.startswith(prefix)]
    if len(values) != 1 or not values[0]:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_CONTRACT_INVALID")
    return values[0]


def _safe_sha(value: str | None, reason: str) -> str:
    normalized = (value or "").lower()
    if len(normalized) != 40 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise VNextPollerError(reason)
    return normalized


def _safe_sha256(value: str | None, reason: str) -> str:
    normalized = (value or "").lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise VNextPollerError(reason)
    return normalized


def _safe_positive_int(value: str | None, reason: str) -> int:
    if not isinstance(value, str) or not value.isdecimal() or int(value) < 1:
        raise VNextPollerError(reason)
    return int(value)


def _safe_branch(value: str | None, fallback: str) -> str:
    candidate = (value or fallback).strip()
    if not candidate or ".." in candidate or candidate.startswith(("/", "~")):
        raise VNextPollerError("VNEXT_BRANCH_INVALID")
    return candidate


def _approval_token(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _idempotency_token(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]
    return f"{prefix}:{digest}"


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _changed_files_from_metadata(body: str) -> tuple[str, ...]:
    metadata = (body or "").split("```task", 1)[0]
    files, reason = legacy._issue_publish_allowed_files(metadata)
    if reason is not None or not files:
        raise VNextPollerError(reason or "VNEXT_ALLOWED_FILES_REQUIRED")
    return tuple(sorted(files))


def _pr_head_ref(repository: str, pr_number: int) -> str:
    code, output = legacy.run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repository,
            "--json",
            "headRefOid",
            "--jq",
            ".headRefOid",
        ]
    )
    if code != 0:
        raise VNextPollerError("VNEXT_PR_HEAD_UNAVAILABLE")
    return f"git:{_safe_sha(output.strip(), 'VNEXT_PR_HEAD_INVALID')}"


def _worktree_head_ref(source_issue: int) -> str:
    path = legacy.worktree_root() / f"issue-{source_issue}"
    code, output = legacy.run_command(["git", "rev-parse", "HEAD"], cwd=path)
    if code != 0:
        raise VNextPollerError("VNEXT_SOURCE_WORKTREE_HEAD_UNAVAILABLE")
    return f"git:{_safe_sha(output.strip(), 'VNEXT_SOURCE_WORKTREE_HEAD_INVALID')}"


def _current_repo_head_ref() -> str:
    code, output = legacy.run_command(["git", "rev-parse", "HEAD"], cwd=ROOT)
    if code != 0:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_HEAD_UNAVAILABLE")
    return f"git:{_safe_sha(output.strip(), 'VNEXT_DIAGNOSTIC_HEAD_INVALID')}"


def _issue_state_ref(repository: str, issue_number: int) -> str:
    code, output = legacy.run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repository,
            "--json",
            "body,state,labels",
        ]
    )
    if code != 0:
        raise VNextPollerError("VNEXT_ISSUE_STATE_UNAVAILABLE")
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise VNextPollerError("VNEXT_ISSUE_STATE_INVALID") from exc
    digest = hashlib.sha256(
        json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"issue-state:{digest}"


def _fetch_exact_issue(repository: str, issue_number: int) -> dict[str, object]:
    if repository != legacy.REPO or issue_number < 1:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_SELECTOR_INVALID")
    code, output = legacy.run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repository,
            "--json",
            "number,body,state",
        ]
    )
    if code != 0:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_ISSUE_UNAVAILABLE")
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_ISSUE_INVALID") from exc
    if set(parsed) != {"number", "body", "state"}:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_ISSUE_INVALID")
    if parsed.get("number") != issue_number or parsed.get("state") != "OPEN" or not isinstance(parsed.get("body"), str):
        raise VNextPollerError("VNEXT_DIAGNOSTIC_ISSUE_INVALID")
    return parsed


def _validation_task(item: object, body: str) -> tuple[CoreRunnerTask, int]:
    repository = _body_field(body, "Repository") or item.source_repository
    pr_number = _safe_positive_int(_body_field(body, "Pull Request"), "VNEXT_VALIDATION_PR_REQUIRED")
    head_sha = _safe_sha(_body_field(body, "Expected Head SHA"), "VNEXT_VALIDATION_HEAD_REQUIRED")
    base_sha = _safe_sha(_body_field(body, "Expected Base SHA"), "VNEXT_VALIDATION_BASE_REQUIRED")
    profile = _body_field(body, "Validation Profile") or "full_pytest"
    files = _changed_files_from_metadata(body)
    seed = f"{repository}:{pr_number}:{head_sha}:{base_sha}:{profile}:{','.join(files)}"
    task = CoreRunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": repository,
            "branch": f"pr-{pr_number}",
            "base_sha": head_sha,
            "task_kind": "repository_maintenance",
            "payload": {
                "operation": "validate_pr_branch",
                "pr_number": pr_number,
                "expected_head_sha": head_sha,
                "expected_base_sha": base_sha,
                "profile": profile,
            },
            "requested_capabilities": ["repository_read", "test_execution"],
            "allowed_files": list(files),
            "forbidden_actions": ["merge", "runtime_activation", "deploy"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 3600,
            "expected_output": ["trusted exact-head validation receipt"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": _approval_token("validation", seed),
            "idempotency_key": _idempotency_token("validation", seed),
        }
    )
    return task, pr_number


def _publication_task(item: object, body: str) -> tuple[CoreRunnerTask, int]:
    repository = _body_field(body, "Target Repository") or _body_field(body, "Repository") or item.source_repository
    source_issue = _safe_positive_int(_body_field(body, "Source Issue"), "VNEXT_PUBLICATION_SOURCE_ISSUE_REQUIRED")
    output_branch = _safe_branch(
        _body_field(body, "Output Branch") or _body_field(body, "Expected Source Branch"),
        f"runner/issue-{source_issue}",
    )
    files = _changed_files_from_metadata(body)
    head_ref = _worktree_head_ref(source_issue)
    head_sha = head_ref.removeprefix("git:")
    seed = f"{repository}:{source_issue}:{output_branch}:{head_sha}:{','.join(files)}"
    task = CoreRunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": repository,
            "branch": output_branch,
            "base_sha": head_sha,
            "task_kind": "publish",
            "payload": {
                "operation": "draft_publication",
                "source_issue": source_issue,
                "output_branch": output_branch,
            },
            "requested_capabilities": [
                "repository_read",
                "repository_write_allowlisted",
                "publish_pull_request",
            ],
            "allowed_files": list(files),
            "forbidden_actions": ["merge", "runtime_activation", "deploy"],
            "validation_commands": [["git", "diff", "--check"]],
            "validation_timeout_seconds": 900,
            "expected_output": ["one idempotent draft publication"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": _approval_token("publication", seed),
            "idempotency_key": _idempotency_token("publication", seed),
        }
    )
    return task, source_issue


def _diagnostic_bound(repository: str, issue_number: int, body: str, expected_idempotency_key: str):
    if repository != legacy.REPO:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_REPOSITORY_INVALID")
    if _exact_body_field(body, "Mode") != DIAGNOSTIC_MODE:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_CONTRACT_INVALID")
    if _exact_body_field(body, "Repository") != repository:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_CONTRACT_INVALID")
    if _exact_body_field(body, "Profile") != EXTERNAL_ATTESTATION_PROFILE:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_CONTRACT_INVALID")
    if _exact_body_field(body, "Idempotency Key") != expected_idempotency_key:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_IDEMPOTENCY_MISMATCH")
    main_sha = _safe_sha(_exact_body_field(body, "Expected Main SHA"), "VNEXT_DIAGNOSTIC_MAIN_SHA_INVALID")
    task = CoreRunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": repository,
            "branch": "main",
            "base_sha": main_sha,
            "task_kind": "diagnostic",
            "payload": {
                "operation": "runner_vnext_green_diagnostic",
                "profile": EXTERNAL_ATTESTATION_PROFILE,
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


def _stores() -> object:
    root = os.environ.get(legacy.RUNNER_VNEXT_STATE_ROOT_ENV, "")
    ledger = os.environ.get(legacy.RUNNER_VNEXT_LEDGER_DB_ENV, "")
    lease = os.environ.get(legacy.RUNNER_VNEXT_LEASE_DB_ENV, "")
    config = RunnerVNextRuntimeConfig(
        state_root=root,
        ledger_db_path=ledger,
        lease_db_path=lease,
    )
    return build_authoritative_stores(config, clock=time.time)


def _private_root() -> Path:
    raw = os.environ.get(legacy.RUNNER_VNEXT_STATE_ROOT_ENV, "")
    if not raw or raw == ":memory:":
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STATE_ROOT_INVALID")
    root = Path(raw)
    if not root.is_absolute():
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STATE_ROOT_INVALID")
    try:
        info = root.lstat()
    except OSError as exc:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STATE_ROOT_INVALID") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STATE_ROOT_INVALID")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STATE_ROOT_INVALID")
    return root


def _private_regular_file(path: Path, root: Path, reason: str) -> None:
    try:
        root_resolved = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise VNextPollerError(reason) from exc
    try:
        relative = resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise VNextPollerError(reason) from exc
    if not relative.parts:
        raise VNextPollerError(reason)
    cursor = root_resolved
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise VNextPollerError(reason)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise VNextPollerError(reason)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise VNextPollerError(reason)


def _require_existing_persistent_stores() -> None:
    root = _private_root()
    raw_ledger = os.environ.get(legacy.RUNNER_VNEXT_LEDGER_DB_ENV, "")
    raw_lease = os.environ.get(legacy.RUNNER_VNEXT_LEASE_DB_ENV, "")
    if not raw_ledger or not raw_lease or raw_ledger == ":memory:" or raw_lease == ":memory:":
        raise VNextPollerError("VNEXT_PERSISTENT_STORES_REQUIRED")
    ledger = Path(raw_ledger)
    lease = Path(raw_lease)
    if not ledger.is_absolute() or not lease.is_absolute() or ledger == lease:
        raise VNextPollerError("VNEXT_PERSISTENT_STORES_REQUIRED")
    _private_regular_file(ledger, root, "VNEXT_PERSISTENT_LEDGER_INVALID")
    _private_regular_file(lease, root, "VNEXT_PERSISTENT_LEASE_INVALID")


def _read_private_snapshot_bytes(path: Path, root: Path) -> bytes:
    _private_regular_file(path, root, "VNEXT_EXTERNAL_ATTESTATION_FILE_INVALID")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_FILE_INVALID") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_FILE_INVALID")
        data = bytearray()
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 65536:
                raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_FILE_OVERSIZE")
        return bytes(data)
    finally:
        os.close(fd)


def _attest(
    bound: object,
    *,
    expected_repository: str | None = None,
    expected_issue: int | None = None,
    expected_binding_sha256: str | None = None,
    expected_idempotency_key: str | None = None,
    now: float | None = None,
) -> tuple[NodeCapabilitySnapshot, dict[str, object]]:
    if os.environ.get(legacy.RUNNER_VNEXT_NODE_SNAPSHOT_JSON_ENV, "").strip():
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_ENV_FORBIDDEN")
    current_time = time.time() if now is None else now
    source_ref = str(getattr(bound, "source_task_ref", ""))
    if not source_ref.startswith("issue:") or not source_ref.removeprefix("issue:").isdigit():
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SOURCE_INVALID")
    issue_number = int(source_ref.removeprefix("issue:"))
    repository = expected_repository or (
        bound.runner_task.repo if getattr(bound, "runner_task", None) is not None else legacy.REPO
    )
    if expected_issue is not None and issue_number != expected_issue:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_ISSUE_MISMATCH")
    binding = _safe_sha256(
        expected_binding_sha256 or str(getattr(bound, "source_binding_hash", "")),
        "VNEXT_EXTERNAL_ATTESTATION_BINDING_INVALID",
    )
    if binding != str(getattr(bound, "source_binding_hash", "")):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_BINDING_MISMATCH")
    runner_task = getattr(bound, "runner_task", None)
    idempotency_key = expected_idempotency_key or (
        runner_task.idempotency_key if runner_task is not None else bound.operation_ir.idempotency_key
    )
    if runner_task is not None and runner_task.idempotency_key != idempotency_key:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_IDEMPOTENCY_MISMATCH")

    root = _private_root()
    path = root / EXTERNAL_ATTESTATION_FILENAME
    try:
        payload = json.loads(_read_private_snapshot_bytes(path, root).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_MALFORMED") from exc
    if not isinstance(payload, dict) or frozenset(payload) != _EXTERNAL_ATTESTATION_ENVELOPE_KEYS:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_ENVELOPE_INVALID")
    if payload["schema"] != EXTERNAL_ATTESTATION_ENVELOPE_SCHEMA:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SCHEMA_INVALID")
    if payload["repository"] != repository or payload["target_issue"] != issue_number:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_IDENTITY_MISMATCH")
    if payload["profile"] != EXTERNAL_ATTESTATION_PROFILE or payload["route"] != getattr(bound, "route"):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_ROUTE_MISMATCH")
    if payload["binding_sha256"] != binding:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_BINDING_MISMATCH")
    if payload["idempotency_sha256"] != _sha256_text(idempotency_key):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_IDEMPOTENCY_MISMATCH")
    base = {key: value for key, value in payload.items() if key != "payload_sha256"}
    if payload["payload_sha256"] != _sha256_json(base):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_DIGEST_MISMATCH")

    raw = payload["snapshot"]
    if not isinstance(raw, dict) or frozenset(raw) != _EXTERNAL_NODE_SNAPSHOT_KEYS:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SNAPSHOT_INVALID")
    if raw["schema"] != EXTERNAL_NODE_SNAPSHOT_SCHEMA:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SNAPSHOT_SCHEMA_INVALID")
    generation = raw["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1 or payload["generation"] != generation:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_GENERATION_MISMATCH")
    expected = (
        tuple(bound.universal_task.required_capabilities),
        (bound.binding.adapter_id,),
        (bound.binding.lane.value,),
        (bound.universal_task.privacy.value,),
        tuple(bound.universal_task.target_resources),
    )
    try:
        actual = (
            tuple(raw["capabilities"]),
            tuple(raw["supported_adapters"]),
            tuple(raw["supported_lanes"]),
            tuple(raw["privacy_classes"]),
            tuple(raw["resource_patterns"]),
        )
    except TypeError as exc:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SCOPE_INVALID") from exc
    if actual != expected:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SCOPE_MISMATCH")
    if getattr(bound, "route") == ROUTE_DIAGNOSTIC:
        if raw["node_id"] != DIAGNOSTIC_NODE_ID or raw["route_rank"] != DIAGNOSTIC_ROUTE_RANK:
            raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_NODE_MISMATCH")
    observed_at = raw["observed_at"]
    expires_at = raw["expires_at"]
    if isinstance(observed_at, bool) or isinstance(expires_at, bool):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_TIME_INVALID")
    try:
        observed = float(observed_at)
        expires = float(expires_at)
    except (TypeError, ValueError) as exc:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_TIME_INVALID") from exc
    if observed > current_time or expires <= current_time or expires <= observed:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STALE")
    if expires - observed > ATTESTATION_TTL_SECONDS:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_TTL_INVALID")
    attestation_ref = raw["attestation_ref"]
    if not isinstance(attestation_ref, str) or not attestation_ref.startswith("attestation:external-boundary:"):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_REF_INVALID")
    if any(token in attestation_ref.lower() for token in ("private", "secret", "/", "\\")):
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_REF_INVALID")
    try:
        snapshot = NodeCapabilitySnapshot(
            node_id=str(raw["node_id"]),
            generation=generation,
            route_rank=int(raw["route_rank"]),
            capabilities=tuple(raw["capabilities"]),
            supported_adapters=tuple(raw["supported_adapters"]),
            supported_lanes=tuple(Lane(value) for value in raw["supported_lanes"]),
            privacy_classes=tuple(PrivacyClass(value) for value in raw["privacy_classes"]),
            resource_patterns=tuple(raw["resource_patterns"]),
            observed_at=observed,
            expires_at=expires,
            attestation_ref=attestation_ref,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_SCOPE_INVALID") from exc
    return snapshot, payload


def _codegen_bound(item: object, body: str, legacy_task: object) -> object:
    metadata = legacy.normalized_runner_shadow_metadata(
        issue_number=item.issue_number,
        issue_body=body,
        route=legacy.ROUTE_CODE_GENERATION,
        maintenance_task_id=None,
        runner_task=legacy_task,
        merge_request=None,
    )
    typed = legacy.runner_task_from_normalized_metadata(metadata, "code_edit")
    return bind_runner_operation(
        runner_task=typed,
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
        source_task_ref=f"issue:{item.issue_number}",
    )


def _dispatch_codegen(item: object, body: str, legacy_task: object, workdir: str | None) -> None:
    bound = _codegen_bound(item, body, legacy_task)
    _attest(bound)
    with _temporary_environment({legacy.RUNNER_VNEXT_MODE_ENV: VNEXT_MODE}):
        legacy.process_issue(
            item.issue,
            workdir=workdir,
            source_repository=item.source_repository,
        )


def _dispatch_validation(item: object, body: str, workdir: str | None) -> None:
    task, pr_number = _validation_task(item, body)
    bound = bind_runner_operation(
        runner_task=task,
        route=ROUTE_VALIDATION,
        operation="validation",
        source_task_ref=f"issue:{item.issue_number}",
    )
    snapshot, _raw = _attest(bound)
    current_ref = lambda: _pr_head_ref(task.repo, pr_number)
    if current_ref() != bound.target_state_ref:
        raise VNextPollerError("VNEXT_VALIDATION_HEAD_STALE")
    stores = _stores()
    try:
        receipt = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(current_ref),
            backend=_LegacyGreenBackend(item=item, workdir=workdir, state_ref=current_ref),
            now=time.time(),
            ttl_seconds=float(task.validation_timeout_seconds),
            parent_environment=dict(os.environ),
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def _dispatch_publication(item: object, body: str, workdir: str | None) -> None:
    task, source_issue = _publication_task(item, body)
    bound = bind_runner_operation(
        runner_task=task,
        route=ROUTE_PUBLISH_ONLY,
        operation="publication",
        source_task_ref=f"issue:{item.issue_number}",
    )
    snapshot, _raw = _attest(bound)
    current_ref = lambda: _worktree_head_ref(source_issue)
    if current_ref() != bound.target_state_ref:
        raise VNextPollerError("VNEXT_PUBLICATION_SOURCE_STALE")
    stores = _stores()
    try:
        receipt = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(current_ref),
            backend=_LegacyGreenBackend(item=item, workdir=workdir, state_ref=current_ref),
            now=time.time(),
            ttl_seconds=float(task.validation_timeout_seconds),
            parent_environment=dict(os.environ),
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def _trusted_evidence(item: object, body: str, route: str, maintenance_task_id: str | None) -> tuple[str, ...]:
    comments = legacy.get_issue_comments(item.issue, item.source_repository)
    decision = legacy.route_authority_decision_for_issue(
        issue_number=item.issue_number,
        body=body,
        route=route,
        maintenance_task_id=maintenance_task_id,
        comments=comments,
    )
    if not decision.allowed:
        raise VNextPollerError(decision.reason or "VNEXT_OPERATOR_AUTHORITY_REQUIRED")
    if decision.required_reference:
        return (decision.required_reference,)
    raise VNextPollerError("VNEXT_OPERATOR_AUTHORITY_REQUIRED")


def _dispatch_control(
    item: object,
    body: str,
    maintenance_task_id: str,
    *,
    recovery: bool,
    workdir: str | None,
) -> None:
    route = ROUTE_RECOVERY if recovery else ROUTE_RUNTIME_ONLY
    operation = "recovery" if recovery else "control"
    state_ref = lambda: _issue_state_ref(item.source_repository, item.issue_number)
    target_state = state_ref()
    evidence = _trusted_evidence(item, body, legacy.ROUTE_RUNTIME_ONLY, maintenance_task_id)
    seed = f"{item.source_repository}:{item.issue_number}:{maintenance_task_id}:{target_state}"
    bound = bind_privileged_operation(
        route=route,
        operation=operation,
        authority_input=PrivilegedAuthorityInput(
            source_task_ref=f"issue:{item.issue_number}",
            target_state_ref=target_state,
            resources=(
                f"control:{maintenance_task_id}",
                f"repo:{item.source_repository}",
            ),
            required_capabilities=(
                "repository_maintenance",
                "diagnostic_read",
                "subprocess_isolated",
            ),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=evidence,
            idempotency_seed=_idempotency_token(operation, seed),
        ),
    )
    snapshot, _raw = _attest(bound)
    stores = _stores()
    try:
        receipt = run_privileged_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(state_ref),
            backend=_LegacyPrivilegedBackend(item=item, workdir=workdir, state_ref=state_ref),
            evidence_refs=evidence,
            fresh_authority=False,
            now=time.time(),
            ttl_seconds=900.0,
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def _dispatch_merge(item: object, body: str, merge_request: object, workdir: str | None) -> None:
    if not legacy.telegram_approve_digest_is_signed(merge_request):
        raise VNextPollerError("VNEXT_MERGE_SIGNED_APPROVAL_REQUIRED")
    try:
        pr_state = legacy.get_pr_merge_state(merge_request.pr_number)
    except Exception as exc:
        raise VNextPollerError("VNEXT_MERGE_APPROVAL_AUDIT_UNAVAILABLE") from exc
    block_reason = legacy._pr_merge_block_reason(merge_request, pr_state)
    if block_reason is not None:
        raise VNextPollerError("VNEXT_MERGE_APPROVAL_AUDIT_INVALID")
    state_ref = lambda: _pr_head_ref(legacy.REPO, merge_request.pr_number)
    target_state = f"git:{merge_request.approved_head_sha}"
    if state_ref() != target_state:
        raise VNextPollerError("VNEXT_MERGE_HEAD_STALE")
    evidence = (
        f"telegram:{merge_request.callback_digest}",
        f"head:{merge_request.approved_head_sha}",
    )
    seed = f"{merge_request.pr_number}:{merge_request.approved_head_sha}:{merge_request.callback_digest}"
    bound = bind_privileged_operation(
        route=ROUTE_MERGE,
        operation="merge",
        authority_input=PrivilegedAuthorityInput(
            source_task_ref=f"issue:{item.issue_number}",
            target_state_ref=target_state,
            resources=(
                f"protected:pr-{merge_request.pr_number}",
                f"repo:{legacy.REPO}",
            ),
            required_capabilities=("publish_pull_request",),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=evidence,
            idempotency_seed=_idempotency_token("merge", seed),
        ),
    )
    snapshot, _raw = _attest(bound)
    stores = _stores()
    try:
        receipt = run_privileged_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(state_ref),
            backend=_LegacyPrivilegedBackend(item=item, workdir=workdir, state_ref=state_ref),
            evidence_refs=evidence,
            fresh_authority=True,
            now=time.time(),
            ttl_seconds=300.0,
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def run_exact_green_canary(
    *,
    repository: str,
    target_issue: int,
    expected_binding_sha256: str,
    expected_idempotency_key: str,
) -> dict[str, object]:
    expected_binding = _safe_sha256(
        expected_binding_sha256,
        "VNEXT_DIAGNOSTIC_BINDING_INVALID",
    )
    if not expected_idempotency_key or len(expected_idempotency_key) > 128:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_IDEMPOTENCY_INVALID")
    issue = _fetch_exact_issue(repository, target_issue)
    body = str(issue["body"])
    bound = _diagnostic_bound(repository, target_issue, body, expected_idempotency_key)
    if bound.source_binding_hash != expected_binding:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_BINDING_MISMATCH")
    current_ref = _current_repo_head_ref
    if current_ref() != bound.target_state_ref:
        raise VNextPollerError("VNEXT_DIAGNOSTIC_HEAD_STALE")
    now = time.time()
    snapshot, _envelope = _attest(
        bound,
        expected_repository=repository,
        expected_issue=target_issue,
        expected_binding_sha256=expected_binding,
        expected_idempotency_key=expected_idempotency_key,
        now=now,
    )
    _require_existing_persistent_stores()
    stores = _stores()
    try:
        receipt = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(current_ref),
            backend=_HarmlessDiagnosticBackend(),
            now=now,
            ttl_seconds=ATTESTATION_TTL_SECONDS,
            parent_environment=dict(os.environ),
        )
    finally:
        stores.close()
    public = receipt.to_public_mapping()
    public["schema"] = "skeleton.runner_vnext_exact_green_canary_receipt.v1"
    public["status"] = "DONE" if receipt.terminal_status == "COMPLETED" else "BLOCKED"
    public["profile"] = EXTERNAL_ATTESTATION_PROFILE
    public["binding_sha256"] = expected_binding
    public["idempotency_sha256"] = _sha256_text(expected_idempotency_key)
    return public


def dispatch_item(item: object, *, workdir: str | None = None) -> None:
    body = str(item.issue.get("body") or "")
    maintenance_mode, maintenance_task_id = legacy.extract_runtime_maintenance_task_id(body)
    merge_mode, merge_request, merge_reason = legacy.extract_telegram_approved_pr_merge_request(body)
    if merge_mode and (merge_request is None or merge_reason is not None):
        raise VNextPollerError("VNEXT_MERGE_REQUEST_INVALID")
    legacy_task, task_reason = legacy.extract_runner_task(
        body,
        default_repository=item.source_repository,
    )
    if task_reason is not None:
        raise VNextPollerError(task_reason)

    if merge_mode:
        _dispatch_merge(item, body, merge_request, workdir)
        return
    if maintenance_mode:
        if maintenance_task_id is None:
            raise VNextPollerError("VNEXT_MAINTENANCE_TASK_ID_REQUIRED")
        if maintenance_task_id == legacy.VALIDATE_PR_BRANCH:
            _dispatch_validation(item, body, workdir)
            return
        if maintenance_task_id in legacy.PUBLISH_ONLY_MAINTENANCE_TASK_IDS:
            _dispatch_publication(item, body, workdir)
            return
        if maintenance_task_id in legacy.RECOVERY_ONLY_MAINTENANCE_TASK_IDS:
            _dispatch_control(
                item,
                body,
                maintenance_task_id,
                recovery=True,
                workdir=workdir,
            )
            return
        _dispatch_control(
            item,
            body,
            maintenance_task_id,
            recovery=False,
            workdir=workdir,
        )
        return
    if legacy_task is None:
        raise VNextPollerError("VNEXT_TYPED_TASK_REQUIRED")
    _dispatch_codegen(item, body, legacy_task, workdir)


def _block_dispatch_error(item: object, error: Exception) -> None:
    reason = getattr(error, "reason_code", "VNEXT_AUTHORITATIVE_DISPATCH_FAILED")
    legacy.block_issue(
        item.issue_number,
        f"Runner vNext authoritative dispatch blocked before unsafe fallback. reason={reason}",
    )


def poll_once(workdir: str | None = None) -> int:
    try:
        legacy.reconcile_scheduler_on_poll()
    except Exception:
        pass
    try:
        legacy.reconcile_terminal_issues_active_execution_labels()
    except Exception:
        pass
    source_token = legacy._QUEUE_RECOVERY_SOURCE.set("vnext-authoritative")
    try:
        legacy.self_heal_run_now_queue_intake()
    finally:
        legacy._QUEUE_RECOVERY_SOURCE.reset(source_token)
    items = legacy.get_ready_issue_items()
    for item in items:
        queue_token = legacy._CURRENT_QUEUE_REPOSITORY.set(item.source_repository)
        try:
            try:
                dispatch_item(item, workdir=workdir)
            except Exception as exc:
                _block_dispatch_error(item, exc)
        finally:
            legacy._CURRENT_QUEUE_REPOSITORY.reset(queue_token)
    return len(items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--workdir")
    parser.add_argument("--exact-green-canary", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--target-issue", type=int)
    parser.add_argument("--expected-binding-sha256")
    parser.add_argument("--expected-idempotency-key")
    args = parser.parse_args(argv)
    if args.exact_green_canary:
        if args.once or args.workdir:
            return 2
        if not args.repository or not args.target_issue or not args.expected_binding_sha256 or not args.expected_idempotency_key:
            return 2
        try:
            receipt = run_exact_green_canary(
                repository=args.repository,
                target_issue=args.target_issue,
                expected_binding_sha256=args.expected_binding_sha256,
                expected_idempotency_key=args.expected_idempotency_key,
            )
        except (VNextPollerError, RunnerVNextAuthorityError, RunnerVNextDispatchError) as exc:
            sys.stderr.write(getattr(exc, "reason_code", "VNEXT_EXACT_GREEN_CANARY_FAILED") + "\n")
            return 2
        sys.stdout.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    if args.once:
        poll_once(workdir=args.workdir)
        return 0
    while True:
        poll_once(workdir=args.workdir)
        time.sleep(legacy.POLL_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
import threading

from jsonschema import Draft202012Validator

from core.audit_ledger import validate_public_safe_payload
from core.runner_executor_registry import RegisteredCommandResult, RunnerExecutorRegistry


SCHEMA_ID = "skeleton.runner_task.v1"
LEGACY_SCHEMA_IDS = frozenset(
    {
        "skeleton.universal_runner_task.v1",
        "skeleton.runner_task.preview.v1",
    }
)
TASK_STATUSES = frozenset(
    {"CHECKPOINTED", "NEEDS_OPERATOR", "RUNNING", "CANCELLED", "FAILED", "COMPLETED"}
)
TERMINAL_STATUSES = frozenset({"CANCELLED", "FAILED", "COMPLETED"})
RISK_LEVELS = frozenset({"LOW", "YELLOW", "RED"})
TASK_ACTIONS = frozenset({"START", "STATUS", "CONTINUE", "CANCEL"})
EXECUTOR_TYPES = frozenset(
    {
        "codex_branch_task",
        "hermes_private_task",
        "local_module_task",
        "runtime_maintenance_task",
        "read_only_probe",
    }
)
STALE_LEASE_SECONDS = 15 * 60
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "universal_runner_task.schema.json"


@dataclass(frozen=True)
class ApprovalEvidence:
    source: str
    evidence_id: str
    verified: bool


@dataclass(frozen=True)
class RunnerTaskEnvelope:
    task_id: str
    task_key: str
    action: str
    executor_type: str
    risk: str
    payload: dict[str, Any]
    resources: tuple[Any, ...] = ()
    approval_evidence: ApprovalEvidence | None = None
    timeout_seconds: float = 60.0
    allowed_scope: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()
    privacy_boundary: str | None = None
    schema_id: str = SCHEMA_ID
    legacy_schema_id: str | None = None

    @property
    def mode(self) -> str:
        return self.executor_type

    @property
    def operator_approved(self) -> bool:
        return self.approval_evidence is not None and self.approval_evidence.verified


@dataclass(frozen=True)
class UniversalTaskResult:
    status: str
    public: dict[str, Any]


def normalize_task(raw: Mapping[str, Any]) -> RunnerTaskEnvelope:
    canonical, legacy_schema_id = _canonicalize_raw_task(raw)
    _validate_against_schema(canonical)
    schema_id = str(canonical.get("schema"))
    if schema_id != SCHEMA_ID:
        raise ValueError("runner task schema must be skeleton.runner_task.v1.")

    payload = dict(canonical.get("payload") or {})
    resources = tuple(canonical.get("resources") or ())
    executor_type = str(canonical.get("executor_type") or "").strip()
    action = str(canonical.get("action") or "").strip().upper()

    timeout_raw = canonical.get("timeout_seconds", 60)
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be numeric.") from exc
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600.")

    risk = str(canonical.get("risk") or "YELLOW").upper()
    approval_evidence = _approval_evidence(canonical.get("approval_evidence"))
    return RunnerTaskEnvelope(
        task_id=_required_token(canonical, "task_id"),
        task_key=_required_token(canonical, "task_key"),
        action=action,
        executor_type=executor_type,
        risk=risk,
        payload=payload,
        resources=resources,
        approval_evidence=approval_evidence,
        timeout_seconds=timeout_seconds,
        allowed_scope=tuple(str(item) for item in canonical.get("allowed_scope") or ()),
        forbidden_actions=tuple(str(item) for item in canonical.get("forbidden_actions") or ()),
        validation=tuple(str(item) for item in canonical.get("validation") or ()),
        privacy_boundary=(
            str(canonical["privacy_boundary"]) if "privacy_boundary" in canonical else None
        ),
        schema_id=schema_id,
        legacy_schema_id=legacy_schema_id,
    )


def _canonicalize_raw_task(raw: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    canonical = dict(raw)
    schema_id = str(canonical.get("schema") or canonical.get("schema_id") or SCHEMA_ID)
    legacy_schema_id = None
    if schema_id in LEGACY_SCHEMA_IDS:
        legacy_schema_id = schema_id
        schema_id = SCHEMA_ID
    if schema_id != SCHEMA_ID:
        raise ValueError("runner task schema must be skeleton.runner_task.v1.")
    canonical["schema"] = schema_id
    canonical.pop("schema_id", None)

    if "payload" not in canonical and "params" in canonical:
        canonical["payload"] = canonical.pop("params")
    if "resources" not in canonical and "files" in canonical:
        canonical["resources"] = canonical.pop("files")
    else:
        canonical.pop("files", None)
    canonical.pop("params", None)

    mode = str(canonical.pop("mode", canonical.pop("task_type", "")) or "").strip()
    if mode:
        mode_map = {
            "codex_issue_worktree": "codex_branch_task",
            "local_command": "local_module_task",
            "hermes_task": "hermes_private_task",
            "RUNNER_TASK": "codex_branch_task",
            "RUNTIME_MAINTENANCE_TASK": "runtime_maintenance_task",
        }
        canonical.setdefault("executor_type", mode_map.get(mode, mode))
        canonical.setdefault("action", "START")

    legacy_operator_approved = canonical.pop("operator_approved", None)
    legacy_operator_approval = canonical.pop("operator_approval", None)
    if legacy_operator_approved is True or legacy_operator_approval is True:
        raise ValueError("operator approval must be verified approval_evidence.")
    return canonical, legacy_schema_id


def _validate_against_schema(canonical: Mapping[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(dict(canonical)), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"universal runner task schema violation at {location}: {first.message}")


def _approval_evidence(raw: Any) -> ApprovalEvidence | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("approval_evidence must be an object.")
    source = str(raw.get("source") or "")
    evidence_id = str(raw.get("evidence_id") or "")
    verified = raw.get("verified") is True
    if source not in {"operator_event", "signed_telegram_callback"} or not verified:
        return None
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,160}", evidence_id) is None:
        return None
    return ApprovalEvidence(source=source, evidence_id=evidence_id, verified=True)


def gate_task(task: RunnerTaskEnvelope) -> UniversalTaskResult:
    reasons: list[str] = []
    if task.risk not in RISK_LEVELS:
        reasons.append("risk_level_invalid")
    if task.action not in TASK_ACTIONS:
        reasons.append("action_invalid")
    if task.executor_type not in EXECUTOR_TYPES:
        reasons.append("executor_type_invalid")
    if task.risk == "RED" and not task.operator_approved:
        reasons.append("red_risk_requires_operator_approval")
    protected = detect_protected_resources(task)
    if protected and not task.operator_approved:
        reasons.append("protected_resource_requires_operator_approval")
    if reasons:
        return UniversalTaskResult(
            status="NEEDS_OPERATOR",
            public={
                "reasons": reasons,
                "risk": task.risk,
                "protected_resources": sorted(protected),
            },
        )
    return UniversalTaskResult(status="RUNNING", public={"risk": task.risk})


def detect_protected_resources(task: RunnerTaskEnvelope) -> set[str]:
    protected: set[str] = set()
    candidates = [*task.resources]
    candidates.extend(_structured_resource_candidates(task.payload))
    candidates.extend(task.allowed_scope)
    candidates.extend(task.forbidden_actions)
    candidates.extend(task.validation)
    if task.privacy_boundary:
        candidates.append(task.privacy_boundary)
    for candidate in candidates:
        reason = protected_resource_reason(str(candidate))
        if reason is not None:
            protected.add(reason)
    return protected


def _structured_resource_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in {
                "file",
                "files",
                "path",
                "paths",
                "resource",
                "resources",
                "target",
                "targets",
                "scope",
                "allowed_scope",
                "forbidden_actions",
            }:
                candidates.extend(_structured_resource_candidates(nested))
            elif isinstance(nested, (Mapping, list, tuple)):
                candidates.extend(_structured_resource_candidates(nested))
            elif isinstance(nested, str):
                candidates.append(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            candidates.extend(_structured_resource_candidates(item))
    return candidates


def protected_resource_reason(resource: str) -> str | None:
    text = str(resource).strip()
    lowered = text.lower().replace("\\", "/")
    if not text:
        return None
    if text.startswith(("/", "~")) or re.match(r"^[a-zA-Z]:\\", text):
        return "absolute_or_user_path"
    if any(part in {"..", "secrets", "private"} for part in lowered.split("/")):
        return "private_or_traversal_path"
    protected_exact = {
        ".env",
        "boot_manifest.yaml",
        "project_tree.yaml",
        "operator_rules.yaml",
        "capability_registry.yaml",
        "scripts/runner_poll_github_tasks.py",
        "core/action_gate.py",
        "core/gate_engine.py",
    }
    if lowered in protected_exact:
        return "protected_control_file"
    if lowered.endswith(".env") or lowered.startswith(".github/workflows/"):
        return "protected_control_file"
    if lowered.startswith(("secrets/", "deploy/", "server/", "finance/", "legal/", "governance/")):
        return "protected_domain_boundary"
    if "runner core" in lowered or "runner_core" in lowered:
        return "protected_runner_core"
    if any(
        token in lowered
        for token in (
            "control manifest",
            "workflow",
            "secret",
            "deploy",
            "server",
            "finance",
            "legal",
            "governance",
            "adapter boundary",
            "adapter_boundar",
        )
    ):
        return "protected_domain_boundary"
    if "drive.google.com" in lowered or "docs.google.com" in lowered:
        return "private_drive_reference"
    return None


def run_with_timeout(
    handler: Callable[[], UniversalTaskResult],
    timeout_seconds: float,
    *,
    on_heartbeat: Callable[[], None] | None = None,
    heartbeat_interval_seconds: float = 5.0,
) -> UniversalTaskResult:
    context = multiprocessing.get_context("fork")
    queue: multiprocessing.Queue[Any] = context.Queue(maxsize=1)
    process = context.Process(target=_run_handler_in_child, args=(handler, queue))
    process.start()
    deadline = time.monotonic() + timeout_seconds
    last_heartbeat = 0.0
    while process.is_alive() and time.monotonic() < deadline:
        now = time.monotonic()
        if on_heartbeat is not None and now - last_heartbeat >= heartbeat_interval_seconds:
            on_heartbeat()
            last_heartbeat = now
        process.join(timeout=min(0.05, max(0.0, deadline - now)))
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)
    try:
        kind, payload = queue.get_nowait()
    except Exception:
        return UniversalTaskResult(
            status="CANCELLED",
            public={"reason": "timeout", "timeout_seconds": timeout_seconds},
        )
    if kind == "result" and isinstance(payload, UniversalTaskResult):
        return payload
    if kind == "result" and isinstance(payload, Mapping):
        return UniversalTaskResult(
            status=str(payload.get("status") or "FAILED").upper(),
            public=dict(payload.get("public") or {}),
        )
    return UniversalTaskResult(status="FAILED", public={"reason": str(payload)})


def _run_handler_in_child(handler: Callable[[], UniversalTaskResult], queue: Any) -> None:
    try:
        result = handler()
        queue.put(("result", result))
    except Exception:
        queue.put(("error", "worker_raised"))


class AtomicTaskStateStore:
    def __init__(self, path: str | Path, *, stale_lease_seconds: int = STALE_LEASE_SECONDS) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.stale_lease_seconds = stale_lease_seconds

    def load(self) -> dict[str, Any]:
        with self._locked():
            return self._read_unlocked()

    def get(self, task_key: str) -> dict[str, Any] | None:
        return self.load().get("tasks", {}).get(task_key)

    def acquire(self, task: RunnerTaskEnvelope, owner: str) -> UniversalTaskResult:
        now = _utc_now()
        with self._locked():
            state = self._read_unlocked()
            tasks = state.setdefault("tasks", {})
            record = tasks.get(task.task_key)
            if record and record.get("status") == "RUNNING":
                lease = record.get("lease") or {}
                if not _lease_is_stale(lease, self.stale_lease_seconds):
                    return UniversalTaskResult(
                        status="RUNNING",
                        public={"reason": "lease_active", "task_key": task.task_key},
                    )
                record["status"] = "FAILED"
                record["stale_recovered_at"] = now
            if task.action == "CONTINUE" and (
                record is None or record.get("status") != "CHECKPOINTED"
            ):
                return UniversalTaskResult(
                    status="FAILED",
                    public={"reason": "checkpoint_missing", "task_key": task.task_key},
                )
            tasks[task.task_key] = {
                "schema": SCHEMA_ID,
                "task_id": task.task_id,
                "task_key": task.task_key,
                "action": task.action,
                "executor_type": task.executor_type,
                "risk": task.risk,
                "status": "RUNNING",
                "updated_at": now,
                "lease": {"owner": owner, "acquired_at": now, "heartbeat_at": now},
                "context": _adapter_context(task),
            }
            self._write_unlocked(state)
        return UniversalTaskResult(status="RUNNING", public={"task_key": task.task_key})

    def update(self, task_key: str, status: str, public: Mapping[str, Any] | None = None) -> UniversalTaskResult:
        status = status.upper()
        if status not in TASK_STATUSES:
            raise ValueError("unknown task status.")
        validate_public_safe_payload({"public": dict(public or {})})
        with self._locked():
            state = self._read_unlocked()
            record = state.setdefault("tasks", {}).get(task_key)
            if record is None:
                return UniversalTaskResult(
                    status="FAILED",
                    public={"reason": "missing_record", "task_key": task_key},
                )
            record["status"] = status
            record["public"] = dict(public or {})
            record["updated_at"] = _utc_now()
            if status in TERMINAL_STATUSES:
                record.pop("lease", None)
            self._write_unlocked(state)
        return UniversalTaskResult(status=status, public=dict(public or {}))

    def persist(
        self,
        task: RunnerTaskEnvelope,
        status: str,
        public: Mapping[str, Any] | None = None,
    ) -> UniversalTaskResult:
        status = status.upper()
        if status not in TASK_STATUSES:
            raise ValueError("unknown task status.")
        validate_public_safe_payload({"public": dict(public or {})})
        now = _utc_now()
        with self._locked():
            state = self._read_unlocked()
            record = state.setdefault("tasks", {}).setdefault(task.task_key, {})
            record.update(
                {
                    "schema": SCHEMA_ID,
                    "task_id": task.task_id,
                    "task_key": task.task_key,
                    "action": task.action,
                    "executor_type": task.executor_type,
                    "risk": task.risk,
                    "status": status,
                    "public": dict(public or {}),
                    "updated_at": now,
                    "context": _adapter_context(task),
                }
            )
            if status in TERMINAL_STATUSES or status in {"NEEDS_OPERATOR", "CHECKPOINTED"}:
                record.pop("lease", None)
            self._write_unlocked(state)
        return UniversalTaskResult(status=status, public=dict(public or {}))

    def heartbeat(self, task_key: str, owner: str) -> bool:
        with self._locked():
            state = self._read_unlocked()
            record = state.setdefault("tasks", {}).get(task_key)
            if record is None or record.get("status") != "RUNNING":
                return False
            lease = record.setdefault("lease", {})
            if lease.get("owner") != owner:
                return False
            lease["heartbeat_at"] = _utc_now()
            record["updated_at"] = lease["heartbeat_at"]
            self._write_unlocked(state)
        return True

    def status(self, task_key: str) -> UniversalTaskResult:
        record = self.get(task_key)
        if record is None:
            return UniversalTaskResult(
                status="FAILED",
                public={"reason": "missing_record", "task_key": task_key},
            )
        return UniversalTaskResult(
            status=str(record.get("status") or "FAILED").upper(),
            public=dict(record.get("public") or {}),
        )

    def cancel(self, task_key: str) -> UniversalTaskResult:
        if self.get(task_key) is None:
            return UniversalTaskResult(
                status="FAILED",
                public={"reason": "missing_record", "task_key": task_key},
            )
        return self.update(task_key, "CANCELLED", {"reason": "cancelled"})

    def _locked(self):
        store = self

        class _Lock:
            def __enter__(self) -> None:
                store.path.parent.mkdir(parents=True, exist_ok=True)
                self.handle = store.lock_path.open("a+", encoding="utf-8")
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)

            def __exit__(self, exc_type, exc, tb) -> None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()

        return _Lock()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": SCHEMA_ID, "tasks": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"schema": SCHEMA_ID, "tasks": {}}
        if state.get("schema") in LEGACY_SCHEMA_IDS:
            state["schema"] = SCHEMA_ID
        state.setdefault("tasks", {})
        return state

    def _write_unlocked(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(state, tmp, ensure_ascii=True, allow_nan=False, sort_keys=True)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def execute_universal_task(
    task: RunnerTaskEnvelope,
    registry: RunnerExecutorRegistry,
    store: AtomicTaskStateStore,
    *,
    owner: str = "runner",
) -> UniversalTaskResult:
    if task.action == "STATUS":
        return store.status(task.task_key)
    if task.action == "CANCEL":
        return store.cancel(task.task_key)

    gate = gate_task(task)
    if gate.status != "RUNNING":
        return store.persist(task, gate.status, gate.public)

    acquired = store.acquire(task, owner)
    if acquired.status != "RUNNING" or acquired.public.get("reason") == "lease_active":
        return acquired

    def _run() -> UniversalTaskResult:
        payload = _adapter_payload(task)
        command_id = str(task.payload.get("command_id") or task.payload.get("task_id") or "")
        if task.executor_type == "local_module_task":
            result = registry.run_local_module_task(
                command_id, payload
            )
            return _from_registered_result(result)
        if task.executor_type == "hermes_private_task":
            result = registry.run_hermes_private_task(
                command_id, payload
            )
            return _from_registered_result(result)
        if task.executor_type == "runtime_maintenance_task":
            result = registry.run_runtime_maintenance_task(command_id, payload)
            return _from_registered_result(result)
        if task.executor_type == "read_only_probe":
            result = registry.run_read_only_probe(command_id, payload)
            return _from_registered_result(result)
        if task.executor_type == "codex_branch_task":
            result = registry.run_codex_branch_task(command_id, payload)
            return _from_registered_result(result)
        return UniversalTaskResult(
            status="FAILED",
            public={"reason": "unsupported_universal_executor_type", "executor_type": task.executor_type},
        )

    result = run_with_timeout(
        _run,
        task.timeout_seconds,
        on_heartbeat=lambda: store.heartbeat(task.task_key, owner),
    )
    return store.persist(task, result.status, result.public)


def github_status_for_universal_state(status: str) -> dict[str, str]:
    normalized = str(status).upper()
    mapping = {
        "CHECKPOINTED": ("pending", "Task checkpointed and waiting for runner resume."),
        "NEEDS_OPERATOR": ("action_required", "Task needs operator approval."),
        "RUNNING": ("pending", "Task is running."),
        "CANCELLED": ("cancelled", "Task was cancelled."),
        "FAILED": ("failure", "Task failed closed."),
        "COMPLETED": ("success", "Task completed."),
    }
    state, description = mapping.get(normalized, ("failure", "Unknown task status."))
    next_action = {
        "CHECKPOINTED": "CONTINUE",
        "NEEDS_OPERATOR": "START_WITH_APPROVAL",
        "RUNNING": "STATUS",
        "CANCELLED": "START",
        "FAILED": "START",
        "COMPLETED": "NONE",
    }.get(normalized, "START")
    return {"state": state, "description": description, "next_action": next_action}


def _adapter_context(task: RunnerTaskEnvelope) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_key": task.task_key,
        "action": task.action,
        "executor_type": task.executor_type,
        "risk": task.risk,
        "allowed_scope": list(task.allowed_scope),
        "forbidden_actions": list(task.forbidden_actions),
        "validation": list(task.validation),
        "privacy_boundary": task.privacy_boundary,
        "timeout_seconds": task.timeout_seconds,
        "approval_evidence": (
            {
                "source": task.approval_evidence.source,
                "evidence_id": task.approval_evidence.evidence_id,
                "verified": True,
            }
            if task.approval_evidence is not None
            else None
        ),
    }


def _adapter_payload(task: RunnerTaskEnvelope) -> dict[str, Any]:
    payload = dict(task.payload)
    payload["runner_context"] = _adapter_context(task)
    return payload


def sanitize_public_text(text: str) -> str:
    sanitized = str(text)
    sanitized = re.sub(r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|KEY|CREDENTIAL|AUTH)[A-Z0-9_]*)\s*[:=]\s*\S+", r"\1=<redacted>", sanitized)
    sanitized = re.sub(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_=-]{12,}\b", "<redacted-token>", sanitized)
    sanitized = re.sub(r"https?://(?:drive|docs)\.google\.com/\S+", "<redacted-drive-url>", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"https?://(?!github\.com/alanua/Skeleton/(?:pull|issues)/[1-9]\d*/?\b)\S+", "<redacted-url>", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<redacted-ip>", sanitized)
    sanitized = re.sub(r"(?<!\w)/(?:home|root|mnt|media|var/lib|etc|run/secrets)/[^\s`'\"<>]+", "<redacted-path>", sanitized)
    sanitized = re.sub(r"\b[A-Za-z]:\\Users\\[^\s`'\"<>]+", "<redacted-path>", sanitized)
    sanitized = re.sub(r"(?i)\b(private_value|private_payload|credential)\b\s*[:=]\s*\S+", r"\1=<redacted>", sanitized)
    return sanitized


def migrate_legacy_task(raw: Mapping[str, Any]) -> RunnerTaskEnvelope:
    return normalize_task(raw)


def _from_registered_result(result: RegisteredCommandResult) -> UniversalTaskResult:
    return UniversalTaskResult(status=result.status, public=result.public)


def _required_token(raw: Mapping[str, Any], key: str) -> str:
    value = str(raw.get(key) or "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}", value) is None:
        raise ValueError(f"{key} must be a stable task token.")
    return value


def _lease_is_stale(lease: Mapping[str, Any], stale_seconds: int) -> bool:
    heartbeat = str(lease.get("heartbeat_at") or "")
    try:
        timestamp = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
    except ValueError:
        return True
    return time.time() - timestamp.timestamp() > stale_seconds


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

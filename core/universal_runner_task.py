from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

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
STALE_LEASE_SECONDS = 15 * 60


@dataclass(frozen=True)
class RunnerTaskEnvelope:
    task_id: str
    task_key: str
    mode: str
    risk: str
    payload: dict[str, Any]
    resources: tuple[str, ...] = ()
    operator_approved: bool = False
    timeout_seconds: float = 60.0
    schema_id: str = SCHEMA_ID
    legacy_schema_id: str | None = None


@dataclass(frozen=True)
class UniversalTaskResult:
    status: str
    public: dict[str, Any]


def normalize_task(raw: Mapping[str, Any]) -> RunnerTaskEnvelope:
    schema_id = str(raw.get("schema") or raw.get("schema_id") or SCHEMA_ID)
    legacy_schema_id = None
    if schema_id in LEGACY_SCHEMA_IDS:
        legacy_schema_id = schema_id
        schema_id = SCHEMA_ID
    if schema_id != SCHEMA_ID:
        raise ValueError("runner task schema must be skeleton.runner_task.v1.")

    payload = dict(raw.get("payload") or raw.get("params") or {})
    resources = tuple(str(item) for item in raw.get("resources") or raw.get("files") or ())
    mode = str(raw.get("mode") or raw.get("task_type") or "").strip()
    if mode == "codex_issue_worktree":
        mode = "codex_branch_task"
    if mode == "local_command":
        mode = "local_module_task"

    timeout_raw = raw.get("timeout_seconds", 60)
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be numeric.") from exc
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600.")

    risk = str(raw.get("risk") or "YELLOW").upper()
    return RunnerTaskEnvelope(
        task_id=_required_token(raw, "task_id"),
        task_key=_required_token(raw, "task_key"),
        mode=mode,
        risk=risk,
        payload=payload,
        resources=resources,
        operator_approved=raw.get("operator_approved") is True
        or raw.get("operator_approval") is True,
        timeout_seconds=timeout_seconds,
        schema_id=schema_id,
        legacy_schema_id=legacy_schema_id,
    )


def gate_task(task: RunnerTaskEnvelope) -> UniversalTaskResult:
    reasons: list[str] = []
    if task.risk not in RISK_LEVELS:
        reasons.append("risk_level_invalid")
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
    candidates = list(task.resources)
    for key in ("file", "files", "path", "paths", "resource", "resources"):
        value = task.payload.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(item) for item in value)
    for candidate in candidates:
        reason = protected_resource_reason(candidate)
        if reason is not None:
            protected.add(reason)
    return protected


def protected_resource_reason(resource: str) -> str | None:
    text = str(resource).strip()
    lowered = text.lower().replace("\\", "/")
    if not text:
        return None
    if text.startswith(("/", "~")) or re.match(r"^[a-zA-Z]:\\", text):
        return "absolute_or_user_path"
    if any(part in {"..", "secrets", "private"} for part in lowered.split("/")):
        return "private_or_traversal_path"
    if lowered in {".env", "boot_manifest.yaml", "project_tree.yaml", "operator_rules.yaml", "capability_registry.yaml"}:
        return "protected_control_file"
    if lowered.endswith(".env") or lowered.startswith(".github/workflows/"):
        return "protected_control_file"
    if "drive.google.com" in lowered or "docs.google.com" in lowered:
        return "private_drive_reference"
    return None


def run_with_timeout(
    handler: Callable[[], UniversalTaskResult], timeout_seconds: float
) -> UniversalTaskResult:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(handler)
    try:
        return future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        return UniversalTaskResult(
            status="CANCELLED",
            public={"reason": "timeout", "timeout_seconds": timeout_seconds},
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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
            tasks[task.task_key] = {
                "schema": SCHEMA_ID,
                "task_id": task.task_id,
                "task_key": task.task_key,
                "mode": task.mode,
                "risk": task.risk,
                "status": "RUNNING",
                "updated_at": now,
                "lease": {"owner": owner, "acquired_at": now, "heartbeat_at": now},
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
    if task.mode == "STATUS":
        return store.status(task.task_key)
    if task.mode == "CANCEL":
        return store.cancel(task.task_key)

    gate = gate_task(task)
    if gate.status != "RUNNING":
        return store.update(task.task_key, gate.status, gate.public) if store.get(task.task_key) else gate

    acquired = store.acquire(task, owner)
    if acquired.status != "RUNNING" or acquired.public.get("reason") == "lease_active":
        return acquired

    def _run() -> UniversalTaskResult:
        if task.mode == "local_module_task":
            result = registry.run_local_module_task(
                str(task.payload.get("command_id") or ""), task.payload
            )
            return _from_registered_result(result)
        if task.mode == "hermes_task":
            result = registry.run_hermes_task(
                str(task.payload.get("command_id") or ""), task.payload
            )
            return _from_registered_result(result)
        return UniversalTaskResult(
            status="FAILED",
            public={"reason": "unsupported_universal_task_mode", "mode": task.mode},
        )

    result = run_with_timeout(_run, task.timeout_seconds)
    return store.update(task.task_key, result.status, result.public)


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
    return {"state": state, "description": description}


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

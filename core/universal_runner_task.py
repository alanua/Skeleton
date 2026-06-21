from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any


SCHEMA_ID = "skeleton.universal_runner_task.v1"
ACTIONS = frozenset({"START", "STATUS", "CONTINUE", "CANCEL"})
STATE_MACHINE = frozenset(
    {
        "RECEIVED",
        "PREFLIGHT",
        "RUNNING",
        "CHECKPOINTED",
        "NEEDS_OPERATOR",
        "BLOCKED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
)
TERMINAL_STATES = frozenset({"BLOCKED", "COMPLETED", "FAILED", "CANCELLED"})
HIGH_RISK_CLASSES = frozenset({"high", "protected"})
APPROVAL_REQUIRED_VALUES = frozenset({"none", "explicit", "protected", "high_risk"})
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,160}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{2,200}$")
SAFE_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,240}$")
PRIVATE_LEAK_RE = re.compile(
    r"(?i)(/home/|/tmp/|secret|token|password|credential|private prompt|BEGIN PRIVATE)"
)


class UniversalTaskError(ValueError):
    """Closed-failure validation error for universal runner tasks."""


@dataclass(frozen=True)
class UniversalRunnerTask:
    schema: str
    task_id: str
    idempotency_key: str
    action: str
    executor_type: str
    capability: str
    risk_class: str
    target: dict[str, Any]
    repo: str
    branch: str
    task: str
    allowed_files_or_resources: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation: dict[str, Any]
    expected_output: str
    privacy_boundary: str
    timeout_seconds: int
    approval_requirement: str
    private_payload_ref: str | None = None
    approval_evidence: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "UniversalRunnerTask":
        reasons = validate_universal_task_payload(payload)
        if reasons:
            raise UniversalTaskError("; ".join(reasons))
        private_payload_ref = payload.get("private_payload_ref")
        approval_evidence = payload.get("approval_evidence")
        return cls(
            schema=payload["schema"],
            task_id=payload["task_id"],
            idempotency_key=payload["idempotency_key"],
            action=payload["action"],
            executor_type=payload["executor_type"],
            capability=payload["capability"],
            risk_class=payload["risk_class"],
            target=dict(payload["target"]),
            repo=payload["repo"],
            branch=payload["branch"],
            task=payload["task"],
            allowed_files_or_resources=tuple(payload["allowed_files_or_resources"]),
            forbidden_actions=tuple(payload["forbidden_actions"]),
            validation=dict(payload["validation"]),
            expected_output=payload["expected_output"],
            privacy_boundary=payload["privacy_boundary"],
            timeout_seconds=payload["timeout_seconds"],
            approval_requirement=payload["approval_requirement"],
            private_payload_ref=private_payload_ref
            if isinstance(private_payload_ref, str)
            else None,
            approval_evidence=dict(approval_evidence)
            if isinstance(approval_evidence, dict)
            else None,
        )

    @classmethod
    def from_json(cls, text: str) -> "UniversalRunnerTask":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UniversalTaskError("universal task envelope must be JSON") from exc
        if not isinstance(payload, dict):
            raise UniversalTaskError("universal task envelope must be a JSON object")
        return cls.from_mapping(payload)


def validate_universal_task_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["envelope must be a JSON object"]

    required = (
        "schema",
        "task_id",
        "idempotency_key",
        "action",
        "executor_type",
        "capability",
        "risk_class",
        "target",
        "repo",
        "branch",
        "task",
        "allowed_files_or_resources",
        "forbidden_actions",
        "validation",
        "expected_output",
        "privacy_boundary",
        "timeout_seconds",
        "approval_requirement",
        "private_payload_ref",
    )
    reasons: list[str] = []
    for field in required:
        if field not in payload:
            reasons.append(f"{field} is required")
    if reasons:
        return reasons

    if payload.get("schema") != SCHEMA_ID:
        reasons.append(f"schema must be {SCHEMA_ID}")
    for field in ("task_id", "idempotency_key", "executor_type", "capability", "repo", "branch"):
        value = payload.get(field)
        if not isinstance(value, str) or SAFE_TOKEN_RE.fullmatch(value) is None:
            reasons.append(f"{field} must be a safe token")
    if payload.get("action") not in ACTIONS:
        reasons.append("action is not registered")
    if not isinstance(payload.get("risk_class"), str) or not payload["risk_class"]:
        reasons.append("risk_class must be a non-empty string")
    if not isinstance(payload.get("target"), dict):
        reasons.append("target must be an object")
    if not isinstance(payload.get("task"), str):
        reasons.append("task must be a string")
    elif PRIVATE_LEAK_RE.search(payload["task"]) and payload.get("private_payload_ref"):
        reasons.append("task must not embed private content when private_payload_ref is used")
    _validate_string_list(
        payload.get("allowed_files_or_resources"),
        "allowed_files_or_resources",
        reasons,
        path_like=True,
    )
    _validate_string_list(payload.get("forbidden_actions"), "forbidden_actions", reasons)
    if not isinstance(payload.get("validation"), dict):
        reasons.append("validation must be an object")
    if not isinstance(payload.get("expected_output"), str):
        reasons.append("expected_output must be a string")
    if not isinstance(payload.get("privacy_boundary"), str) or not payload["privacy_boundary"]:
        reasons.append("privacy_boundary must be a non-empty string")
    timeout = payload.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        reasons.append("timeout_seconds must be a positive integer")
    if payload.get("approval_requirement") not in APPROVAL_REQUIRED_VALUES:
        reasons.append("approval_requirement is not registered")
    private_ref = payload.get("private_payload_ref")
    if private_ref is not None and (
        not isinstance(private_ref, str) or SAFE_REF_RE.fullmatch(private_ref) is None
    ):
        reasons.append("private_payload_ref must be null or an opaque safe reference")
    reasons.extend(_approval_reasons(payload))
    return reasons


def _validate_string_list(
    value: object, field: str, reasons: list[str], *, path_like: bool = False
) -> None:
    if not isinstance(value, list):
        reasons.append(f"{field} must be a list")
        return
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item:
            reasons.append(f"{field} must contain strings")
            return
        if item in seen:
            reasons.append(f"{field} must not contain duplicates")
            return
        seen.add(item)
        if path_like:
            parts = item.split("/")
            if (
                item.startswith("/")
                or "\\" in item
                or any(part in {"", ".", ".."} for part in parts)
                or SAFE_RELATIVE_PATH_RE.fullmatch(item) is None
            ):
                reasons.append(f"{field} must contain safe relative resources")
                return


def _approval_reasons(payload: dict[str, Any]) -> list[str]:
    requirement = payload.get("approval_requirement")
    risk_class = str(payload.get("risk_class") or "").lower()
    protected = requirement in {"explicit", "protected", "high_risk"} or risk_class in HIGH_RISK_CLASSES
    if not protected:
        return []
    evidence = payload.get("approval_evidence")
    if not isinstance(evidence, dict):
        return ["protected or high-risk task requires explicit approval_evidence"]
    if evidence.get("approved") is not True:
        return ["approval_evidence.approved must be true"]
    source = evidence.get("source")
    if not isinstance(source, str) or source not in {"operator", "signed_telegram_callback"}:
        return ["approval_evidence.source is not registered"]
    return []


def sanitize_public_text(text: str) -> str:
    sanitized = PRIVATE_LEAK_RE.sub("[redacted]", text)
    return sanitized[:60000]


@dataclass(frozen=True)
class TaskExecutionRecord:
    task_id: str
    idempotency_key: str
    executor_type: str
    action: str
    state: str
    public_status: str
    public_report: str
    checkpoint: dict[str, Any] | None
    started_at: float
    updated_at: float

    def to_mapping(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "idempotency_key": self.idempotency_key,
            "executor_type": self.executor_type,
            "action": self.action,
            "state": self.state,
            "public_status": self.public_status,
            "public_report": self.public_report,
            "checkpoint": self.checkpoint,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "TaskExecutionRecord":
        return cls(
            task_id=str(payload["task_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            executor_type=str(payload["executor_type"]),
            action=str(payload["action"]),
            state=str(payload["state"]),
            public_status=str(payload["public_status"]),
            public_report=str(payload["public_report"]),
            checkpoint=payload.get("checkpoint")
            if isinstance(payload.get("checkpoint"), dict)
            else None,
            started_at=float(payload["started_at"]),
            updated_at=float(payload["updated_at"]),
        )


class UniversalTaskStateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get("SKELETON_UNIVERSAL_RUNNER_STATE")
        self.path = Path(configured or "/tmp/skeleton_universal_runner_tasks.json")
        self.lock_root = self.path.with_suffix(self.path.suffix + ".locks")

    def get(self, idempotency_key: str) -> TaskExecutionRecord | None:
        records = self._read_all()
        payload = records.get(idempotency_key)
        if not isinstance(payload, dict):
            return None
        return TaskExecutionRecord.from_mapping(payload)

    def save(self, record: TaskExecutionRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = self._read_all()
        records[record.idempotency_key] = record.to_mapping()
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def acquire_lease(self, idempotency_key: str) -> "TaskLease | None":
        self.lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.lock_root / _lock_name(idempotency_key)
        try:
            lock_path.mkdir()
        except FileExistsError:
            return None
        return TaskLease(lock_path)

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


class TaskLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        (self.path / "created_at").write_text(
            datetime.now(timezone.utc).isoformat(), encoding="utf-8"
        )

    def release(self) -> None:
        try:
            for child in self.path.iterdir():
                child.unlink()
            self.path.rmdir()
        except FileNotFoundError:
            return


def _lock_name(idempotency_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", idempotency_key)[:180]


def make_record(
    task: UniversalRunnerTask,
    *,
    state: str,
    public_status: str,
    public_report: str,
    checkpoint: dict[str, Any] | None = None,
    started_at: float | None = None,
) -> TaskExecutionRecord:
    now = time.time()
    if state not in STATE_MACHINE:
        raise UniversalTaskError(f"state {state!r} is not registered")
    return TaskExecutionRecord(
        task_id=task.task_id,
        idempotency_key=task.idempotency_key,
        executor_type=task.executor_type,
        action=task.action,
        state=state,
        public_status=public_status,
        public_report=sanitize_public_text(public_report),
        checkpoint=checkpoint,
        started_at=started_at or now,
        updated_at=now,
    )

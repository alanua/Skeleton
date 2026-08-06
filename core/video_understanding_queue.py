from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


class VideoQueueError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


QUEUE_STATES = ("pending", "processing", "done", "review", "retry", "quarantined")
_ID_RE = re.compile(r"^vut_[0-9a-f]{32}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


@dataclass(frozen=True)
class VideoTask:
    schema: str
    task_id: str
    idempotency_key: str
    operation: str
    payload: Mapping[str, Any]
    state: str
    attempts: int
    available_at: float
    lease_until: float | None
    worker_id: str | None
    reason_code: str | None
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        if self.schema != "skeleton.video_understanding.task.v1":
            raise VideoQueueError("TASK_SCHEMA_INVALID", "task schema is invalid")
        if _ID_RE.fullmatch(self.task_id) is None:
            raise VideoQueueError("TASK_ID_INVALID", "task id is invalid")
        if self.state not in QUEUE_STATES:
            raise VideoQueueError("TASK_STATE_INVALID", "task state is invalid")
        if _TOKEN_RE.fullmatch(self.operation) is None:
            raise VideoQueueError("TASK_OPERATION_INVALID", "task operation is invalid")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or self.attempts < 0:
            raise VideoQueueError("TASK_ATTEMPTS_INVALID", "task attempts are invalid")
        if not isinstance(self.payload, Mapping):
            raise VideoQueueError("TASK_PAYLOAD_INVALID", "task payload must be an object")
        json.dumps(self.payload, allow_nan=False, sort_keys=True)
        for value in (self.available_at, self.created_at, self.updated_at):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise VideoQueueError("TASK_TIME_INVALID", "task time is invalid")
        if self.lease_until is not None and (
            isinstance(self.lease_until, bool) or not isinstance(self.lease_until, (int, float))
        ):
            raise VideoQueueError("TASK_TIME_INVALID", "task lease is invalid")
        if self.worker_id is not None and _TOKEN_RE.fullmatch(self.worker_id) is None:
            raise VideoQueueError("TASK_WORKER_INVALID", "worker id is invalid")
        if self.reason_code is not None and _REASON_RE.fullmatch(self.reason_code) is None:
            raise VideoQueueError("TASK_REASON_INVALID", "task reason code is invalid")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VideoTask":
        try:
            return cls(
                schema=str(payload["schema"]),
                task_id=str(payload["task_id"]),
                idempotency_key=str(payload["idempotency_key"]),
                operation=str(payload["operation"]),
                payload=dict(payload["payload"]),
                state=str(payload["state"]),
                attempts=int(payload["attempts"]),
                available_at=float(payload["available_at"]),
                lease_until=float(payload["lease_until"]) if payload.get("lease_until") is not None else None,
                worker_id=str(payload["worker_id"]) if payload.get("worker_id") is not None else None,
                reason_code=str(payload["reason_code"]) if payload.get("reason_code") is not None else None,
                created_at=float(payload["created_at"]),
                updated_at=float(payload["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoQueueError("TASK_RECORD_INVALID", "task record is invalid") from exc


class VideoUnderstandingQueue:
    def __init__(
        self,
        root: Path,
        *,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.clock = clock
        if lease_seconds <= 0 or max_attempts <= 0:
            raise VideoQueueError("QUEUE_LIMIT_INVALID", "queue limits are invalid")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for state in QUEUE_STATES:
            (self.root / state).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = self.root / ".queue.lock"
        self.worker_lock_path = self.root / ".worker.lock"
        self.lock_path.touch(mode=0o600, exist_ok=True)
        self.worker_lock_path.touch(mode=0o600, exist_ok=True)

    def enqueue(self, *, operation: str, payload: Mapping[str, Any], idempotency_key: str) -> VideoTask:
        if not idempotency_key or not isinstance(idempotency_key, str):
            raise VideoQueueError("TASK_IDEMPOTENCY_INVALID", "idempotency key is invalid")
        json.dumps(payload, allow_nan=False, sort_keys=True)
        task_id = "vut_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        now = self.clock()
        with self._locked():
            existing = self._find(task_id)
            if existing is not None:
                return existing
            task = VideoTask(
                "skeleton.video_understanding.task.v1",
                task_id,
                idempotency_key,
                operation,
                dict(payload),
                "pending",
                0,
                now,
                None,
                None,
                None,
                now,
                now,
            )
            self._write(task)
            return task

    def claim(self, worker_id: str) -> VideoTask | None:
        if _TOKEN_RE.fullmatch(worker_id) is None:
            raise VideoQueueError("TASK_WORKER_INVALID", "worker id is invalid")
        now = self.clock()
        with self._locked():
            self._recover_processing_locked(now)
            for state in ("pending", "retry"):
                for path in sorted((self.root / state).glob("vut_*.json")):
                    task = self._read(path)
                    if task.available_at > now:
                        continue
                    claimed = replace(
                        task,
                        state="processing",
                        attempts=task.attempts + 1,
                        lease_until=now + self.lease_seconds,
                        worker_id=worker_id,
                        reason_code=None,
                        updated_at=now,
                    )
                    self._move(path, claimed)
                    return claimed
        return None

    def complete(self, task_id: str, worker_id: str, *, ambiguous: bool = False) -> VideoTask:
        return self._finish(task_id, worker_id, "review" if ambiguous else "done", "AMBIGUOUS_REVIEW" if ambiguous else None)

    def fail(self, task_id: str, worker_id: str, *, reason_code: str, permanent: bool = False) -> VideoTask:
        if _REASON_RE.fullmatch(reason_code) is None:
            raise VideoQueueError("TASK_REASON_INVALID", "task reason code is invalid")
        now = self.clock()
        with self._locked():
            path = self._path("processing", task_id)
            task = self._read(path)
            self._require_owner(task, worker_id)
            state = "quarantined" if permanent or task.attempts >= self.max_attempts else "retry"
            failed = replace(
                task,
                state=state,
                available_at=now + (0 if state == "quarantined" else min(3600.0, 30.0 * task.attempts)),
                lease_until=None,
                worker_id=None,
                reason_code=reason_code,
                updated_at=now,
            )
            self._move(path, failed)
            return failed

    def recover_after_restart(self) -> int:
        with self._locked():
            return self._recover_processing_locked(self.clock(), force=True)

    def counts(self) -> dict[str, int]:
        with self._locked():
            return {state: len(tuple((self.root / state).glob("vut_*.json"))) for state in QUEUE_STATES}

    @contextmanager
    def single_worker(self, worker_id: str) -> Iterator[None]:
        if _TOKEN_RE.fullmatch(worker_id) is None:
            raise VideoQueueError("TASK_WORKER_INVALID", "worker id is invalid")
        with self.worker_lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise VideoQueueError("WORKER_ALREADY_ACTIVE", "exactly one active worker is allowed") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _finish(self, task_id: str, worker_id: str, state: str, reason_code: str | None) -> VideoTask:
        now = self.clock()
        with self._locked():
            path = self._path("processing", task_id)
            task = self._read(path)
            self._require_owner(task, worker_id)
            finished = replace(task, state=state, lease_until=None, worker_id=None, reason_code=reason_code, updated_at=now)
            self._move(path, finished)
            return finished

    def _recover_processing_locked(self, now: float, *, force: bool = False) -> int:
        recovered = 0
        for path in sorted((self.root / "processing").glob("vut_*.json")):
            task = self._read(path)
            if not force and task.lease_until is not None and task.lease_until > now:
                continue
            recovered_task = replace(
                task,
                state="retry" if task.attempts < self.max_attempts else "quarantined",
                available_at=now,
                lease_until=None,
                worker_id=None,
                reason_code="WORKER_RESTART_RECOVERY",
                updated_at=now,
            )
            self._move(path, recovered_task)
            recovered += 1
        return recovered

    def _find(self, task_id: str) -> VideoTask | None:
        found = [self._read(path) for state in QUEUE_STATES if (path := self._path(state, task_id)).exists()]
        if len(found) > 1:
            raise VideoQueueError("TASK_DUPLICATE_STATE", "task exists in multiple states")
        return found[0] if found else None

    def _path(self, state: str, task_id: str) -> Path:
        if state not in QUEUE_STATES or _ID_RE.fullmatch(task_id) is None:
            raise VideoQueueError("TASK_PATH_INVALID", "task path is invalid")
        return self.root / state / f"{task_id}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.lock_path.open("r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write(self, task: VideoTask) -> None:
        path = self._path(task.state, task.task_id)
        temporary = path.with_name(path.name + ".part")
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(task.to_dict(), allow_nan=False, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _move(self, source: Path, task: VideoTask) -> None:
        self._write(task)
        source.unlink(missing_ok=True)

    @staticmethod
    def _read(path: Path) -> VideoTask:
        return VideoTask.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _require_owner(task: VideoTask, worker_id: str) -> None:
        if task.state != "processing" or task.worker_id != worker_id:
            raise VideoQueueError("TASK_LEASE_OWNER_MISMATCH", "worker does not own task")

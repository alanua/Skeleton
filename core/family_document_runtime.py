from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol, Sequence

from core.family_document_sources import ApprovedRoot, SourceError, inventory_sources, resolve_source, stable_observation


class RuntimeErrorCode(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RuntimeLimits:
    settle_seconds: float = 3.0
    lease_seconds: int = 300
    max_attempts: int = 4
    retry_base_seconds: int = 30
    max_inventory_files: int = 10000

    def __post_init__(self) -> None:
        if not 0 <= self.settle_seconds <= 3600:
            raise RuntimeErrorCode("settle_seconds_invalid")
        if not 30 <= self.lease_seconds <= 86400:
            raise RuntimeErrorCode("lease_seconds_invalid")
        if not 1 <= self.max_attempts <= 20:
            raise RuntimeErrorCode("max_attempts_invalid")
        if not 1 <= self.retry_base_seconds <= 86400:
            raise RuntimeErrorCode("retry_base_invalid")
        if not 1 <= self.max_inventory_files <= 100000:
            raise RuntimeErrorCode("inventory_limit_invalid")


class Processor(Protocol):
    def process(self, source: Path, *, dry_run: bool = False) -> Mapping[str, object]: ...


class AtomicJsonStore:
    def __init__(self, path: Path, *, schema: str) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.schema = schema
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path.touch(mode=0o600, exist_ok=True)

    @contextmanager
    def locked(self) -> Iterator[dict[str, object]]:
        with self.lock_path.open("r+b") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                payload = self._read()
                yield payload
                self._write(payload)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def snapshot(self) -> dict[str, object]:
        with self.locked() as payload:
            return json.loads(json.dumps(payload))

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {"schema": self.schema, "items": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeErrorCode("runtime_state_invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema") != self.schema or not isinstance(payload.get("items"), dict):
            raise RuntimeErrorCode("runtime_state_invalid")
        return payload

    def _write(self, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        fd, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
            _fsync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)


class DurableJournal:
    SCHEMA = "skeleton.family_document.runtime_journal.v1"

    def __init__(self, path: Path, limits: RuntimeLimits, *, clock: Callable[[], float] = time.time) -> None:
        self.store = AtomicJsonStore(path, schema=self.SCHEMA)
        self.limits = limits
        self.clock = clock

    def discover(self, roots: Sequence[ApprovedRoot]) -> int:
        discovered = 0
        sources = inventory_sources(roots, max_files=self.limits.max_inventory_files)
        now = self.clock()
        with self.store.locked() as payload:
            items = _items(payload)
            for source in sources:
                key = source_key(source.root_alias, source.relative_path)
                if key in items:
                    continue
                items[key] = {
                    "state": "DISCOVERED", "source_path": str(source.absolute_path),
                    "root_alias": source.root_alias, "relative_path": source.relative_path,
                    "attempts": 0, "available_at": now, "lease_until": None,
                    "observation": None, "reason_code": None, "updated_at": now,
                }
                discovered += 1
        return discovered

    def recover_expired(self) -> int:
        recovered = 0
        now = self.clock()
        with self.store.locked() as payload:
            for item in _items(payload).values():
                if isinstance(item, dict) and item.get("state") == "PROCESSING" and isinstance(item.get("lease_until"), (int, float)) and item["lease_until"] <= now:
                    item.update(state="RETRY", available_at=now, lease_until=None, reason_code="lease_expired", updated_at=now)
                    recovered += 1
        return recovered

    def settle(self, roots: Sequence[ApprovedRoot]) -> int:
        settled = 0
        now = self.clock()
        with self.store.locked() as payload:
            for item in _items(payload).values():
                if not isinstance(item, dict) or item.get("state") not in {"DISCOVERED", "SETTLING", "RETRY"}:
                    continue
                available_at = item.get("available_at", 0)
                if isinstance(available_at, (int, float)) and available_at > now:
                    continue
                try:
                    reference = resolve_source(Path(str(item.get("source_path"))), roots)
                    stable, observation = stable_observation(reference, item.get("observation") if isinstance(item.get("observation"), dict) else None, observed_at=now, settle_seconds=self.limits.settle_seconds)
                except SourceError as exc:
                    item.update(state="QUARANTINED", reason_code=exc.reason_code, lease_until=None, updated_at=now)
                    continue
                item.update(observation=observation, updated_at=now, state="READY" if stable else "SETTLING")
                if stable:
                    settled += 1
        return settled

    def claim(self, worker_id: str) -> tuple[str, dict[str, object]] | None:
        if not worker_id or len(worker_id) > 128:
            raise RuntimeErrorCode("worker_id_invalid")
        now = self.clock()
        with self.store.locked() as payload:
            items = _items(payload)
            for key in sorted(items):
                item = items[key]
                if isinstance(item, dict) and item.get("state") == "READY":
                    item.update(state="PROCESSING", worker_id=worker_id, lease_until=now + self.limits.lease_seconds, attempts=int(item.get("attempts", 0)) + 1, updated_at=now)
                    return key, json.loads(json.dumps(item))
        return None

    def heartbeat(self, key: str, worker_id: str) -> None:
        now = self.clock()
        with self.store.locked() as payload:
            item = _items(payload).get(key)
            if not isinstance(item, dict) or item.get("state") != "PROCESSING" or item.get("worker_id") != worker_id:
                raise RuntimeErrorCode("lease_owner_mismatch")
            item.update(lease_until=now + self.limits.lease_seconds, updated_at=now)

    def complete(self, key: str, worker_id: str, result: Mapping[str, object]) -> None:
        now = self.clock()
        status = str(result.get("status", "BLOCKED"))
        reason = str(result.get("reason_code", "processing_failed"))
        state = "DONE" if status == "DONE" else "REVIEW" if status == "REVIEW" else "QUARANTINED"
        with self.store.locked() as payload:
            item = _items(payload).get(key)
            if not isinstance(item, dict) or item.get("state") != "PROCESSING" or item.get("worker_id") != worker_id:
                raise RuntimeErrorCode("lease_owner_mismatch")
            item.update(state=state, lease_until=None, worker_id=None, reason_code=reason, result_counts=dict(result.get("counts", {})) if isinstance(result.get("counts"), Mapping) else {}, updated_at=now)

    def fail(self, key: str, worker_id: str, reason_code: str, *, permanent: bool = False) -> str:
        now = self.clock()
        with self.store.locked() as payload:
            item = _items(payload).get(key)
            if not isinstance(item, dict) or item.get("state") != "PROCESSING" or item.get("worker_id") != worker_id:
                raise RuntimeErrorCode("lease_owner_mismatch")
            attempts = int(item.get("attempts", 0))
            quarantine = permanent or attempts >= self.limits.max_attempts
            state = "QUARANTINED" if quarantine else "RETRY"
            available_at = now if quarantine else now + min(86400, self.limits.retry_base_seconds * (2 ** max(0, attempts - 1)))
            item.update(state=state, available_at=available_at, lease_until=None, worker_id=None, reason_code=reason_code, updated_at=now)
            return state

    def health(self) -> dict[str, object]:
        snapshot = self.store.snapshot()
        counts: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for item in _items(snapshot).values():
            if not isinstance(item, Mapping):
                continue
            state = str(item.get("state", "UNKNOWN"))
            counts[state] = counts.get(state, 0) + 1
            reason = item.get("reason_code")
            if isinstance(reason, str) and reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        blocked = counts.get("QUARANTINED", 0) > 0
        degraded = counts.get("REVIEW", 0) > 0 or counts.get("RETRY", 0) > 0
        return {"schema": "skeleton.family_document.worker_health.v1", "status": "BLOCKED" if blocked else "DEGRADED" if degraded else "DONE", "queue_counts": counts, "reason_counts": reasons}


class ProjectionOutbox:
    SCHEMA = "skeleton.family_document.projection_outbox.v1"

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.store = AtomicJsonStore(path, schema=self.SCHEMA)
        self.clock = clock

    def enqueue(self, key: str, payload_hash: str) -> None:
        now = self.clock()
        with self.store.locked() as payload:
            _items(payload).setdefault(key, {"status": "PENDING", "attempts": 0, "available_at": now, "payload_hash": payload_hash, "reason_code": None, "updated_at": now})

    def process_one(self, projector: Callable[[str, str], bool], *, max_attempts: int = 10) -> str:
        now = self.clock()
        with self.store.locked() as payload:
            items = _items(payload)
            for key in sorted(items):
                item = items[key]
                if not isinstance(item, dict) or item.get("status") not in {"PENDING", "RETRY"} or float(item.get("available_at", 0)) > now:
                    continue
                attempts = int(item.get("attempts", 0)) + 1
                try:
                    succeeded = bool(projector(key, str(item.get("payload_hash"))))
                except Exception:
                    succeeded = False
                if succeeded:
                    item.update(status="DONE", attempts=attempts, reason_code=None, updated_at=now)
                    return "DONE"
                if attempts >= max_attempts:
                    item.update(status="QUARANTINED", attempts=attempts, reason_code="projection_failed", updated_at=now)
                    return "QUARANTINED"
                item.update(status="RETRY", attempts=attempts, available_at=now + min(86400, 30 * (2 ** max(0, attempts - 1))), reason_code="projection_failed", updated_at=now)
                return "RETRY"
        return "IDLE"

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in _items(self.store.snapshot()).values():
            if isinstance(item, Mapping):
                status = str(item.get("status", "UNKNOWN"))
                counts[status] = counts.get(status, 0) + 1
        return counts


class FamilyDocumentWorker:
    def __init__(self, *, roots: Sequence[ApprovedRoot], journal: DurableJournal, processor: Processor, lock_path: Path, worker_id: str = "family-document-worker-1") -> None:
        self.roots = tuple(roots)
        self.journal = journal
        self.processor = processor
        self.lock_path = Path(lock_path)
        self.worker_id = worker_id
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path.touch(mode=0o600, exist_ok=True)

    @contextmanager
    def single_instance(self) -> Iterator[None]:
        with self.lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeErrorCode("worker_already_running") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def run_once(self) -> dict[str, object]:
        with self.single_instance():
            discovered = self.journal.discover(self.roots)
            recovered = self.journal.recover_expired()
            settled = self.journal.settle(self.roots)
            claimed = self.journal.claim(self.worker_id)
            if claimed is None:
                return {**self.journal.health(), "operation": "IDLE", "discovered": discovered, "recovered": recovered, "settled": settled}
            key, item = claimed
            try:
                result = self.processor.process(Path(str(item["source_path"])))
                self.journal.complete(key, self.worker_id, result)
            except (SourceError, RuntimeErrorCode) as exc:
                self.journal.fail(key, self.worker_id, exc.reason_code, permanent=True)
            except Exception:
                self.journal.fail(key, self.worker_id, "processing_failed", permanent=False)
            return {**self.journal.health(), "operation": "PROCESSED", "discovered": discovered, "recovered": recovered, "settled": settled}


def source_key(root_alias: str, relative_path: str) -> str:
    return "source:" + hashlib.sha256(f"{root_alias}\x1f{relative_path}".encode("utf-8")).hexdigest()[:40]


def _items(payload: Mapping[str, object]) -> dict[str, object]:
    items = payload.get("items")
    if not isinstance(items, dict):
        raise RuntimeErrorCode("runtime_state_invalid")
    return items


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

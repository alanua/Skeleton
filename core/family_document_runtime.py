from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol, Sequence

from core.family_document_sources import ApprovedRoot, SourceError, inventory_sources, resolve_source, stable_observation

_TRANSIENT_PROCESS_REASONS = frozenset({"adapter_failed", "memory_mutation_failed", "memory_exact_read_failed", "memory_exact_read_value_missing", "calendar_upsert_failed", "pdftotext_failed", "pdf_ocr_failed", "pdf_ocr_empty", "tesseract_failed", "office_conversion_failed", "ocr_command_failed", "ocr_timeout", "processing_failed"})


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
        if not 0 <= self.settle_seconds <= 3600: raise RuntimeErrorCode("settle_seconds_invalid")
        if not 30 <= self.lease_seconds <= 86400: raise RuntimeErrorCode("lease_seconds_invalid")
        if not 1 <= self.max_attempts <= 20: raise RuntimeErrorCode("max_attempts_invalid")
        if not 1 <= self.retry_base_seconds <= 86400: raise RuntimeErrorCode("retry_base_invalid")
        if not 1 <= self.max_inventory_files <= 100000: raise RuntimeErrorCode("inventory_limit_invalid")


class Processor(Protocol):
    def process(self, source: Path, *, dry_run: bool = False) -> Mapping[str, object]: ...


ReceiptTransport = Callable[[Mapping[str, object]], bool]


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
                payload = self._read(); yield payload; self._write(payload)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def snapshot(self) -> dict[str, object]:
        with self.locked() as payload: return json.loads(json.dumps(payload))

    def _read(self) -> dict[str, object]:
        if not self.path.exists(): return {"schema": self.schema, "items": {}}
        try: payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise RuntimeErrorCode("runtime_state_invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema") != self.schema or not isinstance(payload.get("items"), dict): raise RuntimeErrorCode("runtime_state_invalid")
        return payload

    def _write(self, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        fd, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent); temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o600); os.replace(temporary, self.path); self.path.chmod(0o600); _fsync_directory(self.path.parent)
        finally: temporary.unlink(missing_ok=True)


class DurableJournal:
    SCHEMA = "skeleton.family_document.runtime_journal.v1"
    def __init__(self, path: Path, limits: RuntimeLimits, *, clock: Callable[[], float] = time.time) -> None: self.store, self.limits, self.clock = AtomicJsonStore(path, schema=self.SCHEMA), limits, clock

    def discover(self, roots: Sequence[ApprovedRoot]) -> int:
        discovered = 0; now = self.clock()
        with self.store.locked() as payload:
            items = _items(payload)
            for source in inventory_sources(roots, max_files=self.limits.max_inventory_files):
                key = source_key(source.root_alias, source.relative_path)
                if key in items: continue
                items[key] = {"state": "DISCOVERED", "source_path": str(source.absolute_path), "root_alias": source.root_alias, "relative_path": source.relative_path, "attempts": 0, "available_at": now, "lease_until": None, "observation": None, "reason_code": None, "updated_at": now}; discovered += 1
        return discovered

    def recover_expired(self) -> int:
        recovered = 0; now = self.clock()
        with self.store.locked() as payload:
            for item in _items(payload).values():
                if isinstance(item, dict) and item.get("state") == "PROCESSING" and isinstance(item.get("lease_until"), (int, float)) and item["lease_until"] <= now:
                    item.update(state="RETRY", available_at=now, lease_until=None, reason_code="lease_expired", updated_at=now); recovered += 1
        return recovered

    def settle(self, roots: Sequence[ApprovedRoot]) -> int:
        settled = 0; now = self.clock()
        with self.store.locked() as payload:
            for item in _items(payload).values():
                if not isinstance(item, dict) or item.get("state") not in {"DISCOVERED", "SETTLING", "RETRY"}: continue
                if isinstance(item.get("available_at", 0), (int, float)) and item.get("available_at", 0) > now: continue
                try:
                    reference = resolve_source(Path(str(item.get("source_path"))), roots)
                    stable, observation = stable_observation(reference, item.get("observation") if isinstance(item.get("observation"), dict) else None, observed_at=now, settle_seconds=self.limits.settle_seconds)
                except SourceError as exc:
                    item.update(state="QUARANTINED", reason_code=exc.reason_code, lease_until=None, updated_at=now); continue
                if stable:
                    observation = {**observation, "stable": True, "accepted_at": now}
                item.update(observation=observation, updated_at=now, state="READY" if stable else "SETTLING")
                if stable: settled += 1
        return settled

    def claim(self, worker_id: str) -> tuple[str, dict[str, object]] | None:
        if not worker_id or len(worker_id) > 128: raise RuntimeErrorCode("worker_id_invalid")
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
            if not isinstance(item, dict) or item.get("state") != "PROCESSING" or item.get("worker_id") != worker_id: raise RuntimeErrorCode("lease_owner_mismatch")
            item.update(lease_until=now + self.limits.lease_seconds, updated_at=now)

    def complete(self, key: str, worker_id: str, result: Mapping[str, object]) -> str:
        now = self.clock(); status = str(result.get("status", "BLOCKED")); reason = str(result.get("reason_code", "processing_failed")); state = "DONE" if status == "DONE" else "REVIEW" if status == "REVIEW" else "FAILED" if status == "FAILED" else "QUARANTINED"
        with self.store.locked() as payload:
            item = _items(payload).get(key)
            if not isinstance(item, dict) or item.get("state") != "PROCESSING" or item.get("worker_id") != worker_id: raise RuntimeErrorCode("lease_owner_mismatch")
            item.update(state=state, lease_until=None, worker_id=None, reason_code=reason, result_counts=dict(result.get("counts", {})) if isinstance(result.get("counts"), Mapping) else {}, updated_at=now)
        return state

    def fail(self, key: str, worker_id: str, reason_code: str, *, permanent: bool = False) -> str:
        now = self.clock()
        with self.store.locked() as payload:
            item = _items(payload).get(key)
            if not isinstance(item, dict) or item.get("state") != "PROCESSING" or item.get("worker_id") != worker_id: raise RuntimeErrorCode("lease_owner_mismatch")
            attempts = int(item.get("attempts", 0)); quarantine = permanent or attempts >= self.limits.max_attempts; state = "QUARANTINED" if quarantine else "RETRY"; available_at = now if quarantine else now + min(86400, self.limits.retry_base_seconds * (2 ** max(0, attempts - 1)))
            item.update(state=state, available_at=available_at, lease_until=None, worker_id=None, reason_code=reason_code, updated_at=now); return state

    def health(self) -> dict[str, object]:
        counts: dict[str, int] = {}; reasons: dict[str, int] = {}
        for item in _items(self.store.snapshot()).values():
            if not isinstance(item, Mapping): continue
            state = str(item.get("state", "UNKNOWN")); counts[state] = counts.get(state, 0) + 1
            reason = item.get("reason_code")
            if isinstance(reason, str) and reason: reasons[reason] = reasons.get(reason, 0) + 1
        blocked, degraded = counts.get("QUARANTINED", 0) > 0 or counts.get("FAILED", 0) > 0, counts.get("REVIEW", 0) > 0 or counts.get("RETRY", 0) > 0
        return {"schema": "skeleton.family_document.worker_health.v1", "status": "BLOCKED" if blocked else "DEGRADED" if degraded else "DONE", "queue_counts": counts, "reason_counts": reasons}


class ProjectionOutbox:
    SCHEMA = "skeleton.family_document.projection_outbox.v1"
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None: self.store, self.clock = AtomicJsonStore(path, schema=self.SCHEMA), clock
    def enqueue(self, key: str, payload_hash: str) -> None:
        now = self.clock()
        with self.store.locked() as payload: _items(payload).setdefault(key, {"status": "PENDING", "attempts": 0, "available_at": now, "payload_hash": payload_hash, "reason_code": None, "updated_at": now})
    def process_one(self, projector: Callable[[str, str], bool], *, max_attempts: int = 10) -> str:
        now = self.clock()
        with self.store.locked() as payload:
            for key in sorted(_items(payload)):
                item = _items(payload)[key]
                if not isinstance(item, dict) or item.get("status") not in {"PENDING", "RETRY"} or float(item.get("available_at", 0)) > now: continue
                attempts = int(item.get("attempts", 0)) + 1
                try: succeeded = bool(projector(key, str(item.get("payload_hash"))))
                except Exception: succeeded = False
                if succeeded: item.update(status="DONE", attempts=attempts, reason_code=None, updated_at=now); return "DONE"
                if attempts >= max_attempts: item.update(status="QUARANTINED", attempts=attempts, reason_code="projection_failed", updated_at=now); return "QUARANTINED"
                item.update(status="RETRY", attempts=attempts, available_at=now + min(86400, 30 * (2 ** max(0, attempts - 1))), reason_code="projection_failed", updated_at=now); return "RETRY"
        return "IDLE"
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in _items(self.store.snapshot()).values():
            if isinstance(item, Mapping): status = str(item.get("status", "UNKNOWN")); counts[status] = counts.get(status, 0) + 1
        return counts


class ReceiptOutbox:
    SCHEMA = "skeleton.family_document.receipt_outbox.v1"
    HANDOFF_SCHEMA = "skeleton.family_document_receipt.telegram_handoff.v1"
    TRANSPORT_DEPENDENCY = "canonical Skeleton Telegram notification dispatcher for skeleton.family_document_receipt.telegram_handoff.v1"
    _KINDS = frozenset({"INTAKE_ACCEPTED", "TERMINAL"})
    _TERMINAL_STATES = frozenset({"DONE", "REVIEW", "RETRY", "FAILED", "QUARANTINED"})

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.store, self.clock = AtomicJsonStore(path, schema=self.SCHEMA), clock

    def enqueue_intake_accepted(self, task_identity: str, *, root_alias: str, relative_path: str) -> bool:
        payload = self._packet(
            task_identity,
            kind="INTAKE_ACCEPTED",
            state="ACCEPTED",
            reason_code="stable_file_accepted",
            counts={"accepted": 1},
            extra={
                "root_alias_hash": _hash_text(root_alias),
                "relative_path_hash": _hash_text(relative_path),
            },
        )
        return self._enqueue(payload)

    def enqueue_terminal(
        self,
        task_identity: str,
        *,
        state: str,
        reason_code: str,
        counts: Mapping[str, object] | None = None,
    ) -> bool:
        terminal = state if state in self._TERMINAL_STATES else "FAILED"
        payload = self._packet(
            task_identity,
            kind="TERMINAL",
            state=terminal,
            reason_code=_safe_reason(reason_code),
            counts=_safe_counts(counts or {}),
            extra={},
        )
        return self._enqueue(payload)

    def process_one(self, transport: ReceiptTransport, *, max_attempts: int = 10) -> str:
        now = self.clock()
        with self.store.locked() as payload:
            for key in sorted(_items(payload)):
                item = _items(payload)[key]
                if not isinstance(item, dict) or item.get("status") not in {"PENDING", "RETRY"} or float(item.get("available_at", 0)) > now:
                    continue
                attempts = int(item.get("attempts", 0)) + 1
                packet = item.get("packet")
                succeeded = False
                if isinstance(packet, Mapping):
                    try:
                        succeeded = bool(transport(packet))
                    except Exception:
                        succeeded = False
                if succeeded:
                    item.update(status="DONE", attempts=attempts, reason_code=None, updated_at=now)
                    return "DONE"
                if attempts >= max_attempts:
                    item.update(status="QUARANTINED", attempts=attempts, reason_code="telegram_delivery_failed", updated_at=now)
                    return "QUARANTINED"
                item.update(status="RETRY", attempts=attempts, available_at=now + min(86400, 30 * (2 ** max(0, attempts - 1))), reason_code="telegram_delivery_failed", updated_at=now)
                return "RETRY"
        return "IDLE"

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in _items(self.store.snapshot()).values():
            if isinstance(item, Mapping):
                status = str(item.get("status", "UNKNOWN"))
                counts[status] = counts.get(status, 0) + 1
        return counts

    def _enqueue(self, packet: Mapping[str, object]) -> bool:
        now = self.clock()
        key = str(packet["idempotency_key"])
        with self.store.locked() as payload:
            items = _items(payload)
            if key in items:
                return False
            items[key] = {
                "status": "PENDING",
                "attempts": 0,
                "available_at": now,
                "payload_hash": _hash_json(packet),
                "reason_code": None,
                "updated_at": now,
                "packet": dict(packet),
            }
        return True

    def _packet(
        self,
        task_identity: str,
        *,
        kind: str,
        state: str,
        reason_code: str,
        counts: Mapping[str, int],
        extra: Mapping[str, object],
    ) -> dict[str, object]:
        if kind not in self._KINDS:
            raise RuntimeErrorCode("receipt_kind_invalid")
        if not task_identity.startswith("source:"):
            raise RuntimeErrorCode("receipt_identity_invalid")
        idempotency_key = "family-document-receipt:" + _hash_text(f"{task_identity}\x1f{kind}\x1f{state}")
        packet = {
            "schema": self.HANDOFF_SCHEMA,
            "privacy_boundary": "PRIVATE_TELEGRAM_OPERATOR_RECEIPT",
            "notification_channel": "telegram_private",
            "transport_dependency": self.TRANSPORT_DEPENDENCY,
            "idempotency_key": idempotency_key,
            "task_identity": task_identity,
            "kind": kind,
            "state": state,
            "reason_code": reason_code,
            "counts": dict(counts),
            "public_safe": False,
            "content": {
                "template": "family_document_receipt",
                "document_content_included": False,
                "source_path_included": False,
            },
            **dict(extra),
        }
        json.dumps(packet, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        return packet


class FamilyDocumentWorker:
    def __init__(self, *, roots: Sequence[ApprovedRoot], journal: DurableJournal, processor: Processor, lock_path: Path, worker_id: str = "family-document-worker-1", receipt_outbox: ReceiptOutbox | None = None) -> None:
        self.roots, self.journal, self.processor, self.lock_path, self.worker_id, self.receipt_outbox = tuple(roots), journal, processor, Path(lock_path), worker_id, receipt_outbox
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700); self.lock_path.touch(mode=0o600, exist_ok=True)
    @contextmanager
    def single_instance(self) -> Iterator[None]:
        with self.lock_path.open("r+b") as handle:
            try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc: raise RuntimeErrorCode("worker_already_running") from exc
            try: yield
            finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    def run_once(self) -> dict[str, object]:
        with self.single_instance():
            discovered, recovered, settled = self.journal.discover(self.roots), self.journal.recover_expired(), self.journal.settle(self.roots); intake_receipts = self._enqueue_intake_receipts(); claimed = self.journal.claim(self.worker_id)
            if claimed is None: return {**self.journal.health(), "operation": "IDLE", "discovered": discovered, "recovered": recovered, "settled": settled, "intake_receipts": intake_receipts}
            key, item = claimed
            try:
                with _LeaseHeartbeat(self.journal, key, self.worker_id): result = self.processor.process(Path(str(item["source_path"])))
                status, reason = str(result.get("status", "BLOCKED")), str(result.get("reason_code", "processing_failed"))
                if status == "BLOCKED":
                    state = self.journal.fail(key, self.worker_id, reason, permanent=reason not in _TRANSIENT_PROCESS_REASONS)
                else:
                    state = self.journal.complete(key, self.worker_id, result)
                self._enqueue_terminal_receipt(key, state, reason, result.get("counts") if isinstance(result.get("counts"), Mapping) else {})
            except (SourceError, RuntimeErrorCode) as exc:
                state = self.journal.fail(key, self.worker_id, exc.reason_code, permanent=True)
                self._enqueue_terminal_receipt(key, state, exc.reason_code, {})
            except Exception:
                state = self.journal.fail(key, self.worker_id, "processing_failed", permanent=False)
                self._enqueue_terminal_receipt(key, state, "processing_failed", {})
            return {**self.journal.health(), "operation": "PROCESSED", "discovered": discovered, "recovered": recovered, "settled": settled, "intake_receipts": intake_receipts}

    def _enqueue_intake_receipts(self) -> int:
        if self.receipt_outbox is None:
            return 0
        enqueued = 0
        for key, item in _items(self.journal.store.snapshot()).items():
            if not isinstance(key, str) or not isinstance(item, Mapping):
                continue
            if item.get("state") not in {"READY", "PROCESSING", "DONE", "REVIEW", "RETRY", "FAILED", "QUARANTINED"}:
                continue
            observation = item.get("observation")
            if not isinstance(observation, Mapping) or observation.get("stable") is not True:
                continue
            root_alias = str(item.get("root_alias") or "")
            relative_path = str(item.get("relative_path") or "")
            if self.receipt_outbox.enqueue_intake_accepted(key, root_alias=root_alias, relative_path=relative_path):
                enqueued += 1
        return enqueued

    def _enqueue_terminal_receipt(self, key: str, state: str, reason_code: str, counts: Mapping[str, object]) -> None:
        if self.receipt_outbox is None:
            return
        self.receipt_outbox.enqueue_terminal(key, state=state, reason_code=reason_code, counts=counts)


class _LeaseHeartbeat:
    def __init__(self, journal: DurableJournal, key: str, worker_id: str) -> None: self.journal, self.key, self.worker_id, self.stop_event, self.thread = journal, key, worker_id, threading.Event(), None
    def __enter__(self) -> "_LeaseHeartbeat":
        interval = max(1.0, self.journal.limits.lease_seconds / 3)
        def beat() -> None:
            while not self.stop_event.wait(interval):
                try: self.journal.heartbeat(self.key, self.worker_id)
                except RuntimeErrorCode: return
        self.thread = threading.Thread(target=beat, name="family-document-lease-heartbeat", daemon=True); self.thread.start(); return self
    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop_event.set()
        if self.thread is not None: self.thread.join(timeout=2)


def source_key(root_alias: str, relative_path: str) -> str: return "source:" + hashlib.sha256(f"{root_alias}\x1f{relative_path}".encode("utf-8")).hexdigest()[:40]
def _items(payload: Mapping[str, object]) -> dict[str, object]:
    items = payload.get("items")
    if not isinstance(items, dict): raise RuntimeErrorCode("runtime_state_invalid")
    return items
def _safe_counts(counts: Mapping[str, object]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        safe[str(key)] = value
    return safe
def _safe_reason(reason_code: str) -> str:
    return reason_code if isinstance(reason_code, str) and re.fullmatch(r"[a-z0-9_]{1,96}", reason_code) else "processing_failed"
def _hash_text(value: str) -> str: return hashlib.sha256(value.encode("utf-8")).hexdigest()
def _hash_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(descriptor)
    finally: os.close(descriptor)

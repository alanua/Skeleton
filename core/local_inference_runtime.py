from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.local_inference_adapters import (
    AdapterRegistry,
    InferenceValidationError,
    validate_json_schema,
)

REQUEST_SCHEMA_ID = "skeleton.local_inference.request.v1"
RESULT_SCHEMA_ID = "skeleton.local_inference.result.v1"

REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema",
        "request_id",
        "request_type",
        "model",
        "payload",
        "idempotency_key",
        "created_at",
        "max_attempts",
        "timeout_seconds",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "const": REQUEST_SCHEMA_ID},
        "request_id": {"type": "string", "minLength": 32, "maxLength": 36},
        "request_type": {"type": "string", "minLength": 3, "maxLength": 96, "pattern": r"[a-z0-9_.-]+"},
        "model": {"type": "string", "minLength": 1, "maxLength": 128},
        "payload": {"type": "object"},
        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 240},
        "created_at": {"type": "string", "minLength": 20, "maxLength": 40},
        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 5},
        "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
    },
}

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema",
        "request_id",
        "request_type",
        "status",
        "model",
        "attempt",
        "completed_at",
        "reason_codes",
        "output",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "const": RESULT_SCHEMA_ID},
        "request_id": {"type": "string", "minLength": 32, "maxLength": 36},
        "request_type": {"type": "string", "minLength": 3, "maxLength": 96},
        "status": {"type": "string", "enum": ["DONE", "REVIEW"]},
        "model": {"type": "string", "minLength": 1, "maxLength": 128},
        "attempt": {"type": "integer", "minimum": 1, "maximum": 5},
        "completed_at": {"type": "string", "minLength": 20, "maxLength": 40},
        "reason_codes": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 96, "pattern": r"[A-Z0-9_]+"},
        },
        "output": {"type": "object"},
    },
}


class InferenceRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


class FileLock(AbstractContextManager["FileLock"]):
    def __init__(self, path: Path, *, nonblocking: bool = False) -> None:
        self.path = path
        self.nonblocking = nonblocking
        self._handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if self.nonblocking else 0)
        try:
            fcntl.flock(self._handle.fileno(), flags)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise InferenceRuntimeError("worker_already_running", retryable=False) from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class InferenceQueue:
    _STATES = ("pending", "processing", "retry", "done", "quarantine", "results", "state")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for state in self._STATES:
            path = self.root / state
            path.mkdir(mode=0o700, exist_ok=True)
            path.chmod(0o700)

    @property
    def worker_lock(self) -> Path:
        return self.root / "state" / "worker.lock"

    def submit(
        self,
        *,
        request_type: str,
        model: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
        timeout_seconds: int = 120,
    ) -> tuple[str, bool]:
        now = _utc_now()
        request_id = str(uuid.uuid4())
        request = {
            "schema": REQUEST_SCHEMA_ID,
            "request_id": request_id,
            "request_type": request_type,
            "model": model,
            "payload": dict(payload),
            "idempotency_key": idempotency_key,
            "created_at": now,
            "max_attempts": max_attempts,
            "timeout_seconds": timeout_seconds,
        }
        validate_json_schema(request, REQUEST_SCHEMA)
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        index_path = self.root / "state" / f"idempotency-{key_hash}.json"
        with FileLock(self.root / "state" / "submit.lock"):
            if index_path.exists():
                existing = _read_json(index_path)
                existing_id = existing.get("request_id")
                if isinstance(existing_id, str):
                    return existing_id, False
                raise InferenceRuntimeError("idempotency_index_invalid", retryable=False)
            envelope = {"request": request, "attempt": 0, "next_attempt_at": 0.0, "last_reason": None}
            _atomic_write_json(self.root / "pending" / f"{request_id}.json", envelope)
            _atomic_write_json(index_path, {"request_id": request_id})
        return request_id, True

    def claim_next(self, *, now: float | None = None) -> dict[str, Any] | None:
        current = time.time() if now is None else now
        candidates: list[Path] = []
        for path in sorted((self.root / "retry").glob("*.json")):
            try:
                envelope = _read_json(path)
            except InferenceRuntimeError:
                try:
                    os.replace(path, self.root / "quarantine" / path.name)
                except FileNotFoundError:
                    pass
                continue
            due = envelope.get("next_attempt_at", 0)
            if isinstance(due, (int, float)) and due <= current:
                candidates.append(path)
        candidates.extend(sorted((self.root / "pending").glob("*.json")))
        for source in candidates:
            target = self.root / "processing" / source.name
            try:
                os.replace(source, target)
            except FileNotFoundError:
                continue
            try:
                envelope = _read_json(target)
                envelope["attempt"] = int(envelope.get("attempt", 0)) + 1
                envelope["next_attempt_at"] = 0.0
                _request_from_envelope(envelope)
                _atomic_write_json(target, envelope)
            except (InferenceRuntimeError, TypeError, ValueError):
                try:
                    os.replace(target, self.root / "quarantine" / target.name)
                except FileNotFoundError:
                    pass
                continue
            return envelope
        return None

    def complete(self, envelope: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        request = _request_from_envelope(envelope)
        request_id = str(request["request_id"])
        validate_json_schema(result, RESULT_SCHEMA)
        _atomic_write_json(self.root / "results" / f"{request_id}.json", result)
        processing = self.root / "processing" / f"{request_id}.json"
        done = self.root / "done" / f"{request_id}.json"
        os.replace(processing, done)

    def fail(
        self,
        envelope: Mapping[str, Any],
        *,
        reason_code: str,
        retryable: bool,
        now: float | None = None,
    ) -> str:
        mutable = dict(envelope)
        request = _request_from_envelope(mutable)
        request_id = str(request["request_id"])
        attempt = int(mutable.get("attempt", 1))
        max_attempts = int(request["max_attempts"])
        mutable["last_reason"] = _safe_reason(reason_code)
        processing = self.root / "processing" / f"{request_id}.json"
        if retryable and attempt < max_attempts:
            delay = min(60, 2 ** max(0, attempt - 1))
            mutable["next_attempt_at"] = (time.time() if now is None else now) + delay
            _atomic_write_json(processing, mutable)
            os.replace(processing, self.root / "retry" / processing.name)
            return "RETRY"
        _atomic_write_json(processing, mutable)
        os.replace(processing, self.root / "quarantine" / processing.name)
        return "QUARANTINED"

    def recover_stale_processing(self, *, stale_after_seconds: int = 600, now: float | None = None) -> int:
        current = time.time() if now is None else now
        recovered = 0
        for path in sorted((self.root / "processing").glob("*.json")):
            try:
                age = current - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < stale_after_seconds:
                continue
            envelope = _read_json(path)
            envelope["last_reason"] = "stale_processing_recovered"
            envelope["next_attempt_at"] = current
            _atomic_write_json(path, envelope)
            try:
                os.replace(path, self.root / "retry" / path.name)
            except FileNotFoundError:
                continue
            recovered += 1
        return recovered

    def status(self) -> dict[str, Any]:
        counts = {
            state: sum(1 for _ in (self.root / state).glob("*.json"))
            for state in ("pending", "processing", "retry", "done", "quarantine")
        }
        return {
            "schema": "skeleton.local_inference.aggregate_status.v1",
            "ready": True,
            "counts": counts,
        }

    def read_result(self, request_id: str) -> dict[str, Any] | None:
        path = self.root / "results" / f"{request_id}.json"
        return _read_json(path) if path.exists() else None


class OllamaClient:
    def __init__(self, endpoint: str = "http://127.0.0.1:11434") -> None:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise InferenceRuntimeError("ollama_endpoint_invalid", retryable=False)
        if parsed.path not in ("", "/") or not parsed.hostname or not _is_loopback(parsed.hostname):
            raise InferenceRuntimeError("ollama_endpoint_not_loopback", retryable=False)
        self.endpoint = endpoint.rstrip("/")

    def generate(self, *, model: str, prompt: str, timeout_seconds: int) -> str:
        body = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint + "/api/generate",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise InferenceRuntimeError("ollama_request_failed", retryable=True) from exc
        if len(raw) > 2 * 1024 * 1024:
            raise InferenceRuntimeError("ollama_response_oversized", retryable=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InferenceRuntimeError("ollama_response_invalid", retryable=True) from exc
        text = payload.get("response") if isinstance(payload, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            raise InferenceRuntimeError("ollama_response_missing", retryable=True)
        return text


class LocalInferenceWorker:
    def __init__(
        self,
        queue: InferenceQueue,
        registry: AdapterRegistry,
        client: OllamaClient,
        *,
        allowed_models: set[str],
    ) -> None:
        if not allowed_models or any(not item.strip() for item in allowed_models):
            raise InferenceRuntimeError("model_allowlist_invalid", retryable=False)
        self.queue = queue
        self.registry = registry
        self.client = client
        self.allowed_models = frozenset(allowed_models)

    def process_one(self) -> bool:
        envelope = self.queue.claim_next()
        if envelope is None:
            return False
        request = _request_from_envelope(envelope)
        attempt = int(envelope["attempt"])
        try:
            model = str(request["model"])
            if model not in self.allowed_models:
                raise InferenceRuntimeError("model_not_allowed", retryable=False)
            try:
                adapter = self.registry.get(str(request["request_type"]))
                prompt = adapter.prompt_builder(request["payload"])
            except InferenceValidationError as exc:
                raise InferenceRuntimeError(str(exc), retryable=False) from exc
            text = self.client.generate(
                model=model,
                prompt=prompt,
                timeout_seconds=int(request["timeout_seconds"]),
            )
            try:
                parsed = json.loads(text)
                if not isinstance(parsed, Mapping):
                    raise InferenceValidationError("model_output_not_object")
                output = adapter.output_validator(parsed)
            except (json.JSONDecodeError, InferenceValidationError) as exc:
                raise InferenceRuntimeError("model_output_invalid", retryable=True) from exc
            status = "REVIEW" if output.get("route") == "REVIEW" else "DONE"
            reason_codes = output.get("reason_codes", []) if status == "REVIEW" else []
            result = {
                "schema": RESULT_SCHEMA_ID,
                "request_id": request["request_id"],
                "request_type": request["request_type"],
                "status": status,
                "model": model,
                "attempt": attempt,
                "completed_at": _utc_now(),
                "reason_codes": list(reason_codes),
                "output": dict(output),
            }
            self.queue.complete(envelope, result)
        except InferenceRuntimeError as exc:
            self.queue.fail(
                envelope,
                reason_code=exc.reason_code,
                retryable=exc.retryable,
            )
        return True


def _request_from_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    request = envelope.get("request")
    if not isinstance(request, Mapping):
        raise InferenceRuntimeError("queue_envelope_invalid", retryable=False)
    normalized = dict(request)
    try:
        validate_json_schema(normalized, REQUEST_SCHEMA)
    except InferenceValidationError as exc:
        raise InferenceRuntimeError("queue_request_invalid", retryable=False) from exc
    return normalized


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with temporary.open("x", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InferenceRuntimeError("queue_file_invalid", retryable=False) from exc
    if not isinstance(value, dict):
        raise InferenceRuntimeError("queue_file_invalid", retryable=False)
    return value


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _safe_reason(value: str) -> str:
    normalized = "".join(character if character.isalnum() or character in "_.:-" else "_" for character in value)
    return normalized[:96] or "inference_failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

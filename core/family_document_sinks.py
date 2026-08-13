from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MEMORY_GATEWAY_RESPONSE_SCHEMA
from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA


class FamilyDocumentSinkError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ArchiveWriteReceipt:
    path: Path
    sha256: str
    bytes_written: int


class VerifiedArchive:
    """Private binary-first archive with atomic write and exact readback."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def write_source_once(
        self,
        source: str | Path,
        name: str,
        *,
        expected_sha256: str,
    ) -> ArchiveWriteReceipt:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FamilyDocumentSinkError("archive_source_missing", "archive source is not a regular file")
        self._validate_name(name)
        source_bytes = source_path.read_bytes()
        digest = hashlib.sha256(source_bytes).hexdigest()
        if digest != expected_sha256:
            raise FamilyDocumentSinkError("archive_source_hash_mismatch", "source hash changed before archive")
        path = self.root / name
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise FamilyDocumentSinkError("archive_collision", "archive path already has different content")
            return ArchiveWriteReceipt(path=path, sha256=digest, bytes_written=len(existing))
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(source_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        readback = path.read_bytes()
        if len(readback) != len(source_bytes) or hashlib.sha256(readback).hexdigest() != digest:
            raise FamilyDocumentSinkError("archive_readback_failed", "archive readback verification failed")
        return ArchiveWriteReceipt(path=path, sha256=digest, bytes_written=len(readback))

    def write_metadata_once(self, name: str, record: Mapping[str, Any]) -> ArchiveWriteReceipt:
        self._validate_name(name)
        encoded = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        path = self.root / name
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise FamilyDocumentSinkError("archive_metadata_collision", "archive metadata path already differs")
            return ArchiveWriteReceipt(path=path, sha256=digest, bytes_written=len(existing))
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        readback = path.read_bytes()
        if readback != encoded:
            raise FamilyDocumentSinkError("archive_metadata_readback_failed", "archive metadata readback failed")
        return ArchiveWriteReceipt(path=path, sha256=digest, bytes_written=len(readback))

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or "/" in name or "\\" in name or name.startswith(".") or name in {".", ".."}:
            raise FamilyDocumentSinkError("archive_name_invalid", "archive name is invalid")


class CommandMemoryGateway:
    """MemoryGateway protocol bridge. The private command is runtime-owned, never document-owned."""

    def __init__(self, command: Sequence[str], *, timeout_seconds: float = 30.0) -> None:
        normalized = tuple(str(item) for item in command)
        if not normalized or any(not item or "\x00" in item for item in normalized):
            raise FamilyDocumentSinkError("memory_gate_adapter_command_invalid", "memory gate adapter command is invalid")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise FamilyDocumentSinkError("memory_gate_timeout_invalid", "memory gate timeout is invalid")
        self.command = normalized
        self.timeout_seconds = float(timeout_seconds)

    def execute(self, request: Mapping[str, Any]) -> dict[str, object]:
        encoded = json.dumps(dict(request), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            completed = subprocess.run(
                self.command,
                input=encoded,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FamilyDocumentSinkError("memory_gate_adapter_unavailable", "memory gate adapter is unavailable") from exc
        if completed.returncode != 0:
            raise FamilyDocumentSinkError("memory_gate_adapter_failed", "memory gate adapter rejected the request")
        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FamilyDocumentSinkError("memory_gate_adapter_response_invalid", "memory gate response is not JSON") from exc
        if not isinstance(response, dict) or response.get("schema") != MEMORY_GATEWAY_RESPONSE_SCHEMA:
            raise FamilyDocumentSinkError("memory_gate_adapter_response_invalid", "memory gate response schema is invalid")
        if not isinstance(response.get("payload"), Mapping):
            raise FamilyDocumentSinkError("memory_gate_adapter_response_invalid", "memory gate response payload is invalid")
        return response


class DurableProjectionOutbox:
    """Canonical-first durable projection work queue backed by JSONL."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "family_document_projection_outbox.jsonl"

    def enqueue(self, event: Mapping[str, Any]) -> dict[str, object]:
        encoded = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        work_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        row = {"schema": "skeleton.family_document_projection_outbox.v1", "state": "QUEUED", "work_key": work_key, **dict(event)}
        line = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        if self.path.exists() and work_key in self.path.read_text(encoding="utf-8"):
            return {"status": "DUPLICATE", "work_key": work_key}
        with self.path.open("a", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return {"status": "QUEUED", "work_key": work_key}


class TelegramSender:
    """Minimal outbound Telegram transport. Credentials are injected by the external secret runtime."""

    API_BASE = "https://api.telegram.org"

    def __init__(
        self,
        *,
        bot_env: str = "SKELETON_TG_BOT",
        chat_env: str = "SKELETON_TG_CHAT",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.bot_env = bot_env
        self.chat_env = chat_env
        self.timeout_seconds = timeout_seconds

    def send(self, text: str) -> None:
        bot = os.environ.get(self.bot_env, "").strip()
        chat = os.environ.get(self.chat_env, "").strip()
        if not bot or not chat:
            raise FamilyDocumentSinkError("telegram_credentials_missing", "Telegram runtime credentials are unavailable")
        payload = json.dumps({"chat_id": chat, "text": text}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.API_BASE}/bot{bot}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (OSError, urllib.error.URLError) as exc:
            raise FamilyDocumentSinkError("telegram_delivery_failed", "Telegram delivery failed") from exc
        try:
            decoded = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FamilyDocumentSinkError("telegram_response_invalid", "Telegram response is invalid") from exc
        if not isinstance(decoded, Mapping) or decoded.get("ok") is not True:
            raise FamilyDocumentSinkError("telegram_delivery_failed", "Telegram rejected the notification")


class TelegramNotificationOutbox:
    """Private idempotent notification outbox; delivery failure never rolls back canonical work."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve() / "family_document_telegram_outbox"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def enqueue_once(self, *, key: str, text: str) -> dict[str, object]:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        path = self.root / f"{digest}.json"
        if path.exists():
            row = self._read(path)
            return {"status": str(row.get("state", "QUEUED")), "notification_id": digest}
        row = {
            "schema": "skeleton.family_document_telegram_notification.v1",
            "notification_id": digest,
            "key": key,
            "text": text,
            "state": "QUEUED",
        }
        self._write(path, row)
        return {"status": "QUEUED", "notification_id": digest}

    def flush(self, sender: TelegramSender) -> dict[str, int]:
        delivered = 0
        pending = 0
        for path in sorted(self.root.glob("*.json")):
            row = self._read(path)
            if row.get("state") == "DELIVERED":
                continue
            try:
                sender.send(str(row.get("text", "")))
            except FamilyDocumentSinkError:
                pending += 1
                continue
            row["state"] = "DELIVERED"
            self._write(path, row)
            delivered += 1
        return {"delivered": delivered, "pending": pending}

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FamilyDocumentSinkError("telegram_outbox_corrupt", "Telegram outbox entry is corrupt") from exc
        if not isinstance(value, dict):
            raise FamilyDocumentSinkError("telegram_outbox_corrupt", "Telegram outbox entry is invalid")
        return value

    @staticmethod
    def _write(path: Path, row: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        encoded = json.dumps(dict(row), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def private_put_request(
    *,
    fact_namespace: str,
    fact_id: str,
    value: Mapping[str, Any],
    source_hash: str,
    idempotency_key: str,
    approval_ref: str,
) -> dict[str, object]:
    if not approval_ref or approval_ref.lower().startswith("synthetic"):
        raise FamilyDocumentSinkError("approval_ref_invalid", "a non-synthetic approval reference is required")
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": "skeleton.memory.private_mutate",
        "payload": {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "family_documents",
            "fact_namespace": fact_namespace,
            "fact_id": fact_id,
            "value": dict(value),
            "source_hash": source_hash,
            "idempotency_key": idempotency_key,
            "actor_ref": "family-document-intake",
            "reason_code": "family-document-intake",
            "approval_ref": approval_ref,
        },
    }


def aggregate_receipt(*, status: str, duplicate: bool, event_count: int) -> dict[str, object]:
    return {
        "schema": "skeleton.family_document_receipt.v1",
        "privacy": "aggregate_only",
        "status": status,
        "aggregate_counts": {
            "documents_seen": 1,
            "duplicates": 1 if duplicate else 0,
            "event_candidates": event_count,
        },
    }

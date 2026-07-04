from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from core.runner_execution_broker import default_broker
from core.runner_executors import ExecutionContext
from core.task_envelope import TaskEnvelope, parse_task_envelope


class TaskEnvelopeRuntimeError(RuntimeError):
    pass


def execute_task_envelope_file(
    envelope_path: str | Path,
    *,
    context: ExecutionContext | None = None,
    evidence_dir: str | Path | None = None,
    idempotency_dir: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(envelope_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise TaskEnvelopeRuntimeError("envelope path is not a file")
    if path.stat().st_mode & 0o077:
        raise TaskEnvelopeRuntimeError("envelope file must be private")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TaskEnvelopeRuntimeError("envelope JSON must be an object")
    envelope = parse_task_envelope(raw)
    active_context = context or ExecutionContext(
        targets={},
        entrypoints={},
        roots={},
        environment=dict(os.environ),
    )

    if idempotency_dir is None:
        return _execute_and_persist(
            envelope,
            context=active_context,
            evidence_dir=evidence_dir,
        )

    idempotency_root = _private_directory(idempotency_dir)
    receipt_path = idempotency_root / f"{envelope.idempotency_key}.json"
    lock_path = idempotency_root / f".{envelope.idempotency_key}.lock"
    with _exclusive_file_lock(lock_path):
        if receipt_path.is_file():
            receipt = _load_receipt(receipt_path)
            if receipt.get("envelope_hash") != envelope.canonical_hash:
                raise TaskEnvelopeRuntimeError(
                    "idempotency key is already bound to another envelope"
                )
            return receipt

        receipt = _execute_and_persist(
            envelope,
            context=active_context,
            evidence_dir=evidence_dir,
        )
        _write_private_json(receipt_path, receipt)
        return receipt


def _execute_and_persist(
    envelope: TaskEnvelope,
    *,
    context: ExecutionContext,
    evidence_dir: str | Path | None,
) -> dict[str, Any]:
    result = default_broker(context).execute(envelope)
    if evidence_dir is not None:
        evidence_root = _private_directory(evidence_dir)
        destination = evidence_root / f"{envelope.task_id}.json"
        _write_private_json(destination, result["private_evidence"])
    receipt = result["public_receipt"]
    if not isinstance(receipt, dict):
        raise TaskEnvelopeRuntimeError("public receipt must be an object")
    return receipt


def _private_directory(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def _load_receipt(path: Path) -> dict[str, Any]:
    if path.stat().st_mode & 0o077:
        raise TaskEnvelopeRuntimeError("idempotency receipt must be private")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TaskEnvelopeRuntimeError("idempotency receipt must be an object")
    return value


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(descriptor, "r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if path.exists():
            path.chmod(0o600)

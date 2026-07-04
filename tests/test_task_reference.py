from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.task_reference import (
    TaskEnvelopeReference,
    TaskEnvelopeReferenceError,
    resolve_task_envelope_file,
)


def test_resolve_private_envelope_by_hash(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    envelope = inbox / "task-001.json"
    envelope.write_text('{"schema":"example"}\n', encoding="utf-8")
    envelope.chmod(0o600)
    digest = hashlib.sha256(envelope.read_bytes()).hexdigest()

    resolved = resolve_task_envelope_file(
        TaskEnvelopeReference("task-001", digest),
        inbox=inbox,
    )

    assert resolved == envelope.resolve()


def test_resolve_rejects_hash_mismatch(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    envelope = inbox / "task-001.json"
    envelope.write_text("{}\n", encoding="utf-8")
    envelope.chmod(0o600)

    with pytest.raises(TaskEnvelopeReferenceError, match="hash mismatch"):
        resolve_task_envelope_file(
            TaskEnvelopeReference("task-001", "0" * 64),
            inbox=inbox,
        )


def test_resolve_rejects_broad_permissions(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    envelope = inbox / "task-001.json"
    envelope.write_text("{}\n", encoding="utf-8")
    envelope.chmod(0o644)
    digest = hashlib.sha256(envelope.read_bytes()).hexdigest()

    with pytest.raises(TaskEnvelopeReferenceError, match="private"):
        resolve_task_envelope_file(
            TaskEnvelopeReference("task-001", digest),
            inbox=inbox,
        )

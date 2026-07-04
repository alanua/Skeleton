from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class TaskEnvelopeReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class TaskEnvelopeReference:
    reference_id: str
    content_hash: str


def resolve_task_envelope_file(
    reference: TaskEnvelopeReference,
    *,
    inbox: str | Path,
) -> Path:
    root = Path(inbox).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise TaskEnvelopeReferenceError("inbox is not a directory")
    candidate = (root / f"{reference.reference_id}.json").resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise TaskEnvelopeReferenceError("reference escaped inbox") from exc
    if not candidate.is_file():
        raise TaskEnvelopeReferenceError("envelope file is missing")
    if candidate.stat().st_mode & 0o077:
        raise TaskEnvelopeReferenceError("envelope file must be private")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if digest != reference.content_hash:
        raise TaskEnvelopeReferenceError("envelope hash mismatch")
    return candidate

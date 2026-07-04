from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskEnvelopeReference:
    reference_id: str
    content_hash: str

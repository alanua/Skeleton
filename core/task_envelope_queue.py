from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueueEnvelopeRequest:
    issue_number: int
    reference_id: str
    content_hash: str

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvelopeReference:
    issue_number: int
    envelope_ref: str
    envelope_hash: str

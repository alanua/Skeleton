"""Pure, explicit-evidence scan sessions. No clock, OCR, IO, or pairing heuristics."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

Side = Literal["front", "back"]


@dataclass(frozen=True)
class PageSlot:
    """One physical scanned page, in acquisition order within its pass."""

    page_id: str
    sheet: int
    side: Side
    kind: Literal["page", "blank"] = "page"

    def __post_init__(self) -> None:
        if not self.page_id or self.sheet < 1 or self.side not in ("front", "back") or self.kind not in ("page", "blank"):
            raise ValueError("invalid physical page slot")


@dataclass(frozen=True)
class ScanEvent:
    event_id: str
    session_id: str
    profile_id: str
    kind: Literal["open", "pass", "complete", "stale"]
    evidence: str
    pass_id: str = ""
    slots: tuple[PageSlot, ...] = ()
    absent_backs: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.event_id, self.session_id, self.profile_id)):
            raise ValueError("explicit event, session and profile required")
        if self.kind not in ("open", "pass", "complete", "stale"):
            raise ValueError("invalid event kind")
        if self.kind != "pass" and (self.pass_id or self.slots):
            raise ValueError("only pass events contain physical slots")
        if self.kind != "complete" and self.absent_backs:
            raise ValueError("only completion declares absent backs")
        if any(sheet < 1 for sheet in self.absent_backs) or len(set(self.absent_backs)) != len(self.absent_backs):
            raise ValueError("invalid absent backs")

    def mapping(self) -> dict:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: dict) -> ScanEvent:
        return cls(**{**value, "slots": tuple(PageSlot(**slot) for slot in value.get("slots", ())),
                      "absent_backs": tuple(value.get("absent_backs", ()))})


@dataclass(frozen=True)
class ScanSession:
    session_id: str
    profile_id: str
    state: Literal["OPEN", "REVIEW", "FINALIZED"] = "OPEN"
    events: tuple[ScanEvent, ...] = ()
    reason: str = ""

    @property
    def slots(self) -> tuple[PageSlot, ...]:
        return tuple(slot for event in self.events if event.kind == "pass" for slot in event.slots)

    @property
    def absent_backs(self) -> tuple[int, ...]:
        return next((event.absent_backs for event in reversed(self.events) if event.kind == "complete"), ())


def apply_event(session: ScanSession | None, event: ScanEvent) -> ScanSession:
    """Replay is a no-op; conflicting identities fail; uncertain evidence needs review.

    REVIEW is sticky: resolving uncertain evidence requires a separate reviewed workflow.
    Time can only request review and can never complete a session.
    """
    if session is None:
        if event.kind != "open":
            raise ValueError("session must be explicitly opened")
        session = ScanSession(event.session_id, event.profile_id)
    if (session.session_id, session.profile_id) != (event.session_id, event.profile_id):
        raise ValueError("session/profile mismatch")
    for previous in session.events:
        if previous.event_id == event.event_id:
            if previous != event:
                raise ValueError("event identity reused")
            return session
    if session.state == "FINALIZED":
        raise ValueError("finalized session is immutable")
    reason = session.reason
    state = session.state
    if not event.evidence.strip():
        state, reason = "REVIEW", "missing_explicit_evidence"
    if event.kind == "stale":
        state, reason = "REVIEW", "stale_session"
    if event.kind == "open" and session.events:
        state, reason = "REVIEW", "duplicate_session_open"
    if event.kind == "pass":
        previous_passes = [item for item in session.events if item.kind == "pass" and item.pass_id == event.pass_id]
        if previous_passes:
            previous = previous_passes[0]
            if (previous.slots, previous.evidence) == (event.slots, event.evidence):
                return session
            state, reason = "REVIEW", "conflicting_pass"
        slots = session.slots + event.slots
        if (not event.pass_id or not event.slots or
                len({slot.page_id for slot in slots}) != len(slots) or
                len({(slot.sheet, slot.side) for slot in slots}) != len(slots)):
            state, reason = "REVIEW", "ambiguous_side_mapping"
    if event.kind == "complete":
        fronts = {slot.sheet for slot in session.slots if slot.side == "front"}
        backs = {slot.sheet for slot in session.slots if slot.side == "back"}
        absent = set(event.absent_backs)
        if not fronts or fronts != set(range(1, max(fronts) + 1)) or backs & absent or fronts != backs | absent:
            state, reason = "REVIEW", "incomplete_side_mapping"
        elif state == "OPEN":
            state = "FINALIZED"
    return replace(session, state=state, reason=reason, events=session.events + (event,))
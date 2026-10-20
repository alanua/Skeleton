from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from core.topology_fact import TOPOLOGY_FACT_TYPES, TopologyFact


SELF_KNOWLEDGE_RECEIPT_SCHEMA = "skeleton.self_knowledge_receipt.v1"
RESOLVED = "RESOLVED"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
NOT_FOUND = "NOT_FOUND"
STALE = "STALE"
SUPERSEDED = "SUPERSEDED"


class SelfKnowledgeResolverError(ValueError):
    pass


@dataclass(frozen=True)
class SelfKnowledgeReceipt:
    schema: str
    status: str
    fact_type: str
    lookup_key: str
    resolved_ref: str | None
    value_class: str | None
    selected_fact_ref: str | None
    candidate_fact_refs: tuple[str, ...]
    stale_fact_refs: tuple[str, ...]
    superseded_fact_refs: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    freshness_class: str
    verified_revision: int | None
    verified_at: datetime | None
    checked_at: datetime
    reason: str

    def public_receipt(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "status": self.status,
            "fact_type": self.fact_type,
            "lookup_key": self.lookup_key,
            "resolved_ref": self.resolved_ref,
            "value_class": self.value_class,
            "selected_fact_ref": self.selected_fact_ref,
            "candidate_fact_refs": list(self.candidate_fact_refs),
            "stale_fact_refs": list(self.stale_fact_refs),
            "superseded_fact_refs": list(self.superseded_fact_refs),
            "provenance_refs": list(self.provenance_refs),
            "freshness_class": self.freshness_class,
            "verified_revision": self.verified_revision,
            "verified_at": None if self.verified_at is None else _format_datetime(self.verified_at),
            "checked_at": _format_datetime(self.checked_at),
            "reason": self.reason,
        }


class SelfKnowledgeResolver:
    def __init__(self, facts: Iterable[TopologyFact], *, now: datetime | None = None) -> None:
        self._facts = tuple(facts)
        self._now = _require_datetime("now", now or datetime.now(timezone.utc))
        self._superseded_ids = {fact_id for fact in self._facts for fact_id in fact.supersedes}
        self._superseded_ids.update(fact.fact_id for fact in self._facts if fact.superseded_by is not None)

    def resolve(self, fact_type: str, lookup_key: str, *, now: datetime | None = None) -> SelfKnowledgeReceipt:
        if fact_type not in TOPOLOGY_FACT_TYPES:
            raise SelfKnowledgeResolverError("invalid_fact_type")
        if not isinstance(lookup_key, str) or not lookup_key:
            raise SelfKnowledgeResolverError("invalid_lookup_key")
        checked_at = _require_datetime("now", now or self._now)
        candidates = tuple(fact for fact in self._facts if fact.fact_type == fact_type and fact.lookup_key == lookup_key)
        if not candidates:
            return self._receipt(
                status=NOT_FOUND,
                fact_type=fact_type,
                lookup_key=lookup_key,
                checked_at=checked_at,
                reason="no_matching_fact",
            )

        superseded = tuple(fact for fact in candidates if self._is_superseded(fact))
        verified = tuple(fact for fact in candidates if fact.status == "VERIFIED" and not self._is_superseded(fact))
        fresh = tuple(fact for fact in verified if fact.is_fresh_at(checked_at))
        stale = tuple(fact for fact in verified if not fact.is_fresh_at(checked_at))

        if not fresh:
            status = SUPERSEDED if superseded and not stale else STALE
            return self._receipt(
                status=status,
                fact_type=fact_type,
                lookup_key=lookup_key,
                checked_at=checked_at,
                reason="only_superseded_facts" if status == SUPERSEDED else "no_current_verified_fact",
                candidate_fact_refs=tuple(fact.fact_id for fact in candidates),
                stale_fact_refs=tuple(fact.fact_id for fact in stale),
                superseded_fact_refs=tuple(fact.fact_id for fact in superseded),
                provenance_refs=tuple(fact.provenance_ref for fact in candidates),
                freshness_class="STALE",
            )

        winner_key = max(_rank_key(fact) for fact in fresh)
        winners = tuple(fact for fact in fresh if _rank_key(fact) == winner_key)
        distinct_routes = {(fact.value_class, fact.value_ref) for fact in winners}
        if len(distinct_routes) != 1:
            return self._receipt(
                status=NEEDS_VERIFICATION,
                fact_type=fact_type,
                lookup_key=lookup_key,
                checked_at=checked_at,
                reason="ambiguous_equal_authority_facts",
                candidate_fact_refs=tuple(fact.fact_id for fact in candidates),
                stale_fact_refs=tuple(fact.fact_id for fact in stale),
                superseded_fact_refs=tuple(fact.fact_id for fact in superseded),
                provenance_refs=tuple(fact.provenance_ref for fact in candidates),
                freshness_class="CURRENT",
            )

        selected = sorted(winners, key=lambda fact: fact.fact_id)[0]
        return self._receipt(
            status=RESOLVED,
            fact_type=fact_type,
            lookup_key=lookup_key,
            checked_at=checked_at,
            reason="selected_newest_verified_current_fact",
            resolved_ref=selected.value_ref,
            value_class=selected.value_class,
            selected_fact_ref=selected.fact_id,
            candidate_fact_refs=tuple(fact.fact_id for fact in candidates),
            stale_fact_refs=tuple(fact.fact_id for fact in stale),
            superseded_fact_refs=tuple(fact.fact_id for fact in superseded),
            provenance_refs=(selected.provenance_ref,),
            freshness_class=selected.freshness_class,
            verified_revision=selected.verified_revision,
            verified_at=selected.verified_at,
        )

    def resolve_host(self, lookup_key: str, *, now: datetime | None = None) -> SelfKnowledgeReceipt:
        return self.resolve("host", lookup_key, now=now)

    def resolve_repository(self, lookup_key: str, *, now: datetime | None = None) -> SelfKnowledgeReceipt:
        return self.resolve("repository", lookup_key, now=now)

    def resolve_runtime(self, lookup_key: str, *, now: datetime | None = None) -> SelfKnowledgeReceipt:
        return self.resolve("runtime", lookup_key, now=now)

    def resolve_entrypoint(self, lookup_key: str, *, now: datetime | None = None) -> SelfKnowledgeReceipt:
        return self.resolve("entrypoint", lookup_key, now=now)

    def _is_superseded(self, fact: TopologyFact) -> bool:
        return fact.fact_id in self._superseded_ids

    def _receipt(
        self,
        *,
        status: str,
        fact_type: str,
        lookup_key: str,
        checked_at: datetime,
        reason: str,
        resolved_ref: str | None = None,
        value_class: str | None = None,
        selected_fact_ref: str | None = None,
        candidate_fact_refs: tuple[str, ...] = (),
        stale_fact_refs: tuple[str, ...] = (),
        superseded_fact_refs: tuple[str, ...] = (),
        provenance_refs: tuple[str, ...] = (),
        freshness_class: str = "UNKNOWN",
        verified_revision: int | None = None,
        verified_at: datetime | None = None,
    ) -> SelfKnowledgeReceipt:
        return SelfKnowledgeReceipt(
            schema=SELF_KNOWLEDGE_RECEIPT_SCHEMA,
            status=status,
            fact_type=fact_type,
            lookup_key=lookup_key,
            resolved_ref=resolved_ref,
            value_class=value_class,
            selected_fact_ref=selected_fact_ref,
            candidate_fact_refs=tuple(sorted(candidate_fact_refs)),
            stale_fact_refs=tuple(sorted(stale_fact_refs)),
            superseded_fact_refs=tuple(sorted(superseded_fact_refs)),
            provenance_refs=tuple(sorted(set(provenance_refs))),
            freshness_class=freshness_class,
            verified_revision=verified_revision,
            verified_at=None if verified_at is None else _require_datetime("verified_at", verified_at),
            checked_at=checked_at,
            reason=reason,
        )


def _rank_key(fact: TopologyFact) -> tuple[int, int, datetime]:
    return (fact.authority, fact.verified_revision, fact.verified_at)


def _require_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SelfKnowledgeResolverError(f"invalid_{name}")
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence


class FamilyDocumentTaxonomyError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class Person:
    person_id: str
    display_name: str
    surnames: tuple[str, ...]
    country: str = "unknown"


@dataclass(frozen=True)
class RouteDecision:
    status: str
    person_id: str
    topic: str
    country: str
    reason_code: str | None = None


TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tax", ("tax", "steuer", "irs", "assessment")),
    ("identity", ("passport", "birth certificate", "identity", "id card")),
    ("medical", ("medical", "diagnosis", "clinic", "hospital")),
    ("school", ("school", "university", "transcript", "kindergarten")),
    ("property", ("deed", "lease", "mortgage", "grundbuch")),
    ("insurance", ("insurance", "policy", "claim")),
)


def route_document(metadata: Mapping[str, object], people: Sequence[Person]) -> RouteDecision:
    text = _normalized(" ".join(str(metadata.get(key, "")) for key in ("text", "filename", "subject")))
    owners = _owner_matches(text, people)
    if len(owners) != 1:
        return RouteDecision(
            status="REVIEW",
            person_id="review",
            topic=_topic(text),
            country=_country(text, metadata, None),
            reason_code="OWNER_AMBIGUOUS" if owners else "OWNER_UNKNOWN",
        )
    owner = owners[0]
    return RouteDecision(
        status="READY",
        person_id=slug(owner.display_name),
        topic=_topic(text),
        country=_country(text, metadata, owner),
    )


def slug(value: str, *, fallback: str = "unknown") -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    lowered = normalized.lower()
    parts = re.findall(r"[a-z0-9]+", lowered)
    return "-".join(parts) or fallback


def _owner_matches(text: str, people: Sequence[Person]) -> list[Person]:
    matches: list[Person] = []
    for person in people:
        full = _normalized(person.display_name)
        full_match = bool(full and full in text)
        surname_match = any(_word_present(text, surname) for surname in person.surnames)
        if full_match:
            matches.append(person)
        elif surname_match:
            matches.append(person)
    surname_only = [p for p in matches if _normalized(p.display_name) not in text]
    if len(matches) == 1 and surname_only:
        return []
    return matches


def _topic(text: str) -> str:
    for topic, keywords in TOPIC_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return topic
    return "general"


def _country(text: str, metadata: Mapping[str, object], owner: Person | None) -> str:
    explicit = str(metadata.get("country", "")).strip()
    if explicit:
        return slug(explicit)
    if "germany" in text or "deutschland" in text:
        return "de"
    if "united states" in text or "irs" in text:
        return "us"
    if owner is not None and owner.country != "unknown":
        return slug(owner.country)
    return "unknown"


def _word_present(text: str, word: str) -> bool:
    token = re.escape(_normalized(word))
    return bool(re.search(rf"(^|\W){token}(\W|$)", text))


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()

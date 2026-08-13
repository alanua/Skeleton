from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

TOPIC_ALIASES: tuple[str, ...] = (
    "01 identity_and_civil_status",
    "02 migration_and_residence",
    "03 health_and_insurance",
    "04 work_tax_and_business",
    "05 education_and_qualification",
    "06 finance_banking_and_contracts",
    "07 legal_courts_official_correspondence",
    "08 housing_and_utilities",
    "09 transport_and_travel",
)

EVENT_TYPES: tuple[str, ...] = (
    "appointment",
    "hearing",
    "deadline",
    "expiration_renewal",
    "contract_boundary",
    "employment_boundary",
    "insurance_boundary",
    "booked_travel",
    "birthday",
)

TAXONOMY_VERSION = "family-document-taxonomy-2026-08-11"
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_DATE_RE = re.compile(r"\b(20\d{2}|19\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
_TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (TOPIC_ALIASES[0], ("passport", "birth certificate", "marriage", "identity", "personalausweis")),
    (TOPIC_ALIASES[1], ("residence", "visa", "immigration", "aufenthalt", "migration")),
    (TOPIC_ALIASES[2], ("insurance", "medical", "health", "arzt", "krankenkasse")),
    (TOPIC_ALIASES[3], ("tax", "employment", "salary", "invoice", "finanzamt", "work")),
    (TOPIC_ALIASES[4], ("school", "university", "certificate", "zeugnis", "education")),
    (TOPIC_ALIASES[5], ("bank", "loan", "contract", "finance", "konto")),
    (TOPIC_ALIASES[6], ("court", "hearing", "lawyer", "legal", "gericht", "deadline")),
    (TOPIC_ALIASES[7], ("rent", "utility", "housing", "lease", "wohnung", "strom")),
    (TOPIC_ALIASES[8], ("flight", "travel", "ticket", "vehicle", "train", "reise")),
)
_EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hearing", ("hearing", "gerichtstermin", "court date")),
    ("appointment", ("appointment", "termin")),
    ("deadline", ("deadline", "due by", "frist")),
    ("expiration_renewal", ("expires", "valid until", "renewal", "ablauf")),
    ("booked_travel", ("flight", "booking", "ticket")),
    ("birthday", ("birthday", "geburtstag")),
)


@dataclass(frozen=True)
class TaxonomyDecision:
    route: str
    topic_alias: str | None
    document_date: str | None
    date_precision: str
    document_type: str | None
    issuer: str | None
    summary: str
    confidence: float
    reason_codes: tuple[str, ...]
    event_candidates: tuple[dict[str, object], ...]


def classify_text_locally(text: str, *, source_name: str = "") -> TaxonomyDecision:
    normalized = " ".join(text.split())
    lowered = normalized.lower()
    topic_hits = [
        (topic, sum(1 for keyword in keywords if keyword in lowered))
        for topic, keywords in _TOPIC_KEYWORDS
    ]
    topic_hits = [(topic, score) for topic, score in topic_hits if score > 0]
    topic_hits.sort(key=lambda item: (-item[1], item[0]))
    reason_codes: list[str] = []
    topic = topic_hits[0][0] if topic_hits else None
    ambiguous_topic = len(topic_hits) > 1 and topic_hits[0][1] == topic_hits[1][1]
    if topic is None:
        reason_codes.append("TOPIC_UNCERTAIN")
    if ambiguous_topic:
        reason_codes.append("TOPIC_AMBIGUOUS")

    dates = [match.group(0) for match in _DATE_RE.finditer(normalized)]
    valid_dates = tuple(item for item in dates if _valid_day(item))
    document_date = valid_dates[0] if valid_dates else None
    if document_date is None:
        reason_codes.append("DATE_UNCERTAIN")

    issuer = _first_labeled_value(normalized, ("Issuer", "Issued by", "Aussteller", "From"))
    if issuer is None:
        reason_codes.append("ISSUER_UNCERTAIN")
    document_type = _document_type(lowered, source_name)
    if document_type is None:
        reason_codes.append("DOCUMENT_TYPE_UNCERTAIN")

    events = _events(lowered, valid_dates)
    confidence = 0.92
    if reason_codes:
        confidence = 0.55 if topic is not None or document_date is not None else 0.25
    route = "ACCEPT" if not reason_codes else "REVIEW"
    return TaxonomyDecision(
        route=route,
        topic_alias=topic,
        document_date=document_date,
        date_precision="day" if document_date else "unknown",
        document_type=document_type,
        issuer=issuer,
        summary=_summary(normalized),
        confidence=confidence,
        reason_codes=tuple(reason_codes),
        event_candidates=events,
    )


def deterministic_document_name(parts: Mapping[str, object]) -> str:
    date_part = _clean_token(str(parts.get("document_date") or "undated"))
    subject = _clean_token(str(parts.get("principal_subject_alias") or "review"))
    topic = _clean_token(str(parts.get("topic_alias") or "uncategorized"))
    document_type = _clean_token(str(parts.get("document_type") or "document"))
    digest = _clean_token(str(parts.get("sha256", "")))[:12]
    return "-".join(item for item in (date_part, subject, topic, document_type, digest) if item) + ".json"


def public_receipt(statuses: Iterable[str]) -> dict[str, object]:
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema": "skeleton.family_document_receipt.v1",
        "privacy": "aggregate_only",
        "aggregate_counts": counts,
    }


def _clean_token(value: str) -> str:
    cleaned = _TOKEN_RE.sub("-", value.lower()).strip("-")
    return cleaned[:80] or "unknown"


def _valid_day(raw: str) -> bool:
    try:
        date.fromisoformat(raw)
        return True
    except ValueError:
        return False


def _first_labeled_value(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(rf"\b{re.escape(label)}\s*:\s*([A-Za-z0-9 ._-]{{2,80}})", text, re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split())[:80]
    return None


def _document_type(lowered: str, source_name: str) -> str | None:
    for keyword, value in (
        ("invoice", "invoice"),
        ("bescheid", "official decision"),
        ("certificate", "certificate"),
        ("contract", "contract"),
        ("notice", "notice"),
        ("letter", "letter"),
    ):
        if keyword in lowered:
            return value
    if source_name:
        stem = re.sub(r"\.[^.]+$", "", source_name)
        token = _clean_token(stem).replace("-", " ")
        return token[:80] if token else None
    return None


def _events(lowered: str, dates: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    if not dates:
        return ()
    events = []
    for event_type, keywords in _EVENT_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            events.append(
                {
                    "schema": "skeleton.family_document_event.v1",
                    "event_type": event_type,
                    "date": dates[0],
                    "title": f"Family document {event_type.replace('_', ' ')}",
                    "confidence": 0.82,
                    "evidence": "synthetic local keyword/date match",
                }
            )
    return tuple(events)


def _summary(text: str) -> str:
    if not text:
        return "No extractable text."
    return text[:360]

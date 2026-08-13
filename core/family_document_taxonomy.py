from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

TOPICS: tuple[str, ...] = (
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
SERVICE_FOLDERS: tuple[str, ...] = ("00 intake", "98 duplicates_versions", "99 review")
APPROVED_EVENT_TYPES: frozenset[str] = frozenset(
    {"appointment", "deadline", "expiration", "renewal", "hearing", "booked_travel"}
)

TOPIC_RULES: Mapping[str, tuple[str, ...]] = {
    TOPICS[0]: ("birth certificate", "marriage certificate", "standesamt", "geburtsurkunde", "heiratsurkunde"),
    TOPICS[1]: ("aufenthalt", "residence permit", "visa", "jobcenter", "ausländerbehörde", "bürgergeld"),
    TOPICS[2]: ("krankenkasse", "health insurance", "medical", "arzt", "krankenversicherung", "pflegeversicherung"),
    TOPICS[3]: ("steuer", "tax", "invoice", "gewerbe", "finanzamt", "soka-bau", "lohn"),
    TOPICS[4]: ("schule", "university", "diploma", "zeugnis", "qualification", "ausbildung"),
    TOPICS[5]: ("bank", "konto", "contract", "rechnung", "iban", "darlehen", "zahlung"),
    TOPICS[6]: ("court", "gericht", "bescheid", "legal", "widerspruch", "anwalt", "behörde"),
    TOPICS[7]: ("rent", "miete", "wohnung", "utility", "strom", "gas", "nebenkosten"),
    TOPICS[8]: ("booking", "flight", "train", "reise", "ticket", "hotel", "reservation"),
}

COUNTRY_RULES: Mapping[str, tuple[str, ...]] = {
    "DE": ("deutschland", "germany", "finanzamt", "jobcenter", "bundesrepublik", "deutsche"),
    "UA": ("ukraine", "україна", "київ", "україн"),
    "IT": ("italia", "italy", "italiano"),
    "FR": ("france", "français", "république française"),
    "CA": ("canada", "canadian"),
}

DOCUMENT_TYPE_RULES: Mapping[str, tuple[str, ...]] = {
    "invoice": ("invoice", "rechnung", "рахунок"),
    "official notice": ("bescheid", "decision notice", "mitteilung", "aufforderung"),
    "contract": ("contract", "vertrag", "договір"),
    "appointment letter": ("appointment", "termin", "einladung zum termin"),
    "travel booking": ("booking confirmed", "reservation", "buchungsbestätigung"),
    "bank statement": ("kontoauszug", "bank statement"),
    "insurance letter": ("versicherung", "krankenkasse"),
    "tax letter": ("finanzamt", "steuerbescheid", "steuererklärung"),
}

EVENT_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "appointment": ("appointment", "termin", "vorsprache"),
    "deadline": ("deadline", "frist", "spätestens", "bis zum"),
    "expiration": ("expires", "gültig bis", "ablauf", "expiration"),
    "renewal": ("renewal", "verlängerung", "erneuern"),
    "hearing": ("hearing", "anhörung", "gerichtstermin"),
    "booked_travel": ("booking confirmed", "buchungsbestätigung", "departure", "abflug"),
}

DATE_PATTERNS = (
    re.compile(r"\b(?P<day>[0-3]?\d)[./-](?P<month>[01]?\d)[./-](?P<year>20\d{2}|19\d{2})\b"),
    re.compile(r"\b(?P<year>20\d{2}|19\d{2})[./-](?P<month>[01]?\d)[./-](?P<day>[0-3]?\d)\b"),
)
MONTH_PATTERN = re.compile(r"\b(?P<year>20\d{2}|19\d{2})[./-](?P<month>[01]?\d)\b")
YEAR_PATTERN = re.compile(r"\b(20\d{2}|19\d{2})\b")
IDENTIFIER_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "reference": re.compile(r"(?i)\b(?:aktenzeichen|geschäftszeichen|reference|vorgangsnummer|kundennummer)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{3,40})"),
    "invoice": re.compile(r"(?i)\b(?:rechnungsnummer|invoice\s*(?:number|no\.?))\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,40})"),
    "contract": re.compile(r"(?i)\b(?:vertragsnummer|contract\s*(?:number|no\.?))\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,40})"),
}
AMOUNT_PATTERN = re.compile(r"(?<!\w)(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:[.,]\d{2}))\s*(€|EUR|USD|UAH)\b", re.I)
ISSUER_PATTERN = re.compile(r"(?i)^(?:issuer|from|absender|herausgeber|behörde|firma)\s*[:\-]\s*(.{2,100})$")


@dataclass(frozen=True)
class Evidence:
    value: str | None
    confidence: float
    snippets: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "confidence": self.confidence, "snippets": list(self.snippets)}


@dataclass(frozen=True)
class EventCandidate:
    event_type: str
    date: str
    confidence: float
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "date": self.date,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def score_unique(text: str, rules: Mapping[str, Iterable[str]]) -> Evidence:
    scored: list[tuple[int, str, tuple[str, ...]]] = []
    for value, words in rules.items():
        hits = tuple(word for word in words if word.casefold() in text)
        if hits:
            scored.append((len(hits), value, hits))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return Evidence(None, 0.0, ())
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return Evidence(None, 0.35, scored[0][2] + scored[1][2])
    score, value, hits = scored[0]
    confidence = min(0.98, 0.62 + score * 0.09)
    return Evidence(value, confidence, hits)


def extract_document_date(text: str) -> tuple[str | None, str | None, Evidence]:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                value = date(int(match.group("year")), int(match.group("month")), int(match.group("day"))).isoformat()
            except ValueError:
                continue
            return value, "day", Evidence(value, 0.86, (match.group(0),))
    match = MONTH_PATTERN.search(text)
    if match:
        value = f"{int(match.group('year')):04d}-{int(match.group('month')):02d}"
        return value, "month", Evidence(value, 0.70, (match.group(0),))
    match = YEAR_PATTERN.search(text)
    if match:
        value = match.group(1)
        return value, "year", Evidence(value, 0.55, (match.group(0),))
    return None, None, Evidence(None, 0.0, ())


def extract_issuer(text: str) -> Evidence:
    for line in text.splitlines()[:30]:
        match = ISSUER_PATTERN.match(line.strip())
        if match:
            value = match.group(1).strip()[:100]
            return Evidence(value, 0.88, (line.strip()[:160],))
    lines = [line.strip() for line in text.splitlines()[:8] if 3 <= len(line.strip()) <= 100]
    if lines:
        return Evidence(lines[0], 0.45, (lines[0][:160],))
    return Evidence(None, 0.0, ())


def extract_identifiers(text: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for kind, pattern in IDENTIFIER_PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            found.append({"kind": kind, "value": value, "confidence": 0.9, "evidence": match.group(0)[:180]})
    return found[:50]


def extract_amounts(text: str) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for match in AMOUNT_PATTERN.finditer(text):
        values.append(
            {
                "value": match.group(1).replace(" ", ""),
                "currency": match.group(2).upper().replace("€", "EUR"),
                "confidence": 0.85,
                "evidence": match.group(0)[:120],
            }
        )
    return values[:100]


def extract_event_candidates(text: str) -> list[EventCandidate]:
    lowered = text.casefold()
    candidates: list[EventCandidate] = []
    for event_type, keywords in EVENT_KEYWORDS.items():
        for keyword in keywords:
            start = 0
            while True:
                index = lowered.find(keyword.casefold(), start)
                if index < 0:
                    break
                window_start = max(0, index - 120)
                window_end = min(len(text), index + len(keyword) + 160)
                window = text[window_start:window_end]
                date_value = None
                evidence = None
                for pattern in DATE_PATTERNS:
                    match = pattern.search(window)
                    if match:
                        try:
                            date_value = date(
                                int(match.group("year")),
                                int(match.group("month")),
                                int(match.group("day")),
                            ).isoformat()
                        except ValueError:
                            continue
                        evidence = window.strip()[:240]
                        break
                if date_value is not None:
                    candidates.append(EventCandidate(event_type, date_value, 0.82, evidence or keyword))
                start = index + len(keyword)
    unique: dict[tuple[str, str], EventCandidate] = {}
    for candidate in candidates:
        key = (candidate.event_type, candidate.date)
        if key not in unique or candidate.confidence > unique[key].confidence:
            unique[key] = candidate
    return sorted(unique.values(), key=lambda item: (item.date, item.event_type))

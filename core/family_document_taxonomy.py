from __future__ import annotations

from datetime import datetime
import re
from typing import Final, Mapping, Sequence
from zoneinfo import ZoneInfo


FAMILY_DOCUMENT_TOPICS: Final[tuple[str, ...]] = (
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

FAMILY_DOCUMENT_NAMESPACE: Final = "family_document"

_TOPIC_KEYWORDS: Final[Mapping[str, tuple[str, ...]]] = {
    FAMILY_DOCUMENT_TOPICS[0]: ("geburtsurkunde", "heiratsurkunde", "standesamt", "birth certificate", "паспорт", "свідоцтво"),
    FAMILY_DOCUMENT_TOPICS[1]: ("aufenthalt", "ausländerbehörde", "residence permit", "jobcenter", "bürgergeld", "visa", "внж"),
    FAMILY_DOCUMENT_TOPICS[2]: ("krankenkasse", "techniker krankenkasse", "versicherung", "arzt", "hospital", "medical", "страхув"),
    FAMILY_DOCUMENT_TOPICS[3]: ("finanzamt", "steuer", "einkommen", "gewerbe", "arbeitgeber", "lohn", "rechnung", "invoice", "подат"),
    FAMILY_DOCUMENT_TOPICS[4]: ("schule", "universität", "zeugnis", "diplom", "qualification", "ausbildung", "освіт"),
    FAMILY_DOCUMENT_TOPICS[5]: ("bank", "konto", "iban", "darlehen", "kredit", "vertrag", "payment", "рахунок"),
    FAMILY_DOCUMENT_TOPICS[6]: ("gericht", "anwalt", "bescheid", "widerspruch", "mahnung", "behörde", "legal", "суд"),
    FAMILY_DOCUMENT_TOPICS[7]: ("miete", "vermieter", "wohnung", "strom", "gas", "wasser", "nebenkosten", "житл"),
    FAMILY_DOCUMENT_TOPICS[8]: ("bahn", "flug", "ticket", "reise", "booking", "hotel", "fahrzeug", "travel", "подорож"),
}

_DOCUMENT_TYPES: Final[Mapping[str, tuple[str, ...]]] = {
    "Bescheid": ("bescheid", "entscheidung", "decision"),
    "Rechnung": ("rechnung", "invoice", "zahlungsbetrag", "gesamtbetrag"),
    "Vertrag": ("vertrag", "contract", "vereinbarung"),
    "Mahnung": ("mahnung", "zahlungserinnerung", "reminder"),
    "Kontoauszug": ("kontoauszug", "account statement"),
    "Versicherungsdokument": ("versicherung", "versicherungsnummer", "policy"),
    "Termin/Einladung": ("termin", "einladung", "appointment", "invitation"),
    "Brief": ("sehr geehrte", "dear ", "mit freundlichen grüßen"),
}

_ISSUERS: Final[tuple[str, ...]] = (
    "Jobcenter",
    "Finanzamt",
    "Techniker Krankenkasse",
    "TK",
    "Agentur für Arbeit",
    "Ausländerbehörde",
    "Deutsche Rentenversicherung",
    "Deutsche Bahn",
)

_DATE_RE: Final = re.compile(r"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])[.](?P<month>0?[1-9]|1[0-2])[.](?P<year>20\d{2})(?!\d)")
_TIME_RE: Final = re.compile(r"(?<!\d)(?P<hour>[01]?\d|2[0-3])[:.](?P<minute>[0-5]\d)(?!\d)")


def _score(text: str, keywords: Sequence[str]) -> int:
    return sum(1 for keyword in keywords if keyword.casefold() in text)


def _unique_best(text: str, rules: Mapping[str, Sequence[str]]) -> tuple[str | None, int]:
    ranked = sorted(((name, _score(text, keywords)) for name, keywords in rules.items()), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][1] <= 0:
        return None, 0
    if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
        return None, ranked[0][1]
    return ranked[0]


def _summary(raw_text: str) -> str:
    compact = re.sub(r"\s+", " ", raw_text).strip()
    if not compact:
        return "Зміст не розпізнано."
    sentences = re.split(r"(?<=[.!?])\s+", compact)
    summary = " ".join(sentences[:2]).strip()
    return summary[:600] or compact[:600]


def _calendar_candidates(raw_text: str, document_type: str | None, issuer: str | None) -> list[dict[str, object]]:
    if document_type != "Termin/Einladung":
        return []
    dates = list(_DATE_RE.finditer(raw_text))
    if len(dates) != 1:
        return []
    date = dates[0]
    local_tail = raw_text[date.end(): date.end() + 80]
    times = list(_TIME_RE.finditer(local_tail))
    if len(times) != 1:
        return []
    time_match = times[0]
    try:
        when = datetime(
            int(date.group("year")),
            int(date.group("month")),
            int(date.group("day")),
            int(time_match.group("hour")),
            int(time_match.group("minute")),
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
    except ValueError:
        return []
    title = "Termin"
    if issuer:
        title = f"Termin — {issuer}"
    return [
        {
            "event_type": "appointment",
            "title": title,
            "start_at": int(when.timestamp()),
            "end_at": None,
            "timezone": "Europe/Berlin",
        }
    ]


def classify_family_document_text(text: str, subject_aliases: Sequence[str]) -> dict[str, object]:
    """Conservative local classifier. Ambiguity is REVIEW, never guessed ACCEPT."""
    normalized = re.sub(r"\s+", " ", text).casefold()
    aliases = tuple(alias.strip() for alias in subject_aliases if alias.strip())
    if len(aliases) != 3 or len(set(aliases)) != 3:
        raise ValueError("exact three subject aliases required")

    matched_aliases = [alias for alias in aliases if alias.casefold() in normalized]
    principal = matched_aliases[0] if len(matched_aliases) == 1 else None
    topic, topic_score = _unique_best(normalized, _TOPIC_KEYWORDS)
    document_type, type_score = _unique_best(normalized, _DOCUMENT_TYPES)
    issuer_matches = [issuer for issuer in _ISSUERS if issuer.casefold() in normalized]
    issuer = issuer_matches[0] if len(issuer_matches) == 1 else None
    event_candidates = _calendar_candidates(text, document_type, issuer)

    reason_codes: list[str] = []
    if principal is None:
        reason_codes.append("OWNER_AMBIGUOUS")
    if topic is None:
        reason_codes.append("TOPIC_AMBIGUOUS")
    if document_type is None:
        reason_codes.append("DOCUMENT_TYPE_AMBIGUOUS")
    if issuer is None:
        reason_codes.append("ISSUER_AMBIGUOUS")
    if document_type == "Termin/Einladung" and not event_candidates:
        reason_codes.append("CALENDAR_EVENT_AMBIGUOUS")

    confidence = min(
        0.99,
        0.40
        + (0.20 if principal else 0.0)
        + min(topic_score, 2) * 0.08
        + min(type_score, 2) * 0.08
        + (0.12 if issuer else 0.0),
    )
    route = "ACCEPT" if not reason_codes and confidence >= 0.80 else "REVIEW"
    return {
        "route": route,
        "principal_subject_alias": principal,
        "linked_subject_aliases": matched_aliases,
        "topic_alias": topic,
        "document_type": document_type,
        "issuer": issuer,
        "summary": _summary(text),
        "confidence": round(confidence, 2),
        "reason_codes": reason_codes,
        "event_candidates": event_candidates,
    }

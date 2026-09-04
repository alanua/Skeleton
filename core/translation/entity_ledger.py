from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping, Sequence


class EntityClass(StrEnum):
    AMOUNT = "amount"
    DATE = "date"
    CASE_REFERENCE = "case_reference"
    IBAN_ACCOUNT = "iban_account"
    MEDICAL_CODE = "medical_code"
    NAME = "name"
    ADDRESS = "address"


@dataclass(frozen=True, order=True)
class EntitySpan:
    start: int
    end: int
    entity_class: EntityClass
    text: str
    detector: str


@dataclass(frozen=True)
class EntityLedger:
    text_length: int
    entities: tuple[EntitySpan, ...]
    shadow_only: bool = True

    def by_class(self, entity_class: EntityClass) -> tuple[EntitySpan, ...]:
        return tuple(x for x in self.entities if x.entity_class is entity_class)


@dataclass(frozen=True)
class RecallClassResult:
    entity_class: EntityClass
    expected: int
    matched: int
    missed: int
    recall: float | None
    missed_texts: tuple[str, ...]


@dataclass(frozen=True)
class EntityRecallResult:
    classes: tuple[RecallClassResult, ...]
    all_expected_found: bool
    shadow_only: bool = True


# Deliberately conservative deterministic detectors. This is extraction evidence,
# not a production-ready NER system. Misses must be exposed by ENTITY_RECALL_GATE.
_AMOUNT_RE = re.compile(
    r"(?<!\w)(?:€\s*)?(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,\d{2})\s*(?:€|EUR|Euro)(?!\w)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[.\-/](?:0?[1-9]|1[0-2])[.\-/](?:19|20)\d{2}(?!\d)"
)
_IBAN_RE = re.compile(r"(?<![A-Z0-9])(?:DE\s?\d{2}(?:\s?\d{4}){4}\s?\d{2})(?![A-Z0-9])", re.IGNORECASE)
# Common German court/authority references, intentionally broad but label-aware.
_CASE_LABEL_RE = re.compile(
    r"(?im)\b(?:Aktenzeichen|Az\.?|Geschäftszeichen|Kassenzeichen|Vorgangsnummer|Referenz(?:nummer)?|BG[-\s]?Nr\.?|Kundennummer)\s*[:#]?\s*([A-Z0-9ÄÖÜ./\- ]{3,40})"
)
# ICD-10(-GM)-style codes such as A00.1, M54.5, Z76.3; label-aware fallback for other codes.
_ICD_RE = re.compile(r"(?<![A-Z0-9])(?:[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,2})?)(?![A-Z0-9])")
_MED_LABEL_RE = re.compile(r"(?im)\b(?:ICD(?:-10(?:-GM)?)?|Diagnosecode|OPS(?:-Code)?)\s*[:#]?\s*([A-Z0-9.\-]{3,16})")
# German postal address approximation: street name + suffix + house number. No city-only guessing.
_ADDRESS_RE = re.compile(
    r"(?<!\w)([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.'\- ]{2,50}(?:straße|strasse|str\.|weg|allee|platz|ring|gasse|ufer|damm)\s+\d{1,4}[a-zA-Z]?(?:\s*[-/]\s*\d{1,4}[a-zA-Z]?)?)",
    re.IGNORECASE,
)
# Names are only extracted from explicit labels in deterministic shadow mode.
_NAME_LABEL_RE = re.compile(
    r"(?im)\b(?:Name|Versicherte[rn]?|Patient(?:in)?|Empfänger(?:in)?|Antragsteller(?:in)?|Kunde(?:in)?)\s*:\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'\-]+(?:[ \t]+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'\-]+){1,4})"
)


def _add(matches: list[EntitySpan], text: str, start: int, end: int, cls: EntityClass, detector: str) -> None:
    value = text[start:end].strip()
    if not value:
        return
    # Trim leading/trailing spaces while preserving exact source span.
    left = len(text[start:end]) - len(text[start:end].lstrip())
    right = len(text[start:end].rstrip())
    start2 = start + left
    end2 = start + right
    if start2 >= end2:
        return
    matches.append(EntitySpan(start2, end2, cls, text[start2:end2], detector))


def _label_group_matches(pattern: re.Pattern[str], text: str, cls: EntityClass, detector: str) -> list[EntitySpan]:
    out: list[EntitySpan] = []
    for m in pattern.finditer(text):
        _add(out, text, m.start(1), m.end(1), cls, detector)
    return out


def _dedupe_non_overlapping(matches: Iterable[EntitySpan]) -> tuple[EntitySpan, ...]:
    # Prefer longer spans for same class/start, then deterministic class/name ordering.
    ordered = sorted(matches, key=lambda x: (x.start, -(x.end - x.start), x.entity_class.value, x.detector))
    kept: list[EntitySpan] = []
    for item in ordered:
        duplicate = next((k for k in kept if k.entity_class is item.entity_class and k.start == item.start and k.end == item.end), None)
        if duplicate:
            continue
        kept.append(item)
    return tuple(sorted(kept, key=lambda x: (x.start, x.end, x.entity_class.value)))


def extract_entities(text: str) -> EntityLedger:
    """Extract critical entities in shadow mode without changing source text.

    This intentionally favors auditable deterministic extraction over pretending to
    have production NER recall. ENTITY_RECALL_GATE is the mechanism that measures
    what this extractor misses before locking is trusted.
    """
    text = text or ""
    matches: list[EntitySpan] = []
    for pattern, cls, detector in (
        (_AMOUNT_RE, EntityClass.AMOUNT, "amount_regex_v1"),
        (_DATE_RE, EntityClass.DATE, "date_regex_v1"),
        (_IBAN_RE, EntityClass.IBAN_ACCOUNT, "iban_regex_v1"),
        (_ICD_RE, EntityClass.MEDICAL_CODE, "icd10_regex_v1"),
        (_ADDRESS_RE, EntityClass.ADDRESS, "address_regex_v1"),
    ):
        for m in pattern.finditer(text):
            _add(matches, text, m.start(), m.end(), cls, detector)
    matches.extend(_label_group_matches(_CASE_LABEL_RE, text, EntityClass.CASE_REFERENCE, "case_label_regex_v1"))
    matches.extend(_label_group_matches(_MED_LABEL_RE, text, EntityClass.MEDICAL_CODE, "medical_label_regex_v1"))
    matches.extend(_label_group_matches(_NAME_LABEL_RE, text, EntityClass.NAME, "name_label_regex_v1"))
    return EntityLedger(text_length=len(text), entities=_dedupe_non_overlapping(matches))


def evaluate_entity_recall(
    ledger: EntityLedger,
    expected: Mapping[EntityClass | str, Sequence[str]],
) -> EntityRecallResult:
    """Measure exact-source extraction recall per critical class.

    `expected` is human/golden annotation. This function does not estimate recall
    from the extractor itself and therefore cannot silently turn precision into a
    fake recall metric.
    """
    rows: list[RecallClassResult] = []
    all_found = True
    for raw_cls, values in expected.items():
        cls = raw_cls if isinstance(raw_cls, EntityClass) else EntityClass(raw_cls)
        expected_values = tuple(values)
        detected = [e.text for e in ledger.by_class(cls)]
        unmatched = list(detected)
        missed: list[str] = []
        matched = 0
        for value in expected_values:
            try:
                idx = unmatched.index(value)
            except ValueError:
                missed.append(value)
            else:
                matched += 1
                unmatched.pop(idx)
        total = len(expected_values)
        recall = matched / total if total else None
        if missed:
            all_found = False
        rows.append(RecallClassResult(cls, total, matched, len(missed), recall, tuple(missed)))
    return EntityRecallResult(tuple(sorted(rows, key=lambda x: x.entity_class.value)), all_found)

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from core.translation.entity_ledger import EntityClass


CRITICAL_ENTITY_CLASSES: tuple[EntityClass, ...] = (
    EntityClass.AMOUNT,
    EntityClass.DATE,
    EntityClass.CASE_REFERENCE,
    EntityClass.IBAN_ACCOUNT,
    EntityClass.MEDICAL_CODE,
    EntityClass.NAME,
    EntityClass.ADDRESS,
)


class AdjudicationStatus(StrEnum):
    CANDIDATE_UNADJUDICATED = "CANDIDATE_UNADJUDICATED"
    MODEL_ASSISTED_DRAFT = "MODEL_ASSISTED_DRAFT"
    HUMAN_ADJUDICATED = "HUMAN_ADJUDICATED"


@dataclass(frozen=True)
class EntityRecallFixture:
    fixture_id: str
    entity_class: EntityClass
    source_sha256: str
    expected_text: str
    adjudication: AdjudicationStatus
    positive: bool = True
    archive_derived: bool = True


@dataclass(frozen=True)
class GoldSetReadiness:
    ready: bool
    minimum_positive_per_class: int
    human_positive_counts: dict[EntityClass, int]
    failures: tuple[str, ...]


def validate_gold_set(
    fixtures: Iterable[EntityRecallFixture],
    *,
    minimum_positive_per_class: int = 12,
) -> GoldSetReadiness:
    """Fail closed unless every critical class has enough human-adjudicated positives.

    Candidate/model-assisted labels never count as gold. Negative fixtures are
    regression evidence but never satisfy the positive recall denominator.
    """
    if minimum_positive_per_class < 1:
        raise ValueError("minimum_positive_per_class must be >= 1")

    counts: Counter[EntityClass] = Counter()
    failures: list[str] = []
    for item in fixtures:
        if not item.fixture_id or not item.source_sha256 or not item.expected_text:
            failures.append("fixture_missing_required_provenance")
            continue
        if not item.archive_derived:
            failures.append(f"non_archive_fixture:{item.fixture_id}")
        if item.positive and item.adjudication is AdjudicationStatus.HUMAN_ADJUDICATED:
            counts[item.entity_class] += 1

    for entity_class in CRITICAL_ENTITY_CLASSES:
        n = counts[entity_class]
        if n < minimum_positive_per_class:
            failures.append(
                f"insufficient_human_positive_examples:{entity_class}:{n}/{minimum_positive_per_class}"
            )

    return GoldSetReadiness(
        ready=not failures,
        minimum_positive_per_class=minimum_positive_per_class,
        human_positive_counts={entity_class: counts[entity_class] for entity_class in CRITICAL_ENTITY_CLASSES},
        failures=tuple(failures),
    )

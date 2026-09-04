from __future__ import annotations

from core.translation.entity_ledger import EntityClass
from core.translation.entity_recall_dataset import (
    AdjudicationStatus,
    CRITICAL_ENTITY_CLASSES,
    EntityRecallFixture,
    validate_gold_set,
)


def _fixture(cls: EntityClass, index: int, *, adjudication=AdjudicationStatus.HUMAN_ADJUDICATED, positive=True, archive_derived=True, source_quality_requires_review=False):
    return EntityRecallFixture(
        fixture_id=f"{cls}-{index}",
        entity_class=cls,
        source_sha256=f"sha-{cls}-{index}",
        expected_text=f"expected-{cls}-{index}",
        adjudication=adjudication,
        positive=positive,
        archive_derived=archive_derived,
        source_quality_requires_review=source_quality_requires_review,
    )


def test_readiness_requires_minimum_positive_examples_per_class() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        fixtures.extend(_fixture(cls,i) for i in range(12))
    assert validate_gold_set(fixtures).ready is True


def test_eleven_examples_do_not_pass_twelve_example_gate() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        n=11 if cls is EntityClass.CASE_REFERENCE else 12
        fixtures.extend(_fixture(cls,i) for i in range(n))
    result=validate_gold_set(fixtures)
    assert result.ready is False
    assert "insufficient_human_positive_examples:case_reference:11/12" in result.failures


def test_model_assisted_drafts_never_count_as_gold() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        fixtures.extend(_fixture(cls,i) for i in range(12))
    fixtures=[
        _fixture(EntityClass.MEDICAL_CODE,i,adjudication=AdjudicationStatus.MODEL_ASSISTED_DRAFT)
        if f.entity_class is EntityClass.MEDICAL_CODE else f
        for i,f in enumerate(fixtures)
    ]
    result=validate_gold_set(fixtures)
    assert result.ready is False
    assert any(x.startswith("insufficient_human_positive_examples:medical_code:") for x in result.failures)


def test_unadjudicated_candidates_never_count_as_gold() -> None:
    fixtures=[_fixture(cls,i,adjudication=AdjudicationStatus.CANDIDATE_UNADJUDICATED) for cls in CRITICAL_ENTITY_CLASSES for i in range(20)]
    result=validate_gold_set(fixtures)
    assert result.ready is False
    assert all(result.human_positive_counts[c] == 0 for c in CRITICAL_ENTITY_CLASSES)


def test_negative_regressions_do_not_satisfy_positive_quota() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        fixtures.extend(_fixture(cls,i) for i in range(11))
        fixtures.extend(_fixture(cls,100+i,positive=False) for i in range(30))
    result=validate_gold_set(fixtures)
    assert result.ready is False
    assert all(result.human_positive_counts[c] == 11 for c in CRITICAL_ENTITY_CLASSES)


def test_non_archive_fixture_fails_readiness_even_when_counts_are_full() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        fixtures.extend(_fixture(cls,i) for i in range(12))
    fixtures.append(_fixture(EntityClass.DATE,999,archive_derived=False))
    result=validate_gold_set(fixtures)
    assert result.ready is False
    assert "non_archive_fixture:date-999" in result.failures


def test_quality_review_examples_are_reported_separately() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        fixtures.extend(_fixture(cls,i,source_quality_requires_review=(i<3)) for i in range(12))
    result=validate_gold_set(fixtures)
    assert result.ready is True
    assert all(result.quality_review_positive_counts[c] == 3 for c in CRITICAL_ENTITY_CLASSES)
    assert all(result.clean_or_canonical_positive_counts[c] == 9 for c in CRITICAL_ENTITY_CLASSES)


def test_quality_review_majority_is_explicit_warning_not_silent_mix() -> None:
    fixtures=[]
    for cls in CRITICAL_ENTITY_CLASSES:
        fixtures.extend(_fixture(cls,i,source_quality_requires_review=(i<7)) for i in range(12))
    result=validate_gold_set(fixtures)
    assert result.ready is True
    assert any(x.startswith('quality_review_majority:medical_code:7/12') for x in result.warnings)

from __future__ import annotations

from core.evidence_authenticity import ReviewerReceipt, authentic_review_receipt


def test_exact_head_movement_invalidates_review_receipt() -> None:
    receipt = ReviewerReceipt(
        reviewer_identity="reviewer-b",
        head_sha="a" * 40,
        bounded_findings=("bounded",),
        issued_at="2026-08-22T00:00:00+00:00",
    )

    assert not authentic_review_receipt(
        author_identity="author-a",
        reviewer=receipt,
        current_head_sha="b" * 40,
    )


def test_same_author_identity_cannot_satisfy_independent_review() -> None:
    receipt = ReviewerReceipt(
        reviewer_identity="author-a",
        head_sha="a" * 40,
        bounded_findings=("bounded",),
        issued_at="2026-08-22T00:00:00+00:00",
    )

    assert not authentic_review_receipt(
        author_identity="author-a",
        reviewer=receipt,
        current_head_sha="a" * 40,
    )

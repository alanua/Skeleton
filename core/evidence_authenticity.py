from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExactHeadReceipt:
    kind: str
    head_sha: str
    author_identity: str
    issued_at: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewerReceipt:
    reviewer_identity: str
    head_sha: str
    bounded_findings: tuple[str, ...]
    issued_at: str


def receipt_matches_head(receipt: ExactHeadReceipt | ReviewerReceipt, current_head_sha: str) -> bool:
    return bool(receipt.head_sha) and receipt.head_sha.lower() == current_head_sha.lower()


def reviewer_is_independent(author_identity: str, reviewer: ReviewerReceipt) -> bool:
    return bool(reviewer.reviewer_identity) and reviewer.reviewer_identity != author_identity


def authentic_review_receipt(
    *,
    author_identity: str,
    reviewer: ReviewerReceipt,
    current_head_sha: str,
) -> bool:
    return receipt_matches_head(reviewer, current_head_sha) and reviewer_is_independent(author_identity, reviewer)

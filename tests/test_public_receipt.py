from __future__ import annotations

import json

import pytest

from core.public_receipt import (
    PublicField,
    PublicReceiptBoundary,
    PublicReceiptError,
    private_summary,
    sanitize_public_receipt,
)


PUBLIC_FIELDS = (
    PublicField.at("schema", kind="opaque_id"),
    PublicField.at("status", kind="status"),
    PublicField.at("receipt_id", kind="opaque_id"),
    PublicField.at("counts", kind="aggregate"),
    PublicField.at("hashes.content_hash", kind="hash"),
    PublicField.at("items.*.public_id", kind="opaque_id"),
    PublicField.at("items.*.status", kind="status"),
)


def test_nested_secret_sentinel_fails_closed() -> None:
    receipt = {
        "schema": "skeleton.public_receipt.v1",
        "status": "DONE",
        "receipt_id": "receipt-alpha",
        "counts": {"processed": 1},
        "hashes": {"content_hash": "a" * 64},
        "items": [{"public_id": "item-1", "status": "DONE"}],
        "nested": {"credential": {"api_key": "sk_synthetic_secret_value_123456789"}},
    }

    with pytest.raises(PublicReceiptError, match="private-marked"):
        sanitize_public_receipt(receipt, PUBLIC_FIELDS)


def test_private_path_and_provider_identifier_sentinels_do_not_survive() -> None:
    private_path = {
        "schema": "skeleton.public_receipt.v1",
        "status": "DONE",
        "receipt_id": "receipt-alpha",
        "counts": {"processed": 1},
        "hashes": {"content_hash": "a" * 64},
        "items": [{"public_id": "item-1", "status": "DONE"}],
        "evidence_path": "/home/agent/private/provider.json",
    }
    provider_id = {
        "schema": "skeleton.public_receipt.v1",
        "status": "DONE",
        "receipt_id": "receipt-alpha",
        "counts": {"processed": 1},
        "hashes": {"content_hash": "a" * 64},
        "items": [{"public_id": "item-1", "status": "DONE"}],
        "provider_id": "acct_live_private_123456",
    }

    with pytest.raises(PublicReceiptError, match="private path"):
        sanitize_public_receipt(private_path, PUBLIC_FIELDS)
    with pytest.raises(PublicReceiptError, match="private-marked"):
        sanitize_public_receipt(provider_id, PUBLIC_FIELDS)


def test_allowed_aggregate_hash_status_and_opaque_ids_render_deterministically() -> None:
    receipt = {
        "items": [
            {"status": "DONE", "public_id": "pub-item-2", "ignored_public_note": "not allowlisted"},
            {"status": "SKIPPED", "public_id": "pub-item-1"},
        ],
        "hashes": {"content_hash": "b" * 64, "ignored_hash": "c" * 64},
        "counts": {"skipped": 1, "processed": 2},
        "receipt_id": "receipt-alpha",
        "status": "DONE",
        "schema": "skeleton.public_receipt.v1",
    }

    rendered = sanitize_public_receipt(receipt, PUBLIC_FIELDS)

    assert rendered == {
        "counts": {"processed": 2, "skipped": 1},
        "hashes": {"content_hash": "b" * 64},
        "items": [
            {"public_id": "pub-item-2", "status": "DONE"},
            {"public_id": "pub-item-1", "status": "SKIPPED"},
        ],
        "receipt_id": "receipt-alpha",
        "schema": "skeleton.public_receipt.v1",
        "status": "DONE",
    }
    assert json.dumps(rendered, sort_keys=True) == json.dumps(
        sanitize_public_receipt(receipt, PUBLIC_FIELDS),
        sort_keys=True,
    )


def test_sanitizer_is_idempotent_when_run_twice() -> None:
    boundary = PublicReceiptBoundary(fields=PUBLIC_FIELDS)
    receipt = {
        "schema": "skeleton.public_receipt.v1",
        "status": "DONE",
        "receipt_id": "receipt-beta",
        "counts": {"processed": 3},
        "hashes": {"content_hash": "d" * 64},
        "items": [{"public_id": "pub-item-3", "status": "DONE"}],
    }

    once = boundary.sanitize(receipt)
    twice = boundary.sanitize(once)

    assert twice == once


def test_private_summary_is_bounded_class_and_count_only() -> None:
    summary = private_summary(
        [
            {"path": "/home/agent/private/a.json"},
            {"path": "/home/agent/private/b.json"},
        ],
        redacted_class="private_evidence_ref",
    )

    assert summary == {"redacted_class": "private_evidence_ref", "item_count": 2}

from __future__ import annotations

from core.family_document_reconciliation import build_reconciliation_packet, verify_packet_hash


def _entries():
    return [
        {
            "source_id": "private-source-b",
            "source_sha256": "b" * 64,
            "disposition": "REVIEW",
            "reason_code": "OWNER_AMBIGUOUS",
            "planned_storage_ref": "private/location/b",
            "owner_alias": "Private Person",
        },
        {
            "source_id": "private-source-a",
            "source_sha256": "a" * 64,
            "disposition": "IMPORT",
            "reason_code": "READY",
            "planned_record_id": "doc-a",
            "planned_storage_ref": "private/location/a",
            "topic_alias": "private-topic",
            "document_year": 2026,
        },
    ]


def test_reconciliation_packet_is_deterministic_and_zero_side_effect() -> None:
    first = build_reconciliation_packet(_entries())
    second = build_reconciliation_packet(list(reversed(_entries())))

    assert first.packet_hash == second.packet_hash
    assert verify_packet_hash(first.private_packet, first.packet_hash)
    assert first.public_receipt["side_effects"] == 0
    assert first.public_receipt["approval_ready"] is True
    assert first.public_receipt["total_count"] == 2
    assert first.public_receipt["import_count"] == 1
    assert first.public_receipt["review_count"] == 1


def test_public_receipt_contains_no_private_inventory_values() -> None:
    packet = build_reconciliation_packet(_entries())
    rendered = __import__("json").dumps(packet.public_receipt, sort_keys=True)

    assert "private-source" not in rendered
    assert "private/location" not in rendered
    assert "Private Person" not in rendered
    assert "private-topic" not in rendered
    assert packet.packet_hash in rendered

from __future__ import annotations

import json

from core.evidence_packet import EvidencePacket


def test_receipt_contains_aggregate_metadata_only() -> None:
    packet = EvidencePacket(
        task_id="task-001",
        envelope_hash="a" * 64,
        status="DONE",
        executor_class="network.http",
        risk_class="yellow",
        privacy_class="private",
        step_results=(
            {
                "endpoint": "device-status-endpoint",
                "response": {"state": "enabled"},
                "note": "local payload value",
            },
        ),
        assertions=({"kind": "json_path_eq", "passed": True},),
    )

    full = packet.full_payload()
    receipt = packet.public_receipt()
    serialized = json.dumps(receipt, sort_keys=True)

    assert full["step_results"][0]["response"] == {"state": "enabled"}
    assert receipt["status"] == "DONE"
    assert receipt["step_count"] == 1
    assert receipt["assertion_count"] == 1
    assert "device-status-endpoint" not in serialized
    assert "local payload value" not in serialized
    assert "response" not in serialized
    assert len(receipt["evidence_hash"]) == 64

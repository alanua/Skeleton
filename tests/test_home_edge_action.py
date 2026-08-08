from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.home_edge.action import (
    FIXED_BASELINE_PROFILE,
    FIXED_RELAY_NODE_ID,
    FIXED_TARGET_ID,
    REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
    REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
    HomeEdgeActionError,
    remote_read_only_diagnostic,
)


def test_remote_read_only_diagnostic_uses_fixed_de_pc_home_edge_boundary() -> None:
    seen: list[Mapping[str, Any]] = []

    def home_edge_execute(request: Mapping[str, Any]) -> Mapping[str, Any]:
        seen.append(request)
        return {
            "status": "completed",
            "target_id": FIXED_TARGET_ID,
            "baseline_profile": FIXED_BASELINE_PROFILE,
            "aggregate": {
                "classes": {"os": "windows"},
                "counts": {"checks": 3},
                "booleans": {"defender_present": True},
            },
            "reason_codes": ["windows_baseline_completed"],
        }

    receipt = remote_read_only_diagnostic(
        {
            "schema": REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
            "maintenance_task_id": REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
            "idempotency_key": "test-de-pc-baseline",
        },
        home_edge_execute=home_edge_execute,
    )

    assert receipt["relay_node_id"] == FIXED_RELAY_NODE_ID
    assert receipt["target_id"] == FIXED_TARGET_ID
    assert receipt["baseline_profile"] == FIXED_BASELINE_PROFILE
    assert receipt["baseline_completed"] is True
    assert receipt["classes"] == {"os": "windows"}
    assert receipt["counts"] == {"checks": 3}
    assert receipt["booleans"] == {"defender_present": True}
    assert len(seen) == 1
    assert seen[0]["node_id"] == FIXED_RELAY_NODE_ID
    assert "host" not in seen[0]
    assert "command" not in seen[0]
    assert "powershell" not in str(seen[0]).lower()


def test_transport_only_home_edge_receipt_is_not_de_pc_baseline() -> None:
    def home_edge_execute(_request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "ok",
            "aggregate": {"classes": {}, "counts": {}, "booleans": {}},
            "reason_codes": ["home_edge_transport_ok"],
        }

    with pytest.raises(HomeEdgeActionError) as excinfo:
        remote_read_only_diagnostic(
            {
                "schema": REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
                "maintenance_task_id": REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
                "idempotency_key": "transport-only",
            },
            home_edge_execute=home_edge_execute,
        )

    assert excinfo.value.reason_code == "DE_PC_BASELINE_NOT_COMPLETED"


def test_remote_read_only_diagnostic_rejects_arbitrary_payload_fields() -> None:
    with pytest.raises(HomeEdgeActionError) as excinfo:
        remote_read_only_diagnostic(
            {
                "schema": REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
                "maintenance_task_id": REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
                "idempotency_key": "bad",
                "host": "example.invalid",
            },
            home_edge_execute=lambda _request: {},
        )

    assert excinfo.value.reason_code == "UNKNOWN_REMOTE_DIAGNOSTIC_FIELD"

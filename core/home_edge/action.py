from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any, Final

from core.home_edge.executor import (
    DEFAULT_NODE_ID,
    EXEC_REQUEST_SCHEMA,
    ExecutionLane,
    ExecutionUser,
)


REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA: Final = (
    "skeleton.home_edge.remote_read_only_diagnostic_request.v1"
)
REMOTE_READ_ONLY_DIAGNOSTIC_RECEIPT_SCHEMA: Final = (
    "skeleton.home_edge.remote_read_only_diagnostic_receipt.v1"
)
REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID: Final = "remote_read_only_diagnostic"
FIXED_RELAY_NODE_ID: Final = DEFAULT_NODE_ID
FIXED_TARGET_ID: Final = "DE-PC"
FIXED_BASELINE_PROFILE: Final = "windows_read_only_baseline_v1"
FIXED_BASELINE_ACTION: Final = "de_pc_windows_read_only_baseline"

_ALLOWED_REQUEST_KEYS: Final = frozenset(
    {"schema", "maintenance_task_id", "idempotency_key"}
)


class HomeEdgeActionError(ValueError):
    """Raised when a Home Edge action request is outside the reviewed adapter."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RemoteReadOnlyDiagnosticRequest:
    schema: str
    maintenance_task_id: str
    idempotency_key: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RemoteReadOnlyDiagnosticRequest":
        if not isinstance(value, Mapping):
            raise HomeEdgeActionError(
                "INVALID_REMOTE_DIAGNOSTIC_REQUEST",
                "remote diagnostic request must be an object",
            )
        unknown = sorted(set(value) - _ALLOWED_REQUEST_KEYS)
        if unknown:
            raise HomeEdgeActionError(
                "UNKNOWN_REMOTE_DIAGNOSTIC_FIELD",
                f"unknown remote diagnostic field: {unknown[0]}",
            )
        if value.get("schema") != REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA:
            raise HomeEdgeActionError(
                "INVALID_REMOTE_DIAGNOSTIC_SCHEMA",
                "remote diagnostic request schema is invalid",
            )
        if value.get("maintenance_task_id") != REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID:
            raise HomeEdgeActionError(
                "INVALID_REMOTE_DIAGNOSTIC_TASK",
                "remote diagnostic task id is invalid",
            )
        idempotency_key = value.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise HomeEdgeActionError(
                "MISSING_REMOTE_DIAGNOSTIC_IDEMPOTENCY",
                "remote diagnostic idempotency key is required",
            )
        return cls(
            schema=REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
            maintenance_task_id=REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
            idempotency_key=idempotency_key,
        )


def build_de_pc_windows_baseline_home_edge_request(
    request: RemoteReadOnlyDiagnosticRequest,
) -> dict[str, Any]:
    baseline_payload = {
        "schema": "skeleton.home_edge.de_pc_windows_baseline.v1",
        "action": FIXED_BASELINE_ACTION,
        "target_id": FIXED_TARGET_ID,
        "baseline_profile": FIXED_BASELINE_PROFILE,
    }
    return {
        "schema": EXEC_REQUEST_SCHEMA,
        "request_id": f"{REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID}:{request.idempotency_key}",
        "node_id": FIXED_RELAY_NODE_ID,
        "execution_lane": ExecutionLane.READ_ONLY.value,
        "run_as": ExecutionUser.DESKTOP_USER.value,
        "mode": "argv",
        "argv": ["python3", "-m", "core.home_edge.executor_gateway", "--server"],
        "stdin_text": json.dumps(baseline_payload, sort_keys=True, separators=(",", ":")),
        "timeout_seconds": 120,
        "idempotency_key": request.idempotency_key,
        "public": True,
    }


def remote_read_only_diagnostic(
    request_mapping: Mapping[str, Any],
    *,
    home_edge_execute: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    request = RemoteReadOnlyDiagnosticRequest.from_mapping(request_mapping)
    home_edge_request = build_de_pc_windows_baseline_home_edge_request(request)
    result = home_edge_execute(home_edge_request)
    if not isinstance(result, Mapping):
        raise HomeEdgeActionError(
            "INVALID_HOME_EDGE_BASELINE_RECEIPT",
            "Home Edge baseline receipt must be an object",
        )
    public = _public_baseline_receipt(result)
    if public["baseline_completed"] is not True:
        raise HomeEdgeActionError(
            "DE_PC_BASELINE_NOT_COMPLETED",
            "Home Edge transport did not complete the DE-PC Windows baseline",
        )
    return public


def _public_baseline_receipt(result: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = result.get("aggregate") if isinstance(result.get("aggregate"), Mapping) else {}
    reason_codes = result.get("reason_codes")
    if not isinstance(reason_codes, list) or not all(
        isinstance(item, str) for item in reason_codes
    ):
        reason_codes = ["missing_stable_reason_codes"]
    baseline_completed = (
        result.get("status") == "completed"
        and result.get("target_id") == FIXED_TARGET_ID
        and result.get("baseline_profile") == FIXED_BASELINE_PROFILE
    )
    classes = aggregate.get("classes")
    counts = aggregate.get("counts")
    booleans = aggregate.get("booleans")
    return {
        "schema": REMOTE_READ_ONLY_DIAGNOSTIC_RECEIPT_SCHEMA,
        "relay_node_id": FIXED_RELAY_NODE_ID,
        "target_id": FIXED_TARGET_ID,
        "baseline_profile": FIXED_BASELINE_PROFILE,
        "baseline_completed": baseline_completed,
        "classes": dict(classes) if isinstance(classes, Mapping) else {},
        "counts": dict(counts) if isinstance(counts, Mapping) else {},
        "booleans": dict(booleans) if isinstance(booleans, Mapping) else {},
        "reason_codes": sorted(set(reason_codes)),
        "private_details": "private_runtime_artifact_only",
    }

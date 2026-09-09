from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_scheduler import LaneRequest, VNextScheduler

ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_projects_only_public_safe_lease_metadata() -> None:
    now = lambda: 100.0
    scheduler = VNextScheduler(LaneLeaseStore(clock=now))
    receipt = scheduler.reserve(LaneRequest(
        task_id="private-task-id",
        lane=Lane.VALIDATE,
        owner="worker-private-name",
        scope_key="private:/customer/path",
        ttl_seconds=30,
        target_state_ref="private-state-ref",
    ))
    public = scheduler.public_projection(receipt)
    blob = json.dumps(public)
    assert public["reason_code"] == "LEASE_ACQUIRED"
    assert "private-task-id" not in blob
    assert "worker-private-name" not in blob
    assert "/customer/path" not in blob
    assert "private-state-ref" not in blob


def test_public_projection_schema_validates() -> None:
    scheduler = VNextScheduler(LaneLeaseStore(clock=lambda: 1.0))
    receipt = scheduler.reserve(LaneRequest("t", Lane.CONTROL, "w", "control", 10, "sha:x"))
    public = scheduler.public_projection(receipt)
    schema = json.loads((ROOT / "schemas" / "runner_lane_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)


def test_labels_are_not_scheduler_input() -> None:
    annotations = LaneRequest.__annotations__
    assert "labels" not in annotations
    assert "github_labels" not in annotations

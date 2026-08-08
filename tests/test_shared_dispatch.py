from __future__ import annotations

from pathlib import Path

from core.shared_dispatch import (
    PRIVACY_PUBLIC_SAFE,
    SharedDispatcher,
    SharedDispatchRequest,
)


def _payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "privacy_boundary": PRIVACY_PUBLIC_SAFE,
        "bounded": True,
        "requested_capabilities": ["loop:state_write"],
        "task_packet": {
            "schema": "skeleton.loop_runner_packet.v1",
            "action": "create",
            "task_id": "task-1",
            "run_id": "run-1",
            "recorded_at": 1,
            "public_safe": True,
            "no_secrets": True,
            "no_runtime_mutation": True,
            "authority_boundary": {
                "operational_state_write": True,
                "external_side_effects_allowed": False,
                "runtime_mutation_allowed": False,
            },
        },
    }
    payload.update(updates)
    return payload


def _request(tmp_path: Path, **updates: object) -> tuple[SharedDispatcher, SharedDispatchRequest]:
    request = SharedDispatchRequest(
        occurrence_id="occ-1",
        route_type="loop",
        route_id="loop_engine_packet",
        payload=_payload(),
        attempt=1,
        idempotency_key="occ-1:attempt:1",
    )
    for key, value in updates.items():
        request = request.__class__(**{**request.__dict__, key: value})
    return SharedDispatcher.for_loop_engine(loop_state_db_path=str(tmp_path / "loop.sqlite3")), request


def test_allowlisted_loop_route_dispatches_public_safe_receipt(tmp_path: Path) -> None:
    dispatcher, request = _request(tmp_path)

    result = dispatcher.dispatch(request)

    assert result.status == "done"
    assert result.receipt["schema"] == "skeleton.scheduler_dispatch_receipt.v1"
    assert result.receipt["external_side_effects_executed"] is False


def test_unknown_route_fails_closed_before_handler(tmp_path: Path) -> None:
    dispatcher, request = _request(tmp_path, route_id="unknown")

    result = dispatcher.dispatch(request)

    assert result.status == "failed"
    assert result.reason == "ROUTE_NOT_ALLOWLISTED"
    assert result.receipt["external_side_effects_executed"] is False


def test_privacy_and_capability_mismatch_fail_closed(tmp_path: Path) -> None:
    dispatcher, privacy_request = _request(
        tmp_path,
        payload=_payload(privacy_boundary="PRIVATE"),
    )
    _, capability_request = _request(
        tmp_path,
        payload=_payload(requested_capabilities=["repository_write"]),
    )

    assert dispatcher.dispatch(privacy_request).reason == "PRIVACY_BOUNDARY_MISMATCH"
    assert dispatcher.dispatch(capability_request).reason == "CAPABILITY_NOT_APPROVED"


def test_scheduler_payload_cannot_self_approve_capabilities(tmp_path: Path) -> None:
    dispatcher, request = _request(
        tmp_path,
        payload=_payload(
            approved_capabilities=["repository_write"],
            requested_capabilities=["repository_write"],
        ),
    )

    result = dispatcher.dispatch(request)

    assert result.status == "failed"
    assert result.reason == "CAPABILITY_NOT_APPROVED"


def test_loop_next_step_proposal_is_returned_not_executed(tmp_path: Path) -> None:
    first = _payload()["task_packet"]
    second = dict(first)
    second.update({"run_id": "run-2"})
    dispatcher, request = _request(
        tmp_path,
        payload=_payload(
            deterministic_workflow={"steps": [first, second], "index": 0},
        ),
    )

    result = dispatcher.dispatch(request)

    assert result.status == "done"
    assert result.next_step is not None
    assert result.next_step["task_packet"]["run_id"] == "run-2"

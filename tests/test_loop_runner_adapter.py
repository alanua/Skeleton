from __future__ import annotations

from pathlib import Path

from core.loop_controller import LoopEvent, LoopPolicy, LoopState
from core.loop_engine import LoopEngine
from core.loop_policy_registry import DEFAULT_LOOP_POLICY_PROFILE
from core.loop_recovery_packet import (
    LOOP_RECOVERY_APPROVAL_SCHEMA,
    LOOP_RECOVERY_PACKET_SCHEMA,
    LoopRecoveryPacket,
    loop_recovery_packet_hash,
)
from core.loop_runner_adapter import (
    LOOP_RUNNER_PACKET_SCHEMA,
    LOOP_RUNNER_RESULT_SCHEMA,
    run_loop_task_packet,
)
from core.loop_state_store import LoopStateStore


AUTHORITY = {
    "operational_state_write": True,
    "external_side_effects_allowed": False,
    "runtime_mutation_allowed": False,
}


def _engine(tmp_path: Path) -> tuple[LoopEngine, LoopStateStore]:
    store = LoopStateStore(tmp_path / "loop-runner.sqlite")
    store.initialize()
    return LoopEngine(store, LoopPolicy()), store


def _packet(action: str, **overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": LOOP_RUNNER_PACKET_SCHEMA,
        "action": action,
        "task_id": "issue-1465",
        "run_id": "run-1465",
        "recorded_at": 1,
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "authority_boundary": dict(AUTHORITY),
    }
    if action == "step":
        packet.update(
            {
                "event": LoopEvent.PREPARED.value,
                "expected_version": 0,
            }
        )
    packet.update(overrides)
    return packet


def _recovery_packet(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": LOOP_RECOVERY_PACKET_SCHEMA,
        "action": "resume_checkpointed",
        "task_id": "issue-1465",
        "run_id": "run-1465",
        "expected_version": 3,
        "expected_state": LoopState.CHECKPOINTED.value,
        "policy_profile": DEFAULT_LOOP_POLICY_PROFILE,
        "approval_reference": "approval-1465",
        "idempotency_key": "recovery-1465",
        "recovery_reason": "operator_resume",
        "public_safe": True,
        "no_secrets": True,
        "no_external_side_effects": True,
    }
    packet.update(overrides)
    return packet


def _recovery_approval(packet: dict[str, object], **overrides: object) -> dict[str, object]:
    validated = LoopRecoveryPacket.from_mapping(packet)
    approval: dict[str, object] = {
        "schema": LOOP_RECOVERY_APPROVAL_SCHEMA,
        "source": "github_issue_comment",
        "operator": "alanua",
        "action": validated.action,
        "task_id": validated.task_id,
        "run_id": validated.run_id,
        "expected_version": validated.expected_version,
        "expected_state": validated.expected_state.value,
        "policy_profile": validated.policy_profile,
        "approval_reference": validated.approval_reference,
        "idempotency_key": validated.idempotency_key,
        "packet_hash": loop_recovery_packet_hash(validated),
        "state_transition": "accepted",
        "public_safe": True,
    }
    approval.update(overrides)
    return approval


def test_create_packet_returns_public_safe_receipt(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)

    receipt = run_loop_task_packet(_packet("create"), engine=engine)

    assert receipt == {
        "schema": LOOP_RUNNER_RESULT_SCHEMA,
        "status": LoopState.CREATED.value,
        "action": "create",
        "task_id": "issue-1465",
        "run_id": "run-1465",
        "version": 0,
        "loop_state": LoopState.CREATED.value,
        "event": None,
        "accepted": True,
        "decision": "CONTINUE",
        "reason": "RUN_CREATED",
        "context_hash": store.load_run("run-1465").context_hash,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def test_step_packet_advances_and_persists_loop(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    run_loop_task_packet(_packet("create"), engine=engine)

    receipt = run_loop_task_packet(
        _packet("step", recorded_at=2, now=2, budget_delta=1),
        engine=engine,
    )

    assert receipt["status"] == LoopState.READY.value
    assert receipt["loop_state"] == LoopState.READY.value
    assert receipt["version"] == 1
    assert receipt["accepted"] is True
    assert receipt["reason"] == "LOOP_PREPARED"
    assert receipt["external_side_effects_executed"] is False
    assert store.load_run("run-1465").context.budget_used == 1


def test_rejected_transition_is_audited_and_returns_blocked(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    run_loop_task_packet(_packet("create"), engine=engine)

    receipt = run_loop_task_packet(
        _packet(
            "step",
            event=LoopEvent.COMPLETE.value,
            recorded_at=2,
            expected_version=0,
        ),
        engine=engine,
    )

    assert receipt["status"] == LoopState.BLOCKED.value
    assert receipt["loop_state"] == LoopState.CREATED.value
    assert receipt["accepted"] is False
    assert receipt["decision"] == "REJECT"
    assert receipt["reason"] == "ILLEGAL_TRANSITION"
    assert receipt["version"] == 1
    events = store.list_events("run-1465")
    assert len(events) == 1
    assert events[0].accepted is False


def test_extra_or_missing_fields_fail_closed_without_creating_run(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    extra = _packet("create", command="rm -rf /")
    missing = _packet("create")
    del missing["public_safe"]

    extra_receipt = run_loop_task_packet(extra, engine=engine)
    missing_receipt = run_loop_task_packet(missing, engine=engine)

    assert extra_receipt["reason"] == "INVALID_LOOP_TASK_PACKET"
    assert missing_receipt["reason"] == "INVALID_LOOP_TASK_PACKET"
    assert extra_receipt["external_side_effects_executed"] is False
    assert missing_receipt["external_side_effects_executed"] is False


def test_invalid_authority_boundary_fails_closed(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    packet = _packet(
        "create",
        authority_boundary={
            "operational_state_write": True,
            "external_side_effects_allowed": True,
            "runtime_mutation_allowed": False,
        },
    )

    receipt = run_loop_task_packet(packet, engine=engine)

    assert receipt["status"] == LoopState.BLOCKED.value
    assert receipt["reason"] == "INVALID_AUTHORITY_BOUNDARY"


def test_task_identity_mismatch_fails_without_event_append(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    run_loop_task_packet(_packet("create"), engine=engine)

    receipt = run_loop_task_packet(
        _packet("step", task_id="other-task", recorded_at=2),
        engine=engine,
    )

    assert receipt["reason"] == "LOOP_TASK_ID_MISMATCH"
    assert store.load_run("run-1465").version == 0
    assert store.list_events("run-1465") == []


def test_stale_expected_version_fails_closed(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    run_loop_task_packet(_packet("create"), engine=engine)
    first = run_loop_task_packet(_packet("step", recorded_at=2), engine=engine)
    assert first["version"] == 1

    stale = run_loop_task_packet(
        _packet(
            "step",
            event=LoopEvent.STARTED.value,
            expected_version=0,
            recorded_at=3,
        ),
        engine=engine,
    )

    assert stale["reason"] == "LOOP_STATE_CONFLICT"
    assert stale["version"] is None
    assert store.load_run("run-1465").version == 1
    assert len(store.list_events("run-1465")) == 1


def test_invalid_identifiers_are_redacted_from_receipt(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    packet = _packet("create", task_id="../private", run_id="secret path")

    receipt = run_loop_task_packet(packet, engine=engine)

    assert receipt["reason"] == "INVALID_LOOP_TASK_PACKET"
    assert receipt["task_id"] is None
    assert receipt["run_id"] is None


def test_invalid_engine_and_step_shape_fail_closed(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    missing_version = _packet("step")
    del missing_version["expected_version"]

    invalid_engine = run_loop_task_packet(_packet("create"), engine=object())  # type: ignore[arg-type]
    missing_version_receipt = run_loop_task_packet(missing_version, engine=engine)

    assert invalid_engine["reason"] == "INVALID_LOOP_ENGINE"
    assert missing_version_receipt["reason"] == "INVALID_LOOP_TASK_PACKET"


def test_recovery_packet_requires_external_matching_operator_approval(
    tmp_path: Path,
) -> None:
    engine, _store = _engine(tmp_path)
    run_loop_task_packet(_packet("create"), engine=engine)
    run_loop_task_packet(_packet("step", recorded_at=2), engine=engine)
    run_loop_task_packet(
        _packet("step", event=LoopEvent.STARTED.value, expected_version=1, recorded_at=3),
        engine=engine,
    )
    run_loop_task_packet(
        _packet("step", event=LoopEvent.CHECKPOINT.value, expected_version=2, recorded_at=4),
        engine=engine,
    )
    packet = _recovery_packet()

    missing = run_loop_task_packet(packet, engine=engine)
    mismatched = run_loop_task_packet(
        packet,
        engine=engine,
        trusted_recovery_approvals=(
            _recovery_approval(packet, expected_state=LoopState.RUNNING.value),
        ),
    )

    assert missing["reason"] == "LOOP_RECOVERY_APPROVAL_REQUIRED"
    assert mismatched["reason"] == "LOOP_RECOVERY_APPROVAL_REQUIRED"


def test_recovery_replay_returns_stable_reason_after_atomic_transition(
    tmp_path: Path,
) -> None:
    engine, store = _engine(tmp_path)
    run_loop_task_packet(_packet("create"), engine=engine)
    run_loop_task_packet(_packet("step", recorded_at=2), engine=engine)
    run_loop_task_packet(
        _packet("step", event=LoopEvent.STARTED.value, expected_version=1, recorded_at=3),
        engine=engine,
    )
    run_loop_task_packet(
        _packet("step", event=LoopEvent.CHECKPOINT.value, expected_version=2, recorded_at=4),
        engine=engine,
    )
    packet = _recovery_packet()
    approval = _recovery_approval(packet)

    recovered = run_loop_task_packet(
        packet,
        engine=engine,
        trusted_recovery_approvals=(approval,),
    )
    replay = run_loop_task_packet(
        packet,
        engine=engine,
        trusted_recovery_approvals=(approval,),
    )

    assert recovered["accepted"] is True
    assert recovered["loop_state"] == LoopState.RUNNING.value
    assert recovered["reason"] == "LOOP_RESUMED"
    assert store.load_run("run-1465").version == 4
    assert replay["reason"] == "LOOP_RECOVERY_REPLAYED"
    assert store.load_run("run-1465").version == 4

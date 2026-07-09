import pytest

from core.loop_controller import LEASED, LoopEvent, LoopState
from core.loop_policy_registry import DEFAULT_LOOP_POLICY_PROFILE
from core.loop_recovery_packet import (
    LOOP_RECOVERY_PACKET_SCHEMA,
    LoopRecoveryPacket,
    LoopRecoveryPacketError,
)


def valid_packet(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": LOOP_RECOVERY_PACKET_SCHEMA,
        "action": "resume_checkpointed",
        "task_id": "issue-1497",
        "run_id": "run-01",
        "expected_version": 3,
        "expected_state": "CHECKPOINTED",
        "policy_profile": DEFAULT_LOOP_POLICY_PROFILE,
        "approval_reference": "approval-1497",
        "idempotency_key": "recovery-1497-3",
        "recovery_reason": "explicit_recovery",
        "public_safe": True,
        "no_secrets": True,
        "no_external_side_effects": True,
    }
    value.update(changes)
    return value


def test_resume_packet_is_explicit_and_deterministic() -> None:
    first = LoopRecoveryPacket.from_mapping(valid_packet())
    second = LoopRecoveryPacket.from_mapping(valid_packet())
    assert first == second
    assert first.event is LoopEvent.STARTED
    assert first.expected_state is LoopState.CHECKPOINTED
    assert first.to_mapping() == valid_packet()


@pytest.mark.parametrize("state", sorted(LEASED, key=lambda state: state.value))
def test_lease_expired_accepts_only_explicitly_leased_states(
    state: LoopState,
) -> None:
    result = LoopRecoveryPacket.from_mapping(
        valid_packet(action="record_lease_expired", expected_state=state.value)
    )
    assert result.event is LoopEvent.LEASE_EXPIRED
    assert result.expected_state is state


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"schema": "wrong"}, "INVALID_LOOP_RECOVERY_SCHEMA"),
        ({"action": "scan_and_resume"}, "INVALID_LOOP_RECOVERY_ACTION"),
        ({"action": {}}, "INVALID_LOOP_RECOVERY_ACTION"),
        ({"expected_version": -1}, "INVALID_LOOP_RECOVERY_VERSION"),
        ({"expected_version": True}, "INVALID_LOOP_RECOVERY_VERSION"),
        ({"expected_state": "RUNNING"}, "LOOP_RECOVERY_STATE_MISMATCH"),
        ({"expected_state": "MISSING"}, "INVALID_LOOP_RECOVERY_STATE"),
        ({"policy_profile": "unregistered"}, "LOOP_POLICY_PROFILE_NOT_REGISTERED"),
        ({"approval_reference": ""}, "INVALID_LOOP_RECOVERY_TOKEN"),
        ({"task_id": "issue 1497"}, "INVALID_LOOP_RECOVERY_TOKEN"),
        ({"public_safe": False}, "INVALID_LOOP_RECOVERY_BOUNDARY"),
        ({"no_secrets": False}, "INVALID_LOOP_RECOVERY_BOUNDARY"),
        ({"no_external_side_effects": False}, "INVALID_LOOP_RECOVERY_BOUNDARY"),
    ],
)
def test_invalid_packets_fail_closed(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(LoopRecoveryPacketError) as exc_info:
        LoopRecoveryPacket.from_mapping(valid_packet(**changes))
    assert exc_info.value.reason_code == reason


@pytest.mark.parametrize(
    "state",
    sorted(set(LoopState) - LEASED, key=lambda state: state.value),
)
def test_lease_expired_rejects_unleased_states(state: LoopState) -> None:
    with pytest.raises(LoopRecoveryPacketError) as exc_info:
        LoopRecoveryPacket.from_mapping(
            valid_packet(
                action="record_lease_expired",
                expected_state=state.value,
            )
        )
    assert exc_info.value.reason_code == "LOOP_RECOVERY_STATE_MISMATCH"


@pytest.mark.parametrize(
    "field",
    [
        "max_iterations",
        "retry_limit",
        "max_budget_units",
        "failure_exhaustion_state",
    ],
)
def test_inline_policy_limit_fields_are_rejected(field: str) -> None:
    value = valid_packet()
    value[field] = 999
    with pytest.raises(LoopRecoveryPacketError) as exc_info:
        LoopRecoveryPacket.from_mapping(value)
    assert exc_info.value.reason_code == "UNKNOWN_LOOP_RECOVERY_FIELD"


def test_legacy_no_runtime_mutation_field_is_rejected() -> None:
    value = valid_packet()
    del value["no_external_side_effects"]
    value["no_runtime_mutation"] = True
    with pytest.raises(LoopRecoveryPacketError) as exc_info:
        LoopRecoveryPacket.from_mapping(value)
    assert exc_info.value.reason_code == "UNKNOWN_LOOP_RECOVERY_FIELD"


def test_missing_approval_is_rejected() -> None:
    value = valid_packet()
    del value["approval_reference"]
    with pytest.raises(LoopRecoveryPacketError) as exc_info:
        LoopRecoveryPacket.from_mapping(value)
    assert exc_info.value.reason_code == "MISSING_LOOP_RECOVERY_FIELD"

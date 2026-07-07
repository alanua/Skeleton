from dataclasses import FrozenInstanceError

import pytest

from core.loop_recovery_contract import (
    LOOP_RECOVERY_PACKET_SCHEMA,
    LoopRecoveryContractError,
    LoopRecoveryPacket,
    RECOVERY_ACTION_LEASE_EXPIRED,
    RECOVERY_ACTION_RESUME_CHECKPOINTED,
)


def packet(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": LOOP_RECOVERY_PACKET_SCHEMA,
        "action": RECOVERY_ACTION_RESUME_CHECKPOINTED,
        "task_id": "issue-1497",
        "run_id": "run-1",
        "expected_version": 3,
        "policy_profile": "default_bounded",
        "recovery_reason": "operator_approved_resume",
        "approval_reference": "issue-1497-comment-1",
        "public_safe": True,
        "no_runtime_mutation": True,
    }
    value.update(changes)
    return value


def test_resume_packet_maps_to_started_event() -> None:
    recovery = LoopRecoveryPacket.from_mapping(packet())
    assert recovery.loop_event == "STARTED"
    assert recovery.expected_version == 3


def test_lease_expired_packet_maps_to_lease_expired_event() -> None:
    recovery = LoopRecoveryPacket.from_mapping(
        packet(action=RECOVERY_ACTION_LEASE_EXPIRED)
    )
    assert recovery.loop_event == "LEASE_EXPIRED"


def test_packet_round_trip_is_deterministic() -> None:
    recovery = LoopRecoveryPacket.from_mapping(packet())
    assert LoopRecoveryPacket.from_mapping(recovery.to_mapping()) == recovery


def test_packet_is_immutable() -> None:
    recovery = LoopRecoveryPacket.from_mapping(packet())
    with pytest.raises(FrozenInstanceError):
        recovery.expected_version = 4  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, "packet", 7, []])
def test_packet_requires_mapping(value: object) -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(value)
    assert exc_info.value.reason_code == "INVALID_LOOP_RECOVERY_PACKET"


def test_unknown_field_fails_closed() -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(packet(extra="no"))
    assert exc_info.value.reason_code == "UNKNOWN_LOOP_RECOVERY_FIELD"


def test_missing_field_fails_closed() -> None:
    value = packet()
    del value["approval_reference"]
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(value)
    assert exc_info.value.reason_code == "MISSING_LOOP_RECOVERY_FIELD"


def test_invalid_schema_fails_closed() -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(packet(schema="wrong"))
    assert exc_info.value.reason_code == "INVALID_LOOP_RECOVERY_SCHEMA"


def test_unknown_action_fails_closed() -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(packet(action="automatic_resume"))
    assert exc_info.value.reason_code == "INVALID_LOOP_RECOVERY_ACTION"


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", ""),
        ("run_id", "bad value"),
        ("policy_profile", None),
        ("recovery_reason", "../unsafe"),
        ("approval_reference", {}),
        ("expected_version", -1),
        ("expected_version", True),
    ],
)
def test_invalid_fields_fail_closed(field: str, value: object) -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(packet(**{field: value}))
    assert exc_info.value.reason_code == "INVALID_LOOP_RECOVERY_FIELD"


def test_public_safe_is_required() -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(packet(public_safe=False))
    assert exc_info.value.reason_code == "LOOP_RECOVERY_NOT_PUBLIC_SAFE"


def test_contract_does_not_authorize_runtime_mutation() -> None:
    with pytest.raises(LoopRecoveryContractError) as exc_info:
        LoopRecoveryPacket.from_mapping(packet(no_runtime_mutation=False))
    assert exc_info.value.reason_code == "LOOP_RECOVERY_RUNTIME_MUTATION_FORBIDDEN"

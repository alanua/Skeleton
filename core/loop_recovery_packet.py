from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Final

from core.loop_controller import LEASED, LoopEvent, LoopState
from core.loop_policy_registry import LoopPolicyProfileError, resolve_loop_policy_profile


LOOP_RECOVERY_PACKET_SCHEMA: Final = "skeleton.loop_recovery_packet.v1"
LOOP_RECOVERY_APPROVAL_SCHEMA: Final = "skeleton.loop_recovery_approval.v1"
LOOP_RECOVERY_ACTIONS: Final = frozenset(
    {"resume_checkpointed", "record_lease_expired"}
)

_REQUIRED_FIELDS: Final = frozenset(
    {
        "schema",
        "action",
        "task_id",
        "run_id",
        "expected_version",
        "expected_state",
        "policy_profile",
        "approval_reference",
        "idempotency_key",
        "recovery_reason",
        "public_safe",
        "no_secrets",
        "no_external_side_effects",
    }
)
_SAFE_TOKEN_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ACTION_EVENTS: Final = MappingProxyType(
    {
        "resume_checkpointed": LoopEvent.STARTED,
        "record_lease_expired": LoopEvent.LEASE_EXPIRED,
    }
)
_ACTION_STATES: Final = MappingProxyType(
    {
        "resume_checkpointed": frozenset({LoopState.CHECKPOINTED}),
        "record_lease_expired": LEASED,
    }
)


class LoopRecoveryPacketError(ValueError):
    """Raised when an explicit Loop recovery packet is malformed or out of policy."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class LoopRecoveryApprovalError(ValueError):
    """Raised when external Loop recovery approval metadata is not authoritative."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LoopRecoveryPacket:
    """Validated instruction for one bounded Loop state transition.

    Applying the packet may mutate Loop operational state. It must not itself
    authorize model, shell, network, memory, deployment, or other external side
    effects.
    """

    schema: str
    action: str
    task_id: str
    run_id: str
    expected_version: int
    expected_state: LoopState
    policy_profile: str
    approval_reference: str
    idempotency_key: str
    recovery_reason: str
    public_safe: bool
    no_secrets: bool
    no_external_side_effects: bool

    @classmethod
    def from_mapping(cls, value: object) -> "LoopRecoveryPacket":
        if not isinstance(value, Mapping):
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_PACKET",
                "Loop recovery packet must be a mapping",
            )
        if any(not isinstance(key, str) for key in value):
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_FIELD",
                "Loop recovery packet keys must be strings",
            )

        fields = frozenset(value)
        unknown = sorted(fields - _REQUIRED_FIELDS)
        if unknown:
            raise LoopRecoveryPacketError(
                "UNKNOWN_LOOP_RECOVERY_FIELD",
                f"unknown Loop recovery field: {unknown[0]}",
            )
        missing = sorted(_REQUIRED_FIELDS - fields)
        if missing:
            raise LoopRecoveryPacketError(
                "MISSING_LOOP_RECOVERY_FIELD",
                f"missing Loop recovery field: {missing[0]}",
            )

        if value["schema"] != LOOP_RECOVERY_PACKET_SCHEMA:
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_SCHEMA",
                "invalid Loop recovery packet schema",
            )

        action = value["action"]
        if not isinstance(action, str) or action not in LOOP_RECOVERY_ACTIONS:
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_ACTION",
                "unsupported Loop recovery action",
            )

        expected_version = value["expected_version"]
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_VERSION",
                "expected_version must be a non-negative integer",
            )

        expected_state_value = value["expected_state"]
        if not isinstance(expected_state_value, str):
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_STATE",
                "expected_state must be a Loop state string",
            )
        try:
            expected_state = LoopState(expected_state_value)
        except ValueError as exc:
            raise LoopRecoveryPacketError(
                "INVALID_LOOP_RECOVERY_STATE",
                "expected_state is not a registered Loop state",
            ) from exc
        if expected_state not in _ACTION_STATES[action]:
            raise LoopRecoveryPacketError(
                "LOOP_RECOVERY_STATE_MISMATCH",
                "recovery action is not valid for expected_state",
            )

        for boundary_field in (
            "public_safe",
            "no_secrets",
            "no_external_side_effects",
        ):
            if value[boundary_field] is not True:
                raise LoopRecoveryPacketError(
                    "INVALID_LOOP_RECOVERY_BOUNDARY",
                    f"{boundary_field} must be true",
                )

        task_id = _safe_token(value["task_id"], "task_id")
        run_id = _safe_token(value["run_id"], "run_id")
        policy_profile = _safe_token(value["policy_profile"], "policy_profile")
        approval_reference = _safe_token(
            value["approval_reference"], "approval_reference"
        )
        idempotency_key = _safe_token(value["idempotency_key"], "idempotency_key")
        recovery_reason = _safe_token(value["recovery_reason"], "recovery_reason")

        try:
            resolve_loop_policy_profile(policy_profile)
        except LoopPolicyProfileError as exc:
            raise LoopRecoveryPacketError(exc.reason_code, str(exc)) from exc

        return cls(
            schema=LOOP_RECOVERY_PACKET_SCHEMA,
            action=action,
            task_id=task_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_state=expected_state,
            policy_profile=policy_profile,
            approval_reference=approval_reference,
            idempotency_key=idempotency_key,
            recovery_reason=recovery_reason,
            public_safe=True,
            no_secrets=True,
            no_external_side_effects=True,
        )

    @property
    def event(self) -> LoopEvent:
        return _ACTION_EVENTS[self.action]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action": self.action,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "expected_version": self.expected_version,
            "expected_state": self.expected_state.value,
            "policy_profile": self.policy_profile,
            "approval_reference": self.approval_reference,
            "idempotency_key": self.idempotency_key,
            "recovery_reason": self.recovery_reason,
            "public_safe": self.public_safe,
            "no_secrets": self.no_secrets,
            "no_external_side_effects": self.no_external_side_effects,
        }


@dataclass(frozen=True)
class TrustedLoopRecoveryApproval:
    schema: str
    source: str
    operator: str
    action: str
    task_id: str
    run_id: str
    expected_version: int
    expected_state: str
    policy_profile: str
    approval_reference: str
    idempotency_key: str
    packet_hash: str
    state_transition: str
    public_safe: bool

    @classmethod
    def from_mapping(cls, value: object) -> "TrustedLoopRecoveryApproval":
        if not isinstance(value, Mapping):
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL",
                "Loop recovery approval must be a mapping",
            )
        required = {
            "schema",
            "source",
            "operator",
            "action",
            "task_id",
            "run_id",
            "expected_version",
            "expected_state",
            "policy_profile",
            "approval_reference",
            "idempotency_key",
            "packet_hash",
            "state_transition",
            "public_safe",
        }
        if frozenset(value) != required:
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_FIELDS",
                "Loop recovery approval fields must match the authority schema",
            )
        if value["schema"] != LOOP_RECOVERY_APPROVAL_SCHEMA:
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_SCHEMA",
                "invalid Loop recovery approval schema",
            )
        if value["source"] != "github_issue_comment":
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_SOURCE",
                "Loop recovery approval source is not authoritative",
            )
        if value["state_transition"] != "accepted":
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_STATE_TRANSITION",
                "Loop recovery approval must authorize the accepted transition",
            )
        if value["public_safe"] is not True:
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_BOUNDARY",
                "Loop recovery approval must be public-safe",
            )
        expected_version = value["expected_version"]
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_VERSION",
                "expected_version must be a non-negative integer",
            )
        action = _approval_safe_token(value["action"], "action")
        if action not in LOOP_RECOVERY_ACTIONS:
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_ACTION",
                "unsupported Loop recovery approval action",
            )
        expected_state = _approval_safe_token(value["expected_state"], "expected_state")
        try:
            LoopState(expected_state)
        except ValueError as exc:
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_STATE",
                "expected_state is not a registered Loop state",
            ) from exc
        policy_profile = _approval_safe_token(value["policy_profile"], "policy_profile")
        try:
            resolve_loop_policy_profile(policy_profile)
        except LoopPolicyProfileError as exc:
            raise LoopRecoveryApprovalError(exc.reason_code, str(exc)) from exc
        packet_hash = value["packet_hash"]
        if not isinstance(packet_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", packet_hash):
            raise LoopRecoveryApprovalError(
                "INVALID_LOOP_RECOVERY_APPROVAL_PACKET_HASH",
                "packet_hash must be a SHA-256 hex digest",
            )
        return cls(
            schema=LOOP_RECOVERY_APPROVAL_SCHEMA,
            source="github_issue_comment",
            operator=_approval_safe_token(value["operator"], "operator"),
            action=action,
            task_id=_approval_safe_token(value["task_id"], "task_id"),
            run_id=_approval_safe_token(value["run_id"], "run_id"),
            expected_version=expected_version,
            expected_state=expected_state,
            policy_profile=policy_profile,
            approval_reference=_approval_safe_token(
                value["approval_reference"], "approval_reference"
            ),
            idempotency_key=_approval_safe_token(
                value["idempotency_key"], "idempotency_key"
            ),
            packet_hash=packet_hash,
            state_transition="accepted",
            public_safe=True,
        )

    def matches_packet(self, packet: LoopRecoveryPacket) -> bool:
        return (
            self.action == packet.action
            and self.task_id == packet.task_id
            and self.run_id == packet.run_id
            and self.expected_version == packet.expected_version
            and self.expected_state == packet.expected_state.value
            and self.policy_profile == packet.policy_profile
            and self.approval_reference == packet.approval_reference
            and self.idempotency_key == packet.idempotency_key
            and self.packet_hash == loop_recovery_packet_hash(packet)
            and self.state_transition == "accepted"
            and self.public_safe is True
        )


def loop_recovery_packet_hash(packet: LoopRecoveryPacket) -> str:
    encoded = json.dumps(
        packet.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise LoopRecoveryPacketError(
            "INVALID_LOOP_RECOVERY_TOKEN",
            f"{field} must be a bounded public-safe token",
        )
    return value


def _approval_safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise LoopRecoveryApprovalError(
            "INVALID_LOOP_RECOVERY_APPROVAL_TOKEN",
            f"{field} must be a bounded public-safe token",
        )
    return value

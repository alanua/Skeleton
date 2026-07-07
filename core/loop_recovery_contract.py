from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Final


LOOP_RECOVERY_PACKET_SCHEMA: Final = "skeleton.loop_recovery_packet.v1"
RECOVERY_ACTION_LEASE_EXPIRED: Final = "lease_expired"
RECOVERY_ACTION_RESUME_CHECKPOINTED: Final = "resume_checkpointed"
RECOVERY_ACTIONS: Final = frozenset(
    {RECOVERY_ACTION_LEASE_EXPIRED, RECOVERY_ACTION_RESUME_CHECKPOINTED}
)
RECOVERY_EVENTS: Final = MappingProxyType(
    {
        RECOVERY_ACTION_LEASE_EXPIRED: "LEASE_EXPIRED",
        RECOVERY_ACTION_RESUME_CHECKPOINTED: "STARTED",
    }
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "action",
        "task_id",
        "run_id",
        "expected_version",
        "policy_profile",
        "recovery_reason",
        "approval_reference",
        "public_safe",
        "no_runtime_mutation",
    }
)


class LoopRecoveryContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LoopRecoveryPacket:
    schema: str
    action: str
    task_id: str
    run_id: str
    expected_version: int
    policy_profile: str
    recovery_reason: str
    approval_reference: str
    public_safe: bool
    no_runtime_mutation: bool

    @property
    def loop_event(self) -> str:
        return RECOVERY_EVENTS[self.action]

    @classmethod
    def from_mapping(cls, value: object) -> "LoopRecoveryPacket":
        if not isinstance(value, Mapping):
            raise LoopRecoveryContractError(
                "INVALID_LOOP_RECOVERY_PACKET",
                "Loop recovery packet must be a mapping",
            )

        fields = frozenset(value)
        unknown = sorted(fields - _REQUIRED_FIELDS)
        if unknown:
            raise LoopRecoveryContractError(
                "UNKNOWN_LOOP_RECOVERY_FIELD",
                f"unknown Loop recovery field: {unknown[0]}",
            )
        missing = sorted(_REQUIRED_FIELDS - fields)
        if missing:
            raise LoopRecoveryContractError(
                "MISSING_LOOP_RECOVERY_FIELD",
                f"missing Loop recovery field: {missing[0]}",
            )

        if value["schema"] != LOOP_RECOVERY_PACKET_SCHEMA:
            raise LoopRecoveryContractError(
                "INVALID_LOOP_RECOVERY_SCHEMA",
                "Loop recovery packet schema is invalid",
            )

        action = _enum_token(value["action"], "action", RECOVERY_ACTIONS)
        task_id = _safe_token(value["task_id"], "task_id")
        run_id = _safe_token(value["run_id"], "run_id")
        expected_version = _non_negative_int(
            value["expected_version"], "expected_version"
        )
        policy_profile = _safe_token(value["policy_profile"], "policy_profile")
        recovery_reason = _safe_token(value["recovery_reason"], "recovery_reason")
        approval_reference = _safe_token(
            value["approval_reference"], "approval_reference"
        )

        if value["public_safe"] is not True:
            raise LoopRecoveryContractError(
                "LOOP_RECOVERY_NOT_PUBLIC_SAFE",
                "Loop recovery packet must be public-safe",
            )
        if value["no_runtime_mutation"] is not True:
            raise LoopRecoveryContractError(
                "LOOP_RECOVERY_RUNTIME_MUTATION_FORBIDDEN",
                "Loop recovery contract does not authorize runtime mutation",
            )

        return cls(
            schema=LOOP_RECOVERY_PACKET_SCHEMA,
            action=action,
            task_id=task_id,
            run_id=run_id,
            expected_version=expected_version,
            policy_profile=policy_profile,
            recovery_reason=recovery_reason,
            approval_reference=approval_reference,
            public_safe=True,
            no_runtime_mutation=True,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action": self.action,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "expected_version": self.expected_version,
            "policy_profile": self.policy_profile,
            "recovery_reason": self.recovery_reason,
            "approval_reference": self.approval_reference,
            "public_safe": self.public_safe,
            "no_runtime_mutation": self.no_runtime_mutation,
        }


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise LoopRecoveryContractError(
            "INVALID_LOOP_RECOVERY_FIELD",
            f"{field} must be a bounded public-safe token",
        )
    return value


def _enum_token(value: object, field: str, allowed: frozenset[str]) -> str:
    normalized = _safe_token(value, field)
    if normalized not in allowed:
        raise LoopRecoveryContractError(
            "INVALID_LOOP_RECOVERY_ACTION",
            f"unsupported Loop recovery action: {normalized}",
        )
    return normalized


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LoopRecoveryContractError(
            "INVALID_LOOP_RECOVERY_FIELD",
            f"{field} must be a non-negative integer",
        )
    return value

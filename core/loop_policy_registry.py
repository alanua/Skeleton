from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from core.loop_controller import LoopPolicy, LoopState


DEFAULT_LOOP_POLICY_PROFILE: Final = "default_bounded"
FORBIDDEN_LIMIT_FIELDS: Final = frozenset(
    {
        "max_iterations",
        "retry_limit",
        "max_budget_units",
        "failure_exhaustion_state",
    }
)


class LoopPolicyProfileError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_PROFILES: Final = MappingProxyType(
    {
        DEFAULT_LOOP_POLICY_PROFILE: LoopPolicy(
            max_iterations=10,
            retry_limit=2,
            max_budget_units=100,
            failure_exhaustion_state=LoopState.BLOCKED,
        )
    }
)


def registered_loop_policy_profiles() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def resolve_loop_policy_profile(profile_name: object) -> LoopPolicy:
    if not isinstance(profile_name, str) or not profile_name:
        raise LoopPolicyProfileError(
            "INVALID_LOOP_POLICY_PROFILE",
            "policy_profile must be a non-empty string",
        )
    try:
        return _PROFILES[profile_name]
    except KeyError as exc:
        raise LoopPolicyProfileError(
            "LOOP_POLICY_PROFILE_NOT_REGISTERED",
            f"Loop policy profile is not registered: {profile_name}",
        ) from exc


def resolve_loop_policy_from_payload(payload: object) -> LoopPolicy:
    if not isinstance(payload, Mapping):
        raise LoopPolicyProfileError(
            "INVALID_LOOP_POLICY_PAYLOAD",
            "Loop policy payload must be a mapping",
        )
    forbidden = tuple(sorted(FORBIDDEN_LIMIT_FIELDS.intersection(payload)))
    if forbidden:
        raise LoopPolicyProfileError(
            "LOOP_POLICY_LIMIT_OVERRIDE_FORBIDDEN",
            f"Loop policy limits must come from a registered profile: {forbidden[0]}",
        )
    if "policy_profile" not in payload:
        raise LoopPolicyProfileError(
            "MISSING_LOOP_POLICY_PROFILE",
            "Loop policy payload must declare policy_profile",
        )
    return resolve_loop_policy_profile(payload["policy_profile"])

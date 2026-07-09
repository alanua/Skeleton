from dataclasses import FrozenInstanceError

import pytest

from core.loop_controller import LoopPolicy, LoopState
from core.loop_policy_registry import (
    DEFAULT_LOOP_POLICY_PROFILE,
    LoopPolicyProfileError,
    registered_loop_policy_profiles,
    resolve_loop_policy_from_payload,
    resolve_loop_policy_profile,
)


def test_registered_profiles_are_deterministic() -> None:
    assert registered_loop_policy_profiles() == (DEFAULT_LOOP_POLICY_PROFILE,)


def test_default_profile_resolves_to_bounded_policy() -> None:
    assert resolve_loop_policy_profile(DEFAULT_LOOP_POLICY_PROFILE) == LoopPolicy(
        max_iterations=10,
        retry_limit=2,
        max_budget_units=100,
        failure_exhaustion_state=LoopState.BLOCKED,
    )


def test_resolved_policy_is_immutable() -> None:
    policy = resolve_loop_policy_profile(DEFAULT_LOOP_POLICY_PROFILE)
    with pytest.raises(FrozenInstanceError):
        policy.max_iterations = 99  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, "", 7, {}, []])
def test_invalid_profile_name_fails_closed(value: object) -> None:
    with pytest.raises(LoopPolicyProfileError) as exc_info:
        resolve_loop_policy_profile(value)
    assert exc_info.value.reason_code == "INVALID_LOOP_POLICY_PROFILE"


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(LoopPolicyProfileError) as exc_info:
        resolve_loop_policy_profile("unregistered")
    assert exc_info.value.reason_code == "LOOP_POLICY_PROFILE_NOT_REGISTERED"


def test_payload_resolves_registered_profile() -> None:
    assert resolve_loop_policy_from_payload(
        {
            "policy_profile": DEFAULT_LOOP_POLICY_PROFILE,
            "schema": "skeleton.loop_runner_packet.v1",
            "action": "create",
        }
    ) == resolve_loop_policy_profile(DEFAULT_LOOP_POLICY_PROFILE)


def test_payload_requires_mapping() -> None:
    with pytest.raises(LoopPolicyProfileError) as exc_info:
        resolve_loop_policy_from_payload(DEFAULT_LOOP_POLICY_PROFILE)
    assert exc_info.value.reason_code == "INVALID_LOOP_POLICY_PAYLOAD"


def test_payload_requires_profile() -> None:
    with pytest.raises(LoopPolicyProfileError) as exc_info:
        resolve_loop_policy_from_payload({"action": "create"})
    assert exc_info.value.reason_code == "MISSING_LOOP_POLICY_PROFILE"


@pytest.mark.parametrize(
    "field",
    [
        "max_iterations",
        "retry_limit",
        "max_budget_units",
        "failure_exhaustion_state",
    ],
)
def test_payload_rejects_inline_limit_overrides(field: str) -> None:
    with pytest.raises(LoopPolicyProfileError) as exc_info:
        resolve_loop_policy_from_payload(
            {
                "policy_profile": DEFAULT_LOOP_POLICY_PROFILE,
                field: 1,
            }
        )
    assert exc_info.value.reason_code == "LOOP_POLICY_LIMIT_OVERRIDE_FORBIDDEN"


def test_resolution_is_deterministic() -> None:
    first = resolve_loop_policy_from_payload(
        {"policy_profile": DEFAULT_LOOP_POLICY_PROFILE}
    )
    second = resolve_loop_policy_from_payload(
        {"policy_profile": DEFAULT_LOOP_POLICY_PROFILE}
    )
    assert first == second

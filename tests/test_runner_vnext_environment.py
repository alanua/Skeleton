from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_environment import (
    EnvironmentContractError,
    LaneEnvironmentPolicy,
    LaneEnvironmentPolicyRegistry,
    lane_environment_policy_hash,
    validate_lane_environment_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def validation_policy() -> LaneEnvironmentPolicy:
    return LaneEnvironmentPolicy(
        lane_id="lane:validation",
        inherit_keys=("HOME", "PATH", "LANG", "SKELETON_LLM_MODEL"),
        inject_keys=(),
        denied_keys=("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"),
        secret_keys=(),
    )


def test_positive_allowlist_strips_parent_secrets_and_unknowns() -> None:
    parent = {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SKELETON_LLM_MODEL": "model:test",
        "OPENROUTER_API_KEY": "secret-a",
        "BWS_ACCESS_TOKEN": "secret-b",
        "SKELETON_TG_BOT": "secret-c",
        "UNRELATED_PARENT_FLAG": "drop-me",
    }
    registry = LaneEnvironmentPolicyRegistry({"lane:validation": validation_policy()})
    plan = registry.build(lane_id="lane:validation", parent_environment=parent, injected_environment={})
    assert dict(plan.environment) == {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SKELETON_LLM_MODEL": "model:test",
    }
    assert plan.receipt.stripped_parent_key_count == 4
    assert plan.receipt.values_exposed is False


def test_secret_injection_is_exact_lane_scoped_and_receipt_is_value_free() -> None:
    policy = LaneEnvironmentPolicy(
        lane_id="lane:provider",
        inherit_keys=("HOME", "PATH"),
        inject_keys=("OPENROUTER_API_KEY",),
        denied_keys=(),
        secret_keys=("OPENROUTER_API_KEY",),
    )
    registry = LaneEnvironmentPolicyRegistry({"lane:provider": policy})
    plan = registry.build(
        lane_id="lane:provider",
        parent_environment={"HOME": "/home/agent", "PATH": "/usr/bin", "OPENROUTER_API_KEY": "parent-secret"},
        injected_environment={"OPENROUTER_API_KEY": "injected-secret"},
    )
    assert plan.environment["OPENROUTER_API_KEY"] == "injected-secret"
    receipt = asdict(plan.receipt)
    assert receipt["secret_key_names"] == ("OPENROUTER_API_KEY",)
    assert "injected-secret" not in repr(receipt)
    assert "parent-secret" not in repr(receipt)
    assert "injected-secret" not in repr(plan)


def test_undeclared_or_denied_injection_fails_closed() -> None:
    with pytest.raises(EnvironmentContractError, match="ENV_UNDECLARED_INJECTION"):
        LaneEnvironmentPolicyRegistry({"lane:validation": validation_policy()}).build(
            lane_id="lane:validation", parent_environment={}, injected_environment={"RANDOM_INJECT": "value"}
        )
    policy = LaneEnvironmentPolicy("lane:test", (), (), ("SAFE_FLAG",), ())
    with pytest.raises(EnvironmentContractError, match="ENV_DENIED_INJECTION"):
        LaneEnvironmentPolicyRegistry({"lane:test": policy}).build(lane_id="lane:test", parent_environment={}, injected_environment={"SAFE_FLAG": "1"})


def test_sensitive_parent_inheritance_is_forbidden_even_when_named() -> None:
    for key in ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "MY_CUSTOM_SECRET", "SERVICE_PASSWORD"):
        policy = LaneEnvironmentPolicy("lane:test", (key,), (), (), ())
        with pytest.raises(EnvironmentContractError, match="ENV_POLICY_SECRET_INHERIT_FORBIDDEN"):
            validate_lane_environment_policy(policy)


def test_sensitive_injection_must_be_classified_as_secret() -> None:
    policy = LaneEnvironmentPolicy("lane:test", (), ("SKELETON_TG_BOT",), (), ())
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_SECRET_CLASSIFICATION_REQUIRED"):
        validate_lane_environment_policy(policy)


def test_overlap_duplicate_wildcard_and_invalid_lane_fail_closed() -> None:
    cases = [
        (LaneEnvironmentPolicy("lane:test", ("PATH",), ("PATH",), (), ()), "ENV_POLICY_SOURCE_OVERLAP"),
        (LaneEnvironmentPolicy("lane:test", ("PATH",), (), ("PATH",), ()), "ENV_POLICY_DENY_OVERLAP"),
        (LaneEnvironmentPolicy("lane:test", ("PATH", "PATH"), (), (), ()), "ENV_POLICY_DUPLICATE_KEY"),
        (LaneEnvironmentPolicy("lane:test", ("*",), (), (), ()), "ENV_POLICY_KEY_INVALID"),
        (LaneEnvironmentPolicy("validation", ("PATH",), (), (), ()), "ENV_LANE_ID_INVALID"),
    ]
    for policy, reason in cases:
        with pytest.raises(EnvironmentContractError, match=reason):
            validate_lane_environment_policy(policy)


def test_secret_key_must_be_in_injection_set() -> None:
    policy = LaneEnvironmentPolicy("lane:test", (), (), (), ("OPENROUTER_API_KEY",))
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_SECRET_MUST_BE_INJECTED"):
        validate_lane_environment_policy(policy)


def test_policy_and_receipt_schemas_are_closed_and_value_free() -> None:
    policy_schema = json.loads((ROOT / "schemas" / "runner_lane_environment_policy.schema.json").read_text())
    receipt_schema = json.loads((ROOT / "schemas" / "runner_child_environment_receipt.schema.json").read_text())
    assert policy_schema["additionalProperties"] is False
    assert receipt_schema["additionalProperties"] is False
    policy_payload = {
        "schema": "skeleton.runner_lane_environment_policy.v1",
        "lane_id": "lane:validation",
        "inherit_keys": ["HOME", "PATH"],
        "inject_keys": [],
        "denied_keys": ["OPENROUTER_API_KEY"],
        "secret_keys": [],
    }
    jsonschema.validate(policy_payload, policy_schema)
    receipt_payload = {
        "schema": "skeleton.runner_child_environment_receipt.v1",
        "lane_id": "lane:validation",
        "policy_hash": lane_environment_policy_hash(validation_policy()),
        "inherited_keys": ["HOME", "PATH"],
        "injected_keys": [],
        "secret_key_names": [],
        "stripped_parent_key_count": 3,
        "environment_key_count": 2,
        "values_exposed": False,
    }
    jsonschema.validate(receipt_payload, receipt_schema)
    assert receipt_schema["properties"]["values_exposed"]["const"] is False
    assert "environment" not in receipt_schema["properties"]
    assert "secret_values" not in receipt_schema["properties"]


def test_registry_binds_policy_to_exact_lane_and_rejects_unknown_lane() -> None:
    registry = LaneEnvironmentPolicyRegistry({"lane:validation": validation_policy()})
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_LANE_UNKNOWN"):
        registry.build(lane_id="lane:provider", parent_environment={}, injected_environment={})
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_REGISTRY_LANE_MISMATCH"):
        LaneEnvironmentPolicyRegistry({"lane:wrong": validation_policy()})


def test_receipt_binds_exact_policy_hash() -> None:
    policy = validation_policy()
    plan = LaneEnvironmentPolicyRegistry({policy.lane_id: policy}).build(
        lane_id=policy.lane_id, parent_environment={"HOME":"/h","PATH":"/p"}, injected_environment={}
    )
    assert plan.receipt.policy_hash == lane_environment_policy_hash(policy)
    changed = LaneEnvironmentPolicy(policy.lane_id, ("HOME",), (), policy.denied_keys, ())
    assert lane_environment_policy_hash(changed) != plan.receipt.policy_hash

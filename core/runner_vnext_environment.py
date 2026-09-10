from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Mapping


KNOWN_SECRET_ENV_KEYS = frozenset({
    "OPENROUTER_API_KEY",
    "SKELETON_OPENROUTER_FALLBACK_API_KEY",
    "BWS_ACCESS_TOKEN",
    "CREDENTIALS_DIRECTORY",
    "LLM_API_KEY",
    "SKELETON_TG_BOT",
})
_SENSITIVE_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "CREDENTIAL")


class EnvironmentContractError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LaneEnvironmentPolicy:
    lane_id: str
    inherit_keys: tuple[str, ...]
    inject_keys: tuple[str, ...]
    denied_keys: tuple[str, ...]
    secret_keys: tuple[str, ...]


@dataclass(frozen=True)
class ChildEnvironmentReceipt:
    lane_id: str
    policy_hash: str
    inherited_keys: tuple[str, ...]
    injected_keys: tuple[str, ...]
    secret_key_names: tuple[str, ...]
    stripped_parent_key_count: int
    environment_key_count: int
    values_exposed: bool = False


@dataclass(frozen=True)
class ChildEnvironmentPlan:
    environment: Mapping[str, str] = field(repr=False)
    receipt: ChildEnvironmentReceipt


class LaneEnvironmentPolicyRegistry:
    """Authoritative in-process registry for exact lane environment contracts."""

    def __init__(self, policies: Mapping[str, LaneEnvironmentPolicy]) -> None:
        self._policies = dict(policies)
        if len(self._policies) != len(policies):
            raise EnvironmentContractError("ENV_POLICY_REGISTRY_DUPLICATE")
        for lane_id, policy in self._policies.items():
            validate_lane_environment_policy(policy)
            if lane_id != policy.lane_id:
                raise EnvironmentContractError("ENV_POLICY_REGISTRY_LANE_MISMATCH")

    def policy_hash(self, lane_id: str) -> str:
        policy = self._policies.get(lane_id)
        if policy is None:
            raise EnvironmentContractError("ENV_POLICY_LANE_UNKNOWN")
        return lane_environment_policy_hash(policy)

    def build(
        self,
        *,
        lane_id: str,
        parent_environment: Mapping[str, str],
        injected_environment: Mapping[str, str],
    ) -> ChildEnvironmentPlan:
        policy = self._policies.get(lane_id)
        if policy is None:
            raise EnvironmentContractError("ENV_POLICY_LANE_UNKNOWN")
        return _build_child_environment(
            parent_environment=parent_environment,
            injected_environment=injected_environment,
            policy=policy,
        )


def _build_child_environment(
    *,
    parent_environment: Mapping[str, str],
    injected_environment: Mapping[str, str],
    policy: LaneEnvironmentPolicy,
) -> ChildEnvironmentPlan:
    injected_keys = set(injected_environment)
    if injected_keys & set(policy.denied_keys):
        raise EnvironmentContractError("ENV_DENIED_INJECTION")
    undeclared = injected_keys - set(policy.inject_keys)
    if undeclared:
        raise EnvironmentContractError("ENV_UNDECLARED_INJECTION")

    child: dict[str, str] = {}
    for key in policy.inherit_keys:
        if key in parent_environment:
            child[key] = str(parent_environment[key])
    for key in policy.inject_keys:
        if key in injected_environment:
            child[key] = str(injected_environment[key])

    inherited = tuple(sorted(set(child) & set(policy.inherit_keys)))
    injected = tuple(sorted(set(child) & set(policy.inject_keys)))
    secret_names = tuple(sorted(set(injected) & set(policy.secret_keys)))
    receipt = ChildEnvironmentReceipt(
        lane_id=policy.lane_id,
        policy_hash=lane_environment_policy_hash(policy),
        inherited_keys=inherited,
        injected_keys=injected,
        secret_key_names=secret_names,
        stripped_parent_key_count=max(0, len(parent_environment) - len(inherited)),
        environment_key_count=len(child),
    )
    return ChildEnvironmentPlan(MappingProxyType(child), receipt)


def validate_lane_environment_policy(policy: LaneEnvironmentPolicy) -> None:
    if not _public_lane_id(policy.lane_id):
        raise EnvironmentContractError("ENV_LANE_ID_INVALID")
    groups = {
        "inherit": policy.inherit_keys,
        "inject": policy.inject_keys,
        "deny": policy.denied_keys,
        "secret": policy.secret_keys,
    }
    for values in groups.values():
        if len(set(values)) != len(values):
            raise EnvironmentContractError("ENV_POLICY_DUPLICATE_KEY")
        for key in values:
            if not _env_key(key):
                raise EnvironmentContractError("ENV_POLICY_KEY_INVALID")
    inherit = set(policy.inherit_keys)
    inject = set(policy.inject_keys)
    deny = set(policy.denied_keys)
    secrets = set(policy.secret_keys)
    if inherit & inject:
        raise EnvironmentContractError("ENV_POLICY_SOURCE_OVERLAP")
    if deny & (inherit | inject):
        raise EnvironmentContractError("ENV_POLICY_DENY_OVERLAP")
    if not secrets.issubset(inject):
        raise EnvironmentContractError("ENV_POLICY_SECRET_MUST_BE_INJECTED")
    if any(_looks_sensitive(key) for key in inherit):
        raise EnvironmentContractError("ENV_POLICY_SECRET_INHERIT_FORBIDDEN")
    sensitive_injected = {key for key in inject if _looks_sensitive(key)}
    if not sensitive_injected.issubset(secrets):
        raise EnvironmentContractError("ENV_POLICY_SECRET_CLASSIFICATION_REQUIRED")


def _looks_sensitive(key: str) -> bool:
    return key in KNOWN_SECRET_ENV_KEYS or any(marker in key for marker in _SENSITIVE_MARKERS)


def _env_key(key: str) -> bool:
    return bool(key) and key != "*" and all(ch.isupper() or ch.isdigit() or ch == "_" for ch in key)


def _public_lane_id(value: str) -> bool:
    return value.startswith("lane:") and len(value) > 5 and all(ch.islower() or ch.isdigit() or ch in "-_" for ch in value[5:])


def lane_environment_policy_hash(policy: LaneEnvironmentPolicy) -> str:
    validate_lane_environment_policy(policy)
    payload = {
        "lane_id": policy.lane_id,
        "inherit_keys": list(policy.inherit_keys),
        "inject_keys": list(policy.inject_keys),
        "denied_keys": list(policy.denied_keys),
        "secret_keys": list(policy.secret_keys),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


MANIFEST_SCHEMA = "skeleton.resolver_capability.manifest.v1"
CANON_SCHEMA = "skeleton.resolver_capability.canon.v1"
STATE_SCHEMA = "skeleton.resolver_capability.state.v1"
STATUS_VALUES = frozenset(
    {
        "discovered",
        "researching",
        "implemented",
        "tested",
        "approved",
        "canary",
        "active",
        "degraded",
        "disabled",
        "rolled_back",
    }
)
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{2,127}$")
_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$")


class ResolverCapabilityError(ValueError):
    """Raised when a resolver capability contract is invalid."""


@dataclass(frozen=True)
class ResolverCapabilityManifest:
    capability_id: str
    version: str
    supported_hosts: tuple[str, ...]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    dependencies: Mapping[str, Any]
    network_policy: Mapping[str, Any]
    timeouts: Mapping[str, int]
    response_bounds: Mapping[str, int]
    authentication: Mapping[str, Any]
    test_fixtures: tuple[str, ...]
    risk: str
    approvals: Mapping[str, Any]
    deploy: Mapping[str, Any]
    verify: Mapping[str, Any]
    rollback: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ResolverCapabilityManifest":
        if not isinstance(data, Mapping) or data.get("schema") != MANIFEST_SCHEMA:
            raise ResolverCapabilityError("resolver capability manifest schema is invalid")
        capability_id = _token(data.get("capability_id"), "capability_id")
        version = _semver(data.get("version"))
        supported_hosts = tuple(_string_list(data.get("supported_hosts"), "supported_hosts", min_items=1))
        manifest = cls(
            capability_id=capability_id,
            version=version,
            supported_hosts=supported_hosts,
            input_schema=_mapping(data.get("input_schema"), "input_schema"),
            output_schema=_mapping(data.get("output_schema"), "output_schema"),
            dependencies=_mapping(data.get("dependencies"), "dependencies"),
            network_policy=_mapping(data.get("network_policy"), "network_policy"),
            timeouts={str(k): _int(v, k, 1, 86_400) for k, v in _mapping(data.get("timeouts"), "timeouts").items()},
            response_bounds={
                str(k): _int(v, k, 1, 10_485_760)
                for k, v in _mapping(data.get("response_bounds"), "response_bounds").items()
            },
            authentication=_mapping(data.get("authentication"), "authentication"),
            test_fixtures=tuple(_string_list(data.get("test_fixtures"), "test_fixtures", min_items=1)),
            risk=_enum(data.get("risk"), "risk", {"low", "medium", "high"}),
            approvals=_mapping(data.get("approvals"), "approvals"),
            deploy=_operation(data.get("deploy"), "deploy"),
            verify=_operation(data.get("verify"), "verify"),
            rollback=_operation(data.get("rollback"), "rollback"),
        )
        manifest.validate_policy()
        return manifest

    def validate_policy(self) -> None:
        if self.network_policy.get("ssrf_guard") is not True:
            raise ResolverCapabilityError("network_policy.ssrf_guard must be true")
        forbidden = {str(item).lower() for item in self.network_policy.get("forbidden_headers", ())}
        if not {"cookie", "authorization"}.issubset(forbidden):
            raise ResolverCapabilityError("network policy must forbid cookie and authorization headers")
        if self.network_policy.get("allow_signed_media_urls") is not False:
            raise ResolverCapabilityError("signed media URLs must not be exchanged")
        if self.authentication.get("required") is not False or self.authentication.get("secret_refs"):
            raise ResolverCapabilityError("resolver capability exchange must not require shared secrets")
        if not self.approvals.get("merge"):
            raise ResolverCapabilityError("merge approval reference is required")
        if self.approvals.get("production_activation") in {"", None}:
            return
        _token(self.approvals.get("production_activation"), "production_activation")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "capability_id": self.capability_id,
            "version": self.version,
            "supported_hosts": list(self.supported_hosts),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "dependencies": dict(self.dependencies),
            "network_policy": dict(self.network_policy),
            "timeouts": dict(self.timeouts),
            "response_bounds": dict(self.response_bounds),
            "authentication": dict(self.authentication),
            "test_fixtures": list(self.test_fixtures),
            "risk": self.risk,
            "approvals": dict(self.approvals),
            "deploy": dict(self.deploy),
            "verify": dict(self.verify),
            "rollback": dict(self.rollback),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_mapping(), allow_nan=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CanonicalCapabilityRecord:
    capability_id: str
    version: str
    supported_hosts: tuple[str, ...]
    package_hash: str
    manifest_hash: str
    status: str = "approved"
    operations: Mapping[str, str] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": CANON_SCHEMA,
            "capability_id": self.capability_id,
            "version": self.version,
            "supported_hosts": list(self.supported_hosts),
            "package_hash": self.package_hash,
            "manifest_hash": self.manifest_hash,
            "status": self.status,
            "operations": dict(self.operations),
        }


@dataclass(frozen=True)
class CapabilityStateRecord:
    capability_id: str
    node_id: str
    status: str
    active_version: str | None = None
    package_hash: str | None = None
    rollback_version: str | None = None
    last_success: str | None = None
    last_failure: str | None = None
    deployment_receipts: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUS_VALUES:
            raise ResolverCapabilityError(f"invalid capability state: {self.status}")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA,
            "capability_id": self.capability_id,
            "node_id": self.node_id,
            "status": self.status,
            "active_version": self.active_version,
            "package_hash": self.package_hash,
            "rollback_version": self.rollback_version,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "deployment_receipts": [dict(receipt) for receipt in self.deployment_receipts],
        }


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResolverCapabilityError(f"{field} must be an object")
    return value


def _string_list(value: object, field: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < min_items or any(not isinstance(item, str) or not item for item in value):
        raise ResolverCapabilityError(f"{field} must be a non-empty string list")
    return list(value)


def _token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise ResolverCapabilityError(f"{field} is malformed")
    return value


def _semver(value: object) -> str:
    if not isinstance(value, str) or not _SEMVER_RE.fullmatch(value):
        raise ResolverCapabilityError("version must be semantic")
    return value


def _enum(value: object, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ResolverCapabilityError(f"{field} is invalid")
    return value


def _int(value: object, field: object, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise ResolverCapabilityError(f"{field} must be a bounded integer")
    return value


def _operation(value: object, field: str) -> Mapping[str, Any]:
    data = _mapping(value, field)
    _token(data.get("operation_id"), f"{field}.operation_id")
    if data.get("executor") not in {"home_edge_exec", "runner"}:
        raise ResolverCapabilityError(f"{field}.executor is invalid")
    _string_list(data.get("args"), f"{field}.args")
    return data

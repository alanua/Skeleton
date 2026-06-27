from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping


MEMORY_GATEWAY_POLICY_SCHEMA = "skeleton.memory_gateway.policy.v1"

ALLOWED_NAMESPACES = frozenset(
    {
        "aufmass",
        "bauclock",
        "skeleton",
        "home_automation",
        "legal_private",
    }
)

ALLOWED_COMMAND_SUFFIXES = frozenset(
    {
        "memory.lookup_exact",
        "memory.search_semantic",
        "memory.get_conflicts",
        "memory.get_override_history",
        "memory.get_audit_log",
        "memory.get_index_freshness",
        "graph.query_code",
        "graph.get_index_freshness",
        "memory.propose_patch",
    }
)

PUBLIC_MODE_FORBIDDEN_NAMESPACES = frozenset({"legal_private"})
SEMANTIC_RESULT_NOT_CANON_CONFIRMED = "SEMANTIC_RESULT_NOT_CANON_CONFIRMED"
GRAPH_RESULT_NOT_CANON_CONFIRMED = "GRAPH_RESULT_NOT_CANON_CONFIRMED"
STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE = "STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE"
EXACT_CONFIRMATION_REVISION_MISMATCH = "EXACT_CONFIRMATION_REVISION_MISMATCH"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_PUBLIC_MARKERS = (
    "/",
    "\\",
    "file:",
    ".sqlite",
    ".db",
    "secret",
    "token",
    "password",
    "credential",
    "private-value",
)


class MemoryGatewayPolicyError(ValueError):
    """Raised when gateway policy rejects a request fail-closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def command_name(namespace: str, suffix: str) -> str:
    namespace = validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
    if suffix not in ALLOWED_COMMAND_SUFFIXES:
        raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command suffix is not allowlisted")
    return f"{namespace}.{suffix}"


def split_command(command: str) -> tuple[str, str]:
    if not isinstance(command, str) or "." not in command:
        raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command must be namespace-qualified")
    namespace, suffix = command.split(".", 1)
    validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
    if suffix not in ALLOWED_COMMAND_SUFFIXES:
        raise MemoryGatewayPolicyError("COMMAND_NOT_ALLOWLISTED", "command is not allowlisted")
    return namespace, suffix


def validate_namespace(namespace: object, *, allowed_namespaces: frozenset[str]) -> str:
    if not isinstance(namespace, str) or not namespace:
        raise MemoryGatewayPolicyError("NAMESPACE_REQUIRED", "namespace is mandatory")
    if namespace == "*" or "*" in namespace:
        raise MemoryGatewayPolicyError("WILDCARD_NAMESPACE_FORBIDDEN", "wildcard namespace access is forbidden")
    if not _SAFE_TOKEN_RE.fullmatch(namespace):
        raise MemoryGatewayPolicyError("INVALID_NAMESPACE", "namespace is malformed")
    if namespace not in ALLOWED_NAMESPACES:
        raise MemoryGatewayPolicyError("UNKNOWN_NAMESPACE", "namespace is not registered")
    if namespace not in allowed_namespaces:
        raise MemoryGatewayPolicyError("NAMESPACE_NOT_AUTHORIZED", "namespace is not authorized by capability token")
    return namespace


def validate_public_payload(value: Any) -> Any:
    sanitized = deepcopy(value)
    _reject_unsafe_public_value(sanitized, "payload")
    return sanitized


def sanitized_actor_ref(actor_ref: object) -> str:
    if not isinstance(actor_ref, str) or not _SAFE_TOKEN_RE.fullmatch(actor_ref):
        raise MemoryGatewayPolicyError("INVALID_ACTOR_REF", "actor ref must be a safe token")
    return actor_ref


def sanitized_reason_code(reason_code: object) -> str:
    if not isinstance(reason_code, str) or not _SAFE_TOKEN_RE.fullmatch(reason_code):
        raise MemoryGatewayPolicyError("INVALID_REASON_CODE", "reason code must be a safe token")
    return reason_code


def _reject_unsafe_public_value(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or not _SAFE_TOKEN_RE.fullmatch(key):
                raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains unsafe key")
            if key.lower() in {
                "content",
                "raw_content",
                "raw_path",
                "local_path",
                "path",
                "secret",
                "token",
                "password",
                "credential",
                "storage_api",
            }:
                raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains unsafe field")
            _reject_unsafe_public_value(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_unsafe_public_value(child, f"{path}[{index}]")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_MARKERS):
            raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains private-looking value")
        return
    raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} is not JSON-safe")

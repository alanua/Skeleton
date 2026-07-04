from __future__ import annotations

import math
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
        "memory.prepare_canonical_manifest",
        "memory.import_canonical_manifest",
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

MAX_PUBLIC_PAYLOAD_DEPTH = 32
MAX_PUBLIC_PAYLOAD_NODES = 10_000
MAX_PUBLIC_PAYLOAD_KEYS = 5_000
MAX_PUBLIC_STRING_BYTES = 1_048_576
MAX_PUBLIC_KEY_BYTES = 4_096

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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
    """Validate bounded JSON structure without inspecting words or topics.

    Memory content is never rejected because of lexical markers, path-like text,
    file extensions, or subject matter. Public safety must be enforced by the
    producing operation's explicit response schema and authorization boundary.
    """

    sanitized = deepcopy(value)
    counters = {"nodes": 0, "keys": 0, "string_bytes": 0}
    _validate_bounded_json(sanitized, path="payload", depth=0, counters=counters)
    return sanitized


def sanitized_actor_ref(actor_ref: object) -> str:
    if not isinstance(actor_ref, str) or not _SAFE_TOKEN_RE.fullmatch(actor_ref):
        raise MemoryGatewayPolicyError("INVALID_ACTOR_REF", "actor ref must be a safe token")
    return actor_ref


def sanitized_reason_code(reason_code: object) -> str:
    if not isinstance(reason_code, str) or not _SAFE_TOKEN_RE.fullmatch(reason_code):
        raise MemoryGatewayPolicyError("INVALID_REASON_CODE", "reason code must be a safe token")
    return reason_code


def _validate_bounded_json(
    value: Any,
    *,
    path: str,
    depth: int,
    counters: dict[str, int],
) -> None:
    if depth > MAX_PUBLIC_PAYLOAD_DEPTH:
        raise MemoryGatewayPolicyError("PUBLIC_PAYLOAD_LIMIT_EXCEEDED", f"{path} exceeds maximum depth")

    counters["nodes"] += 1
    if counters["nodes"] > MAX_PUBLIC_PAYLOAD_NODES:
        raise MemoryGatewayPolicyError("PUBLIC_PAYLOAD_LIMIT_EXCEEDED", "payload exceeds maximum node count")

    if isinstance(value, Mapping):
        counters["keys"] += len(value)
        if counters["keys"] > MAX_PUBLIC_PAYLOAD_KEYS:
            raise MemoryGatewayPolicyError("PUBLIC_PAYLOAD_LIMIT_EXCEEDED", "payload exceeds maximum key count")
        for key, child in value.items():
            if not isinstance(key, str):
                raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains non-string key")
            if len(key.encode("utf-8")) > MAX_PUBLIC_KEY_BYTES:
                raise MemoryGatewayPolicyError("PUBLIC_PAYLOAD_LIMIT_EXCEEDED", f"{path} contains oversized key")
            _validate_bounded_json(child, path=f"{path}[{key!r}]", depth=depth + 1, counters=counters)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_bounded_json(child, path=f"{path}[{index}]", depth=depth + 1, counters=counters)
        return

    if value is None or isinstance(value, (bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} contains non-finite number")
        return

    if isinstance(value, str):
        counters["string_bytes"] += len(value.encode("utf-8"))
        if counters["string_bytes"] > MAX_PUBLIC_STRING_BYTES:
            raise MemoryGatewayPolicyError("PUBLIC_PAYLOAD_LIMIT_EXCEEDED", "payload exceeds maximum string bytes")
        return

    raise MemoryGatewayPolicyError("UNSAFE_PUBLIC_PAYLOAD", f"{path} is not JSON-safe")

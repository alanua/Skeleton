from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TASK_PACKET_SCHEMA_PATH = ROOT / "schemas" / "hermes_task_packet.schema.json"

ROUTE_ALIASES = {
    "LOW": {
        "provider_role": "bulk_worker",
        "provider_example": "low-cost public-safe worker example",
    },
    "MID": {
        "provider_role": "planner",
        "provider_example": "planner-class model example",
    },
    "HIGH": {
        "provider_role": "critic",
        "provider_example": "independent auditor model example",
    },
}

FORBIDDEN_ACTIONS = (
    "server_install",
    "runtime_service_change",
    "workflow_change",
    "protected_file_change",
    "private_data_access",
    "secret_access",
    "queue_mutation",
    "issue_mutation",
    "merge",
    "deploy",
    "publish",
    "host_maintenance",
    "canon_promotion",
)

PUBLIC_TEXT_FIELDS = (
    "task_id",
    "title",
    "goal",
    "scope",
    "source_context",
    "notes",
)

PRIVATE_OR_MUTATING_FIELD_NAMES = re.compile(
    r"(?i)(secret|token|credential|password|private|api[_-]?key|cookie|"
    r"env|environment|path|url|link|drive|file_id|mutation|mutating|"
    r"execute|deploy|merge|publish|install|runtime|workflow)"
)
URL_LIKE = re.compile(r"(?i)\b(?:https?://|www\.|drive\.google\.com|docs\.google\.com)")
PATH_LIKE = re.compile(
    r"(?i)(?:^|[\s:=])(?:/[A-Za-z0-9._-]+){2,}|[A-Za-z]:\\|\\\\|(?:^|[\s:=])~/"
)
SECRET_LIKE = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]+|api[_-]?key|token|secret|password)\b")

MIN_BUDGET_UNITS = 1
MAX_BUDGET_UNITS = 1_000
MIN_RETRIES = 0
MAX_RETRIES = 3
MAX_TOKEN_CAP = 200_000
MAX_CHUNK_TOKEN_CAP = 20_000


class HermesTaskRouterError(ValueError):
    """Raised when a public-safe task packet cannot be routed."""


def build_hermes_task_packet(
    *,
    task_id: str,
    title: str,
    goal: str,
    route_alias: str,
    budget_units: int,
    max_retries: int,
    token_cap: int,
    chunk_token_cap: int,
    source_reference: str = "synthetic public-safe routing request",
    scope: tuple[str, ...] = ("public-safe provider routing contract",),
    allowed_files: tuple[str, ...] = (
        "core/hermes_task_router.py",
        "schemas/hermes_task_packet.schema.json",
        "tests/test_hermes_task_router.py",
        "docs/HERMES_PROVIDER_ROUTING.md",
    ),
    notes: str | None = None,
) -> dict[str, Any]:
    """Build a static, schema-valid, public-safe Hermes task packet.

    The route alias is policy metadata only. This function does not call,
    configure, or select any live provider.
    """

    alias = _validate_route_alias(route_alias)
    route = ROUTE_ALIASES[alias]

    input_fields: dict[str, object] = {
        "task_id": task_id,
        "title": title,
        "goal": goal,
        "source_context": source_reference,
        "scope": list(scope),
    }
    if notes is not None:
        input_fields["notes"] = notes
    _validate_public_input_fields(input_fields)
    _validate_budget_bounds(
        budget_units=budget_units,
        max_retries=max_retries,
        token_cap=token_cap,
        chunk_token_cap=chunk_token_cap,
    )
    _validate_allowed_repo_paths(allowed_files)

    packet: dict[str, Any] = {
        "schema": "hermes.task_packet.v0",
        "task_id": task_id,
        "title": title,
        "goal": goal,
        "worker_mode": "contract_test",
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "approval_required": True,
        "source_context": [
            {
                "source_type": "operator_context",
                "reference": source_reference,
                "public_safe": True,
                "read_only": True,
            }
        ],
        "scope": list(scope),
        "allowed_files": list(allowed_files),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "validation": [
            {
                "command": "python3 -m pytest -q tests/test_hermes_task_router.py",
                "purpose": "Validate synthetic public-safe router regressions.",
                "mutating": False,
            },
            {
                "command": "git diff --check",
                "purpose": "Validate patch whitespace.",
                "mutating": False,
            },
        ],
        "expected_outputs": [
            "public_safe_contract",
            "task_packet_schema",
            "contract_tests",
            "draft_pr",
        ],
        "authority_boundary": {
            "review_only": True,
            "mutation_allowed": False,
            "runtime_install_allowed": False,
            "approval_path": "authorized operator or reviewed process",
        },
        "provider_route": {
            "route_alias": alias,
            "provider_role": route["provider_role"],
            "provider_example": route["provider_example"],
            "provider_example_only": True,
            "live_route_enabled": False,
            "budget_units": budget_units,
            "token_cap": token_cap,
            "chunk_token_cap": chunk_token_cap,
            "max_retries": max_retries,
        },
    }
    if notes is not None:
        packet["notes"] = notes

    validate_hermes_task_packet(packet)
    return packet


def validate_hermes_task_packet(packet: dict[str, Any]) -> None:
    """Validate an emitted packet against the checked-in task packet schema."""

    schema = json.loads(TASK_PACKET_SCHEMA_PATH.read_text(encoding="utf-8"))
    _validate_object(schema, packet, path="packet", root_schema=schema)


def _validate_route_alias(route_alias: str) -> str:
    if not isinstance(route_alias, str):
        raise HermesTaskRouterError("route_alias must be a string")
    alias = route_alias.upper()
    if alias not in ROUTE_ALIASES:
        raise HermesTaskRouterError("route_alias must be one of LOW, MID, HIGH")
    return alias


def _validate_public_input_fields(fields: dict[str, object]) -> None:
    for name in fields:
        if name not in PUBLIC_TEXT_FIELDS:
            raise HermesTaskRouterError(f"unsupported public task field: {name}")
        if PRIVATE_OR_MUTATING_FIELD_NAMES.search(name):
            raise HermesTaskRouterError(f"unsafe public task field name: {name}")
    _validate_public_value(fields)


def _validate_public_value(value: object) -> None:
    if isinstance(value, str):
        if value.strip() == "":
            raise HermesTaskRouterError("public text fields must not be empty")
        if URL_LIKE.search(value):
            raise HermesTaskRouterError("URL-like public task field rejected")
        if PATH_LIKE.search(value):
            raise HermesTaskRouterError("path-like public task field rejected")
        if SECRET_LIKE.search(value):
            raise HermesTaskRouterError("secret-like public task field rejected")
        return
    if isinstance(value, list) or isinstance(value, tuple):
        if not value:
            raise HermesTaskRouterError("public task lists must not be empty")
        for item in value:
            _validate_public_value(item)
        return
    if isinstance(value, dict):
        for child_name, child_value in value.items():
            if not isinstance(child_name, str):
                raise HermesTaskRouterError("public task field names must be strings")
            if PRIVATE_OR_MUTATING_FIELD_NAMES.search(child_name):
                raise HermesTaskRouterError(f"unsafe public task field name: {child_name}")
            _validate_public_value(child_value)
        return
    raise HermesTaskRouterError("public task fields must be strings or string lists")


def _validate_budget_bounds(
    *,
    budget_units: int,
    max_retries: int,
    token_cap: int,
    chunk_token_cap: int,
) -> None:
    bounds = (
        ("budget_units", budget_units, MIN_BUDGET_UNITS, MAX_BUDGET_UNITS),
        ("max_retries", max_retries, MIN_RETRIES, MAX_RETRIES),
        ("token_cap", token_cap, 1, MAX_TOKEN_CAP),
        ("chunk_token_cap", chunk_token_cap, 1, MAX_CHUNK_TOKEN_CAP),
    )
    for name, value, minimum, maximum in bounds:
        if not isinstance(value, int) or isinstance(value, bool):
            raise HermesTaskRouterError(f"{name} must be an integer")
        if value < minimum or value > maximum:
            raise HermesTaskRouterError(f"{name} must be between {minimum} and {maximum}")
    if chunk_token_cap > token_cap:
        raise HermesTaskRouterError("chunk_token_cap must not exceed token_cap")


def _validate_allowed_repo_paths(paths: tuple[str, ...]) -> None:
    if not paths:
        raise HermesTaskRouterError("allowed_files must not be empty")
    for path in paths:
        if not isinstance(path, str) or path.strip() == "":
            raise HermesTaskRouterError("allowed_files entries must be non-empty strings")
        if path.startswith(("/", "~")) or "\\" in path or ".." in path.split("/"):
            raise HermesTaskRouterError("allowed_files must be relative repository paths")


def _validate_object(
    schema: dict[str, Any],
    value: object,
    *,
    path: str,
    root_schema: dict[str, Any],
) -> None:
    if schema.get("type") == "object":
        if not isinstance(value, dict):
            raise HermesTaskRouterError(f"{path} must be an object")
        required = set(schema.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            raise HermesTaskRouterError(f"{path} missing required fields: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(schema.get("properties", {})))
            if extra:
                raise HermesTaskRouterError(f"{path} has unsupported fields: {', '.join(extra)}")
        for key, child_value in value.items():
            child_schema = schema.get("properties", {}).get(key)
            if child_schema is not None:
                _validate_object(
                    _resolve_ref(root_schema, child_schema),
                    child_value,
                    path=f"{path}.{key}",
                    root_schema=root_schema,
                )
        return

    if schema.get("type") == "array":
        if not isinstance(value, list):
            raise HermesTaskRouterError(f"{path} must be an array")
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            raise HermesTaskRouterError(f"{path} must contain at least {min_items} item(s)")
        if schema.get("uniqueItems") is True and len(value) != len({json.dumps(item, sort_keys=True) for item in value}):
            raise HermesTaskRouterError(f"{path} must contain unique items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_object(
                    _resolve_ref(root_schema, item_schema),
                    item,
                    path=f"{path}[{index}]",
                    root_schema=root_schema,
                )
        return

    if "const" in schema and value != schema["const"]:
        raise HermesTaskRouterError(f"{path} must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise HermesTaskRouterError(f"{path} has unsupported value {value!r}")
    if "type" in schema and not _matches_type(value, schema["type"]):
        raise HermesTaskRouterError(f"{path} must be {schema['type']}")
    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise HermesTaskRouterError(f"{path} must not be empty")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise HermesTaskRouterError(f"{path} does not match required pattern")
    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise HermesTaskRouterError(f"{path} must be >= {minimum}")
        if isinstance(maximum, int) and value > maximum:
            raise HermesTaskRouterError(f"{path} must be <= {maximum}")


def _resolve_ref(root_schema: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    if not ref.startswith("#/$defs/"):
        raise HermesTaskRouterError(f"unsupported schema ref: {ref}")
    name = ref.removeprefix("#/$defs/")
    return root_schema["$defs"][name]


def _matches_type(value: object, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True

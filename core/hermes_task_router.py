from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ROUTE_ALIASES = {
    "LOW": "AUFMASS_WORKER_LOW",
    "MID": "AUFMASS_REVIEW_MID",
    "HIGH": "AUFMASS_EXPERT_HIGH",
}

ROUTE_RANK = {"LOW": 1, "MID": 2, "HIGH": 3}
ALIAS_TO_ROUTE = {alias: route for route, alias in ROUTE_ALIASES.items()}

PUBLIC_SAFE_PRIVACY_CLASSES = {"public", "public_safe", "synthetic", "sanitized"}
WEAK_EVIDENCE_QUALITY = {"missing", "insufficient", "weak", "contradictory"}

LOW_TASK_TYPES = {
    "candidate_generation",
    "deterministic_extraction",
    "normalization",
    "repetitive_calculation",
}

MID_TASK_TYPES = {
    "contradiction_check",
    "evidence_review",
    "geometry_review",
    "rework_instruction",
    "tolerance_decision",
}

HIGH_TASK_TYPES = {
    "final_expert_adjudication",
    "method_approval",
    "method_rejection",
    "unresolved_high_impact_ambiguity",
}


class HermesTaskRouterError(ValueError):
    """Base error for public-safe Hermes routing failures."""


class RouteUnavailableError(HermesTaskRouterError):
    """Raised when the required public alias is unavailable."""


class UnsafeRouteDowngradeError(HermesTaskRouterError):
    """Raised when a requested alias would downgrade the required route."""


def route_hermes_task(
    task: Mapping[str, Any],
    *,
    available_aliases: set[str] | None = None,
    requested_alias: str | None = None,
) -> dict[str, Any]:
    """Return a public-safe Hermes task packet with a validated route alias.

    The router is intentionally local and static. It does not load provider
    names, secrets, runtime configuration, or external model clients.
    """

    classification = classify_hermes_task(task)
    alias = _select_alias(
        route=classification["route"],
        requested_alias=requested_alias or _string(task.get("requested_alias")),
        available_aliases=available_aliases,
    )

    route = {
        "classification": classification["route"],
        "alias": alias,
        "transition": classification["transition"],
        "reason_codes": classification["reason_codes"],
        "budget": _budget(task.get("budget")),
        "retry": _retry(task.get("retry_count"), task.get("max_retries")),
        "evidence": _evidence(task),
        "privacy": _privacy(task.get("privacy_class")),
        "operator_approval": _operator_approval(task),
    }

    return {
        "schema": "hermes.task_packet.v0",
        "task_id": _required_string(task, "task_id"),
        "title": _required_string(task, "title"),
        "goal": _required_string(task, "goal"),
        "worker_mode": "review_only",
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "approval_required": True,
        "source_context": _source_context(task),
        "scope": _string_list(task.get("scope"), ["public-safe synthetic routing"]),
        "allowed_files": _string_list(task.get("allowed_files"), []),
        "forbidden_actions": [
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
        ],
        "validation": _validation(task),
        "expected_outputs": _string_list(task.get("expected_outputs"), ["draft_pr"]),
        "authority_boundary": {
            "review_only": True,
            "mutation_allowed": False,
            "runtime_install_allowed": False,
            "approval_path": "operator approval required before any live provider use",
        },
        "route": route,
    }


def classify_hermes_task(task: Mapping[str, Any]) -> dict[str, Any]:
    task_type = _string(task.get("task_type")) or "unknown"
    ambiguity = _string(task.get("ambiguity")) or "low"
    impact = _string(task.get("impact")) or "low"
    evidence_quality = _string(task.get("evidence_quality")) or "sufficient"
    privacy_class = _string(task.get("privacy_class")) or "synthetic"
    retry_count = _int(task.get("retry_count"), 0)
    max_retries = _int(task.get("max_retries"), 2)
    operator_gate = _bool(task.get("operator_gate"), False)

    route = _route_for_task_type(task_type)
    reason_codes = [f"task_type:{task_type}"]

    if ambiguity in {"medium", "moderate"}:
        route = _max_route(route, "MID")
        reason_codes.append("ambiguity:mid")
    elif ambiguity in {"high", "unresolved"}:
        route = _max_route(route, "HIGH" if impact == "high" else "MID")
        reason_codes.append(f"ambiguity:{ambiguity}")

    if impact == "high" and ambiguity in {"high", "unresolved"}:
        route = "HIGH"
        reason_codes.append("impact:high_with_unresolved_ambiguity")
    elif impact in {"medium", "moderate"} and route == "LOW":
        route = "MID"
        reason_codes.append("impact:mid")

    if evidence_quality in WEAK_EVIDENCE_QUALITY:
        route = _max_route(route, "MID")
        reason_codes.append(f"evidence:{evidence_quality}")

    if privacy_class not in PUBLIC_SAFE_PRIVACY_CLASSES:
        route = "HIGH"
        reason_codes.append("privacy:requires_operator_boundary")

    if retry_count >= max_retries:
        route = _max_route(route, "MID")
        reason_codes.append("retry:cap_reached")

    if operator_gate:
        route = "HIGH"
        reason_codes.append("operator_gate:required")

    transition = _transition(task, route, evidence_quality, privacy_class)
    return {
        "route": route,
        "alias": ROUTE_ALIASES[route],
        "transition": transition,
        "reason_codes": reason_codes,
    }


def _route_for_task_type(task_type: str) -> str:
    if task_type in LOW_TASK_TYPES:
        return "LOW"
    if task_type in MID_TASK_TYPES:
        return "MID"
    if task_type in HIGH_TASK_TYPES:
        return "HIGH"
    return "MID"


def _transition(
    task: Mapping[str, Any], route: str, evidence_quality: str, privacy_class: str
) -> str:
    previous_route = _string(task.get("previous_route"))

    if route == "HIGH" and (
        evidence_quality in {"missing", "insufficient"} or privacy_class == "private"
    ):
        return "HIGH->REQUEST_EVIDENCE"
    if previous_route == "MID" and _bool(task.get("rework_ready"), False):
        return "MID->LOW_REWORK"
    if previous_route == "MID" and route == "HIGH":
        return "MID->HIGH"
    if previous_route == "LOW" and route == "MID":
        return "LOW->MID"
    return "STAY"


def _select_alias(
    *,
    route: str,
    requested_alias: str | None,
    available_aliases: set[str] | None,
) -> str:
    required_alias = ROUTE_ALIASES[route]
    alias = requested_alias or required_alias

    if alias not in ALIAS_TO_ROUTE:
        raise RouteUnavailableError(f"unknown_route_alias:{alias}")

    requested_route = ALIAS_TO_ROUTE[alias]
    if ROUTE_RANK[requested_route] < ROUTE_RANK[route]:
        raise UnsafeRouteDowngradeError(
            f"requested_alias:{alias}:downgrades_required_route:{required_alias}"
        )

    if available_aliases is not None and alias not in available_aliases:
        raise RouteUnavailableError(f"route_alias_unavailable:{alias}")

    return alias


def _budget(value: object) -> dict[str, Any]:
    reader = value if isinstance(value, Mapping) else {}
    return {
        "budget_units": _int(reader.get("budget_units"), 1),
        "token_cap": _int(reader.get("token_cap"), 2000),
        "live_route_enabled": False,
    }


def _retry(retry_count: object, max_retries: object) -> dict[str, int]:
    return {
        "count": _int(retry_count, 0),
        "max": _int(max_retries, 2),
    }


def _evidence(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "quality": _string(task.get("evidence_quality")) or "sufficient",
        "items": _string_list(task.get("evidence"), ["synthetic_evidence_metadata"]),
        "contains_real_artifacts": False,
    }


def _privacy(value: object) -> dict[str, Any]:
    privacy_class = _string(value) or "synthetic"
    return {
        "class": privacy_class,
        "public_safe": privacy_class in PUBLIC_SAFE_PRIVACY_CLASSES,
        "no_secrets": True,
    }


def _operator_approval(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "required": True,
        "gate": _bool(task.get("operator_gate"), False),
        "approved": False,
    }


def _source_context(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_context = task.get("source_context")
    if isinstance(source_context, list) and source_context:
        return source_context
    return [
        {
            "source_type": "other_public_safe_source",
            "reference": "synthetic public-safe task descriptor",
            "public_safe": True,
            "read_only": True,
        }
    ]


def _validation(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    validation = task.get("validation")
    if isinstance(validation, list) and validation:
        return validation
    return [
        {
            "command": "python3 -m pytest -q tests/test_hermes_task_router.py",
            "purpose": "Validate synthetic public-safe Hermes routing behavior.",
            "mutating": False,
        }
    ]


def _max_route(left: str, right: str) -> str:
    return left if ROUTE_RANK[left] >= ROUTE_RANK[right] else right


def _required_string(task: Mapping[str, Any], key: str) -> str:
    value = _string(task.get(key))
    if value:
        return value
    raise HermesTaskRouterError(f"missing_required_field:{key}")


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _string_list(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    strings = [item for item in value if isinstance(item, str) and item]
    return strings or default


def _int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default

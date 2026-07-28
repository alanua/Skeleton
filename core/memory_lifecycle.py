from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_policy import MemoryGatewayPolicyError, command_name, validate_public_payload
from core.private_memory_history import canonical_json, content_hash, safe_token


MEMORY_LIFECYCLE_EVENT_SCHEMA = "skeleton.memory_lifecycle.event.v1"
MEMORY_LIFECYCLE_RECALL_SCHEMA = "skeleton.memory_lifecycle.recall_result.v1"
MEMORY_LIFECYCLE_CAPTURE_SCHEMA = "skeleton.memory_lifecycle.capture_result.v1"
MEMORY_LIFECYCLE_PUBLIC_RECEIPT_SCHEMA = "skeleton.memory_lifecycle.public_receipt.v1"
MEMORY_LIFECYCLE_CANONICAL_VALUE_SCHEMA = "skeleton.memory_lifecycle.canonical_value.v1"

MAX_RECALL_RECORDS = 10
MAX_RECALL_CHARS = 6000

_GATEWAY_NAMESPACE = "skeleton"
_SUPPORTED_DOMAINS: dict[str, str] = {
    "skeleton": "skeleton.context",
    "core": "skeleton.context",
    "life_archive": "life_archive.context",
    "home_edge": "home_edge.devices",
    "home_devices": "home_edge.devices",
    "documents": "documents.metadata",
    "mfp": "documents.metadata",
    "travel": "travel.context",
    "dios": "dios.aufmass",
    "aufmass": "aufmass.context",
    "gewerbe": "gewerbe.bauclock",
    "bauclock": "gewerbe.bauclock",
    "runner": "runner.context",
}
_DURABLE_EVENTS = frozenset(
    {
        "meaningful_input",
        "decision",
        "observation",
        "artifact_ingestion",
        "device_event",
        "completed_action",
        "remember_this",
    }
)
_DURABLE_CLASSES = frozenset(
    {
        "preference",
        "identity_context_fact",
        "confirmed_decision",
        "configuration",
        "relationship",
        "project_state",
        "document_metadata",
        "device_state_change",
        "completed_action_outcome",
    }
)
_REJECTED_PRIVACY_CLASSES = frozenset({"secret", "credential", "raw_high_volume_telemetry"})
_ALLOWED_PRIVACY_CLASSES = frozenset(
    {"public_safe", "internal", "private", "sensitive", "operator_private"}
) | _REJECTED_PRIVACY_CLASSES


class MemoryLifecycleError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MemoryScope:
    operator_id: str
    domain: str
    project_id: str
    dataset_id: str
    fact_namespace: str
    privacy_class: str


@dataclass(frozen=True)
class MemoryLifecycleResult:
    schema: str
    status: str
    private_payload: dict[str, Any]
    receipt: dict[str, object]

    def public_receipt(self) -> dict[str, object]:
        return dict(self.receipt)


def resolve_memory_scope(
    *,
    operator_id: str,
    domain: str,
    project_id: str = "skeleton",
    dataset_id: str = "default",
    privacy_class: str = "private",
    fact_namespace: str | None = None,
) -> MemoryScope:
    operator = safe_token(operator_id, "operator_id")
    domain_token = safe_token(domain, "domain")
    if domain_token not in _SUPPORTED_DOMAINS:
        raise MemoryLifecycleError("UNKNOWN_DOMAIN_SCOPE", "domain memory scope is not registered")
    project = safe_token(project_id, "project_id")
    dataset = safe_token(dataset_id, "dataset_id")
    if project != "skeleton":
        raise MemoryLifecycleError("PROJECT_SCOPE_NOT_AUTHORIZED", "private canonical gateway scope is local skeleton")
    if not isinstance(privacy_class, str) or privacy_class not in _ALLOWED_PRIVACY_CLASSES:
        raise MemoryLifecycleError("UNKNOWN_PRIVACY_CLASS", "privacy class is not registered")
    privacy = privacy_class
    registered_namespace = _SUPPORTED_DOMAINS[domain_token]
    namespace = safe_token(fact_namespace or registered_namespace, "fact_namespace")
    if namespace != registered_namespace:
        raise MemoryLifecycleError("UNKNOWN_NAMESPACE_SCOPE", "domain namespace scope is not registered")
    return MemoryScope(
        operator_id=operator,
        domain=domain_token,
        project_id=project,
        dataset_id=dataset,
        fact_namespace=namespace,
        privacy_class=privacy,
    )


def recall_before_task(
    gateway: MemoryGateway,
    *,
    operator_id: str,
    domain: str,
    task_route: str,
    query: str,
    project_id: str = "skeleton",
    dataset_id: str = "default",
    privacy_class: str = "private",
    limit: int = MAX_RECALL_RECORDS,
    max_chars: int = MAX_RECALL_CHARS,
) -> MemoryLifecycleResult:
    scope = resolve_memory_scope(
        operator_id=operator_id,
        domain=domain,
        project_id=project_id,
        dataset_id=dataset_id,
        privacy_class=privacy_class,
    )
    route = safe_token(task_route, "task_route")
    bounded_limit = _bounded_limit(limit, MAX_RECALL_RECORDS)
    bounded_chars = max(0, min(int(max_chars), MAX_RECALL_CHARS))
    status_payload = _gateway_payload(gateway, "memory.private_status", {"project_id": scope.project_id, "dataset_id": scope.dataset_id})
    canonical_revision = _canonical_revision(status_payload)
    if status_payload.get("state") != "READY" or bounded_limit == 0:
        return _recall_result(scope, route, canonical_revision, [], [], status="UNAVAILABLE" if bounded_limit else "DONE")

    candidate_refs = _candidate_refs(
        gateway,
        scope=scope,
        query=query,
        limit=bounded_limit,
    )
    selected_public: list[dict[str, object]] = []
    private_values: list[dict[str, Any]] = []
    rendered_chars = 0
    truncated = False
    for canonical_ref in candidate_refs:
        if len(private_values) >= bounded_limit:
            truncated = True
            break
        exact = _gateway_payload(
            gateway,
            "memory.private_read_exact",
            {"project_id": scope.project_id, "dataset_id": scope.dataset_id, "canonical_ref": canonical_ref},
        )
        if not str(exact.get("canonical_ref", "")).startswith(scope.fact_namespace + ":"):
            continue
        rendered = canonical_json(exact.get("value"))
        remaining = bounded_chars - rendered_chars
        if remaining <= 0:
            truncated = True
            break
        if len(rendered) > remaining:
            truncated = True
            continue
        rendered_chars += len(rendered)
        selected_public.append(
            {
                "canonical_ref": str(exact["canonical_ref"]),
                "canonical_revision": int(exact["canonical_revision"]),
                "value_hash": str(exact["value_hash"]),
            }
        )
        private_values.append(
            {
                "canonical_ref": str(exact["canonical_ref"]),
                "canonical_revision": int(exact["canonical_revision"]),
                "value": exact.get("value"),
                "value_hash": str(exact["value_hash"]),
            }
        )
    return _recall_result(
        scope,
        route,
        canonical_revision,
        selected_public,
        private_values,
        status="DONE",
        candidate_count=len(candidate_refs),
        rendered_chars=rendered_chars,
        truncated=truncated,
    )


def capture_after_event(
    gateway: MemoryGateway,
    event: Mapping[str, Any],
    *,
    operator_id: str,
    domain: str,
    project_id: str = "skeleton",
    dataset_id: str = "default",
) -> MemoryLifecycleResult:
    if event.get("schema") != MEMORY_LIFECYCLE_EVENT_SCHEMA:
        raise MemoryLifecycleError("INVALID_LIFECYCLE_EVENT_SCHEMA", "lifecycle event schema is invalid")
    scope = resolve_memory_scope(
        operator_id=operator_id,
        domain=domain,
        project_id=project_id,
        dataset_id=dataset_id,
        privacy_class=str(event.get("privacy_class", "private")),
        fact_namespace=event.get("fact_namespace") if isinstance(event.get("fact_namespace"), str) else None,
    )
    candidate = _classify_candidate(event, scope)
    if candidate is None:
        receipt = _base_receipt(scope, "capture")
        receipt.update(
            {
                "status": "SKIPPED",
                "canonical_write_performed": False,
                "classification": "not_durable",
                "counts": {"accepted": 0, "rejected": 1},
            }
        )
        _assert_public_safe(receipt)
        return MemoryLifecycleResult(
            schema=MEMORY_LIFECYCLE_CAPTURE_SCHEMA,
            status="SKIPPED",
            private_payload={"candidate": None},
            receipt=receipt,
        )

    status_payload = _gateway_payload(gateway, "memory.private_status", {"project_id": scope.project_id, "dataset_id": scope.dataset_id})
    before_revision = _canonical_revision(status_payload)
    mutation = {
        "schema": "skeleton.private_memory_gateway.mutation.v1",
        "operation": "put",
        "project_id": scope.project_id,
        "dataset_id": scope.dataset_id,
        "expected_revision": before_revision,
        "actor_ref": candidate["actor_ref"],
        "reason_code": candidate["reason_code"],
        "approval_ref": candidate["approval_ref"],
        "fact_namespace": candidate["fact_namespace"],
        "fact_id": candidate["fact_id"],
        "value": candidate["value"],
        "source_hash": candidate["source_hash"],
        "idempotency_key": candidate["idempotency_key"],
    }
    try:
        private_receipt = _gateway_payload(gateway, "memory.private_mutate", mutation)
    except MemoryGatewayPolicyError:
        raise
    status = str(private_receipt.get("status", "DONE"))
    public = _base_receipt(scope, "capture")
    public.update(
        {
            "status": status,
            "canonical_write_performed": True,
            "canonical_ref": private_receipt.get("canonical_ref"),
            "canonical_revision": private_receipt.get("canonical_revision"),
            "expected_revision": private_receipt.get("expected_revision"),
            "source_hash": private_receipt.get("source_hash"),
            "value_hash": candidate["value_hash"],
            "idempotency_key": private_receipt.get("idempotency_key"),
            "idempotency_classification": private_receipt.get("idempotency_classification"),
            "classification": candidate["classification"],
            "privacy_class": scope.privacy_class,
            "confidence": candidate["confidence"],
            "indexes": private_receipt.get("indexes"),
            "degraded_indexes": private_receipt.get("degraded_indexes", []),
            "counts": {"accepted": 1, "rejected": 0},
        }
    )
    _assert_public_safe(public)
    return MemoryLifecycleResult(
        schema=MEMORY_LIFECYCLE_CAPTURE_SCHEMA,
        status=status,
        private_payload={"candidate": candidate, "gateway_receipt": private_receipt},
        receipt=public,
    )


def _classify_candidate(event: Mapping[str, Any], scope: MemoryScope) -> dict[str, Any] | None:
    event_type = safe_token(str(event.get("event_type", "")), "event_type")
    if event_type not in _DURABLE_EVENTS:
        return None
    classification = safe_token(str(event.get("classification", "")), "classification")
    if classification not in _DURABLE_CLASSES:
        return None
    if scope.privacy_class in _REJECTED_PRIVACY_CLASSES:
        raise MemoryLifecycleError("PRIVACY_CLASS_REQUIRES_AUTHORIZED_BOUNDARY", "secret or telemetry capture is not authorized")
    confidence = event.get("confidence", 1.0)
    if isinstance(confidence, bool) or not isinstance(confidence, int | float) or not 0 <= float(confidence) <= 1:
        raise MemoryLifecycleError("INVALID_CONFIDENCE", "confidence must be between zero and one")
    if float(confidence) < 0.5 and event_type != "remember_this":
        return None
    fact_id = safe_token(str(event.get("fact_id", "")), "fact_id")
    payload = event.get("payload")
    if payload is None:
        return None
    provenance = event.get("provenance")
    if not isinstance(provenance, Mapping):
        raise MemoryLifecycleError("MISSING_PROVENANCE", "accepted memory candidates require provenance")
    approval_ref = _lifecycle_token(
        str(event.get("approval_ref", "automatic_lifecycle")),
        "approval_ref",
        "INVALID_APPROVAL_REF",
    )
    actor_ref = _lifecycle_token(str(event.get("actor_ref", scope.operator_id)), "actor_ref", "INVALID_ACTOR_REF")
    reason_code = _lifecycle_token(
        str(event.get("reason_code", f"auto_capture_{classification}")),
        "reason_code",
        "INVALID_REASON_CODE",
    )
    source_hash = str(event.get("source_hash") or content_hash({"event": event_type, "payload": payload, "provenance": provenance}))
    if len(source_hash) != 64:
        raise MemoryLifecycleError("INVALID_SOURCE_HASH", "source hash must be sha256 hex")
    value = {
        "schema": MEMORY_LIFECYCLE_CANONICAL_VALUE_SCHEMA,
        "operator_id": scope.operator_id,
        "domain": scope.domain,
        "memory_class": classification,
        "privacy_class": scope.privacy_class,
        "confidence": float(confidence),
        "payload": payload,
        "provenance": {
            "kind": safe_token(str(provenance.get("kind", "lifecycle_event")), "provenance_kind"),
            "evidence_hash": str(provenance.get("evidence_hash") or source_hash),
        },
        "supersedes": event.get("supersedes") if isinstance(event.get("supersedes"), str) else None,
        "idempotency_source_hash": source_hash,
    }
    return {
        "fact_namespace": scope.fact_namespace,
        "fact_id": fact_id,
        "value": value,
        "value_hash": content_hash(value),
        "source_hash": source_hash.lower(),
        "idempotency_key": safe_token(str(event.get("idempotency_key") or "life_" + content_hash({"scope": scope.fact_namespace, "fact_id": fact_id, "source_hash": source_hash})[:48]), "idempotency_key"),
        "classification": classification,
        "confidence": float(confidence),
        "actor_ref": actor_ref,
        "reason_code": reason_code,
        "approval_ref": approval_ref,
    }


def _candidate_refs(gateway: MemoryGateway, *, scope: MemoryScope, query: str, limit: int) -> list[str]:
    seen: set[str] = set()
    refs: list[str] = []
    for suffix, key, route_limit in (
        ("memory.private_search_semantic", "query", limit),
        ("graph.private_query", "query", min(limit, 5)),
    ):
        payload = _gateway_payload(
            gateway,
            suffix,
            {"project_id": scope.project_id, "dataset_id": scope.dataset_id, key: query, "limit": route_limit},
        )
        for item in payload.get("results", []):
            if not isinstance(item, Mapping):
                continue
            canonical_ref = item.get("canonical_ref")
            if isinstance(canonical_ref, str) and canonical_ref.startswith(scope.fact_namespace + ":") and canonical_ref not in seen:
                seen.add(canonical_ref)
                refs.append(canonical_ref)
    return refs[:limit]


def _gateway_payload(gateway: MemoryGateway, suffix: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    response = gateway.execute(
        {
            "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
            "namespace": _GATEWAY_NAMESPACE,
            "command": command_name(_GATEWAY_NAMESPACE, suffix),
            "payload": dict(payload),
        }
    )
    result = response.get("payload")
    if not isinstance(result, Mapping):
        raise MemoryLifecycleError("INVALID_GATEWAY_RESPONSE", "gateway response payload is invalid")
    return dict(result)


def _recall_result(
    scope: MemoryScope,
    task_route: str,
    canonical_revision: int,
    selected_public: Sequence[Mapping[str, object]],
    private_values: Sequence[Mapping[str, Any]],
    *,
    status: str,
    candidate_count: int = 0,
    rendered_chars: int = 0,
    truncated: bool = False,
) -> MemoryLifecycleResult:
    receipt = _base_receipt(scope, "recall")
    receipt.update(
        {
            "status": status,
            "task_route": task_route,
            "canonical_revision": canonical_revision,
            "selected_canonical_refs": [item["canonical_ref"] for item in selected_public],
            "selected_records": [dict(item) for item in selected_public],
            "counts": {
                "selected": len(selected_public),
                "candidate_refs": candidate_count,
                "rendered_chars": rendered_chars,
            },
            "limits": {"records": MAX_RECALL_RECORDS, "max_chars": MAX_RECALL_CHARS},
            "truncated": truncated,
            "context_hash": content_hash({"revision": canonical_revision, "selected": list(selected_public), "truncated": truncated}),
        }
    )
    _assert_public_safe(receipt)
    return MemoryLifecycleResult(
        schema=MEMORY_LIFECYCLE_RECALL_SCHEMA,
        status=status,
        private_payload={"scope": scope.__dict__, "values": [dict(item) for item in private_values]},
        receipt=receipt,
    )


def _base_receipt(scope: MemoryScope, operation: str) -> dict[str, object]:
    return {
        "schema": MEMORY_LIFECYCLE_PUBLIC_RECEIPT_SCHEMA,
        "operation": operation,
        "namespace": _GATEWAY_NAMESPACE,
        "project_id": scope.project_id,
        "dataset_id": scope.dataset_id,
        "domain": scope.domain,
        "fact_namespace": scope.fact_namespace,
    }


def _canonical_revision(status_payload: Mapping[str, Any]) -> int:
    canonical = status_payload.get("canonical_sqlite")
    if isinstance(canonical, Mapping) and isinstance(canonical.get("canonical_revision"), int):
        return int(canonical["canonical_revision"])
    if isinstance(canonical, Mapping) and isinstance(canonical.get("current_canonical_revision"), int):
        return int(canonical["current_canonical_revision"])
    return 0


def _bounded_limit(value: object, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return maximum
    return min(max(value, 0), maximum)


def _lifecycle_token(value: str, name: str, reason_code: str) -> str:
    try:
        return safe_token(value, name)
    except ValueError as exc:
        raise MemoryLifecycleError(reason_code, str(exc)) from exc


def _assert_public_safe(receipt: Mapping[str, object]) -> None:
    validate_public_payload(receipt)
    _reject_private_receipt_shape(receipt)


def _reject_private_receipt_shape(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"payload", "value", "source_payload", "local_path", "source_path", "secret", "secrets"}:
                raise MemoryLifecycleError("UNSAFE_PUBLIC_RECEIPT", "public receipt contains private-bearing fields")
            _reject_private_receipt_shape(child)
        return
    if isinstance(value, list):
        for child in value:
            _reject_private_receipt_shape(child)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if "/" in value or "\\" in value or "private.sqlite" in lowered:
            raise MemoryLifecycleError("UNSAFE_PUBLIC_RECEIPT", "public receipt contains path-shaped text")

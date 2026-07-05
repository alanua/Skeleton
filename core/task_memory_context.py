from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.private_memory_history import canonical_json, content_hash, safe_token
from core.private_memory_stack import PrivateMemoryStack


TASK_MEMORY_CONTEXT_SCHEMA = "skeleton.task_memory_context.v1"
TASK_MEMORY_CONTEXT_RESULT_SCHEMA = "skeleton.task_memory_context.private_result.v1"
TASK_MEMORY_CONTEXT_PROFILES = frozenset({"public_control", "private_runtime", "none"})
TASK_MEMORY_CONTEXT_NAMESPACES = frozenset({"skeleton.context"})
MAX_CONTEXT_RECORDS = 10
MAX_CONTEXT_CHARS = 6000
MAX_PUBLIC_CONTROL_TEXT_CHARS = 1200

_PUBLIC_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "profile",
        "project_id",
        "task_route",
        "canonical_revision",
        "selected_canonical_refs",
        "selected_records",
        "counts",
        "limits",
        "truncated",
        "context_hash",
    }
)
_PUBLIC_RECORD_REQUIRED_FIELDS = frozenset(
    {"canonical_ref", "canonical_revision", "value_hash"}
)
_PUBLIC_RECORD_ALLOWED_FIELDS = _PUBLIC_RECORD_REQUIRED_FIELDS | frozenset({"public_text"})
_PUBLIC_COUNT_FIELDS = frozenset({"selected", "candidate_refs", "rendered_chars"})
_PUBLIC_LIMIT_FIELDS = frozenset({"records", "max_chars"})


class TaskMemoryContextError(ValueError):
    """Raised when task memory context must fail closed."""


@dataclass(frozen=True)
class TaskMemoryContextResult:
    schema: str
    receipt: dict[str, object]
    private_values: list[dict[str, Any]]

    def public_receipt(self) -> dict[str, object]:
        return dict(self.receipt)


def build_task_memory_context(
    stack: PrivateMemoryStack,
    *,
    project_id: str,
    task_route: str,
    profile: str,
    query: str,
    namespaces: Sequence[str] | None = None,
    required: bool = False,
    limit: int = MAX_CONTEXT_RECORDS,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> TaskMemoryContextResult:
    project_id = safe_token(project_id, "project_id")
    task_route = safe_token(task_route, "task_route")
    if profile not in TASK_MEMORY_CONTEXT_PROFILES:
        raise TaskMemoryContextError("unsupported task context profile")
    namespace_filter = _validate_namespaces(namespaces)
    allowed_namespaces = namespace_filter or TASK_MEMORY_CONTEXT_NAMESPACES
    bounded_limit = max(0, min(int(limit), MAX_CONTEXT_RECORDS))
    bounded_chars = max(0, min(int(max_chars), MAX_CONTEXT_CHARS))
    if profile == "none" or bounded_limit == 0:
        return _empty_result(project_id, task_route, profile, stack.status(), "DONE")

    status = stack.status()
    if status["state"] != "READY":
        if required:
            raise TaskMemoryContextError("private memory stack is not ready")
        return _empty_result(project_id, task_route, profile, status, "UNAVAILABLE")

    refs = _derived_candidate_refs(stack, query=query, limit=bounded_limit)
    selected: list[dict[str, Any]] = []
    rendered_chars = 0
    truncated = False
    for canonical_ref in refs:
        if len(selected) >= bounded_limit:
            truncated = True
            break
        namespace, fact_id = canonical_ref.split(":", 1)
        if namespace not in allowed_namespaces:
            continue
        exact = stack.get(namespace=namespace, fact_id=fact_id)
        value = exact["value"]
        rendered = _render_value_for_profile(profile, value)
        if rendered is None:
            continue
        remaining = bounded_chars - rendered_chars
        if remaining <= 0:
            truncated = True
            break
        if profile == "private_runtime" and len(rendered) > remaining:
            truncated = True
            continue
        if len(rendered) > remaining:
            rendered = rendered[:remaining]
            truncated = True
        rendered_chars += len(rendered)
        selected.append(
            {
                "canonical_ref": exact["canonical_ref"],
                "namespace": namespace,
                "fact_id": fact_id,
                "canonical_revision": exact["canonical_revision"],
                "value_hash": exact["value_hash"],
                "public_text": rendered if profile == "public_control" else None,
                "value": value if profile == "private_runtime" else None,
            }
        )

    canonical_revision = int(status["canonical_sqlite"]["canonical_revision"])
    public_selected = [
        {
            "canonical_ref": item["canonical_ref"],
            "canonical_revision": item["canonical_revision"],
            "value_hash": item["value_hash"],
            **({"public_text": item["public_text"]} if item.get("public_text") is not None else {}),
        }
        for item in selected
    ]
    receipt = {
        "schema": TASK_MEMORY_CONTEXT_SCHEMA,
        "status": "DONE",
        "profile": profile,
        "project_id": project_id,
        "task_route": task_route,
        "canonical_revision": canonical_revision,
        "selected_canonical_refs": [item["canonical_ref"] for item in selected],
        "selected_records": public_selected,
        "counts": {
            "selected": len(selected),
            "candidate_refs": len(refs),
            "rendered_chars": rendered_chars,
        },
        "limits": {"records": bounded_limit, "max_chars": bounded_chars},
        "truncated": truncated,
    }
    receipt["context_hash"] = content_hash(
        {
            "canonical_revision": canonical_revision,
            "profile": profile,
            "selected": public_selected,
            "truncated": truncated,
        }
    )
    _assert_public_receipt_shape(receipt)
    return TaskMemoryContextResult(
        schema=TASK_MEMORY_CONTEXT_RESULT_SCHEMA,
        receipt=receipt,
        private_values=[
            {
                "canonical_ref": item["canonical_ref"],
                "value": item["value"],
                "value_hash": item["value_hash"],
            }
            for item in selected
            if item.get("value") is not None
        ],
    )


def _derived_candidate_refs(stack: PrivateMemoryStack, *, query: str, limit: int) -> list[str]:
    seen: set[str] = set()
    refs: list[str] = []
    mempalace_limit = max(1, min(MAX_CONTEXT_RECORDS, 10))
    graphify_limit = 5
    for payload in (
        stack.search(query=query, limit=mempalace_limit),
        stack.relations(query=query, limit=graphify_limit),
    ):
        for result in payload.get("results", []):
            if not isinstance(result, Mapping):
                continue
            canonical_ref = result.get("canonical_ref")
            if not isinstance(canonical_ref, str) or ":" not in canonical_ref:
                continue
            namespace, fact_id = canonical_ref.split(":", 1)
            safe_token(namespace, "namespace")
            safe_token(fact_id, "fact_id")
            if canonical_ref not in seen:
                seen.add(canonical_ref)
                refs.append(canonical_ref)
    return refs[:MAX_CONTEXT_RECORDS]


def _render_value_for_profile(profile: str, value: Any) -> str | None:
    if profile == "private_runtime":
        return canonical_json(value)
    if profile != "public_control" or not isinstance(value, Mapping):
        return None
    if value.get("egress_classification") != "PUBLIC_SAFE_CONTROL":
        return None
    text = value.get("text")
    if not isinstance(text, str) or not text or len(text) > MAX_PUBLIC_CONTROL_TEXT_CHARS:
        return None
    return text


def _validate_namespaces(namespaces: Sequence[str] | None) -> set[str]:
    if not namespaces:
        return set()
    validated = {safe_token(namespace, "namespace") for namespace in namespaces}
    unsupported = validated - TASK_MEMORY_CONTEXT_NAMESPACES
    if unsupported:
        raise TaskMemoryContextError("unsupported task context namespace")
    return validated


def _empty_result(
    project_id: str,
    task_route: str,
    profile: str,
    status: Mapping[str, Any],
    result_status: str,
) -> TaskMemoryContextResult:
    revision = int(status.get("canonical_sqlite", {}).get("canonical_revision", 0))
    receipt = {
        "schema": TASK_MEMORY_CONTEXT_SCHEMA,
        "status": result_status,
        "profile": profile,
        "project_id": project_id,
        "task_route": task_route,
        "canonical_revision": revision,
        "selected_canonical_refs": [],
        "selected_records": [],
        "counts": {"selected": 0, "candidate_refs": 0, "rendered_chars": 0},
        "limits": {"records": MAX_CONTEXT_RECORDS, "max_chars": MAX_CONTEXT_CHARS},
        "truncated": False,
    }
    receipt["context_hash"] = content_hash(receipt)
    _assert_public_receipt_shape(receipt)
    return TaskMemoryContextResult(
        schema=TASK_MEMORY_CONTEXT_RESULT_SCHEMA,
        receipt=receipt,
        private_values=[],
    )


def _assert_public_receipt_shape(receipt: Mapping[str, Any]) -> None:
    if set(receipt) != _PUBLIC_RECEIPT_FIELDS:
        raise TaskMemoryContextError("invalid task context public receipt fields")
    if receipt.get("schema") != TASK_MEMORY_CONTEXT_SCHEMA:
        raise TaskMemoryContextError("invalid task context public receipt schema")
    if receipt.get("profile") not in TASK_MEMORY_CONTEXT_PROFILES:
        raise TaskMemoryContextError("invalid task context public receipt profile")
    safe_token(receipt.get("project_id"), "project_id")
    safe_token(receipt.get("task_route"), "task_route")
    if not isinstance(receipt.get("canonical_revision"), int):
        raise TaskMemoryContextError("invalid task context canonical revision")
    records = receipt.get("selected_records")
    if not isinstance(records, list):
        raise TaskMemoryContextError("invalid task context selected records")
    for record in records:
        if not isinstance(record, Mapping):
            raise TaskMemoryContextError("invalid task context selected record")
        fields = set(record)
        if fields - _PUBLIC_RECORD_ALLOWED_FIELDS or _PUBLIC_RECORD_REQUIRED_FIELDS - fields:
            raise TaskMemoryContextError("invalid task context selected record fields")
        canonical_ref = record.get("canonical_ref")
        if not isinstance(canonical_ref, str) or ":" not in canonical_ref:
            raise TaskMemoryContextError("invalid task context canonical ref")
        namespace, fact_id = canonical_ref.split(":", 1)
        safe_token(namespace, "namespace")
        safe_token(fact_id, "fact_id")
        if not isinstance(record.get("canonical_revision"), int):
            raise TaskMemoryContextError("invalid task context record revision")
        if not isinstance(record.get("value_hash"), str):
            raise TaskMemoryContextError("invalid task context value hash")
        if "public_text" in record and not isinstance(record.get("public_text"), str):
            raise TaskMemoryContextError("invalid task context public text")
    counts = receipt.get("counts")
    limits = receipt.get("limits")
    if not isinstance(counts, Mapping) or set(counts) != _PUBLIC_COUNT_FIELDS:
        raise TaskMemoryContextError("invalid task context counts")
    if not isinstance(limits, Mapping) or set(limits) != _PUBLIC_LIMIT_FIELDS:
        raise TaskMemoryContextError("invalid task context limits")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts.values()):
        raise TaskMemoryContextError("invalid task context count value")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in limits.values()):
        raise TaskMemoryContextError("invalid task context limit value")
    if not isinstance(receipt.get("truncated"), bool):
        raise TaskMemoryContextError("invalid task context truncation flag")
    if not isinstance(receipt.get("context_hash"), str):
        raise TaskMemoryContextError("invalid task context hash")

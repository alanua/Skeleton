from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.private_memory_history import canonical_json, content_hash, safe_token
from core.private_memory_stack import PrivateMemoryStack, PrivateMemoryStackError


TASK_MEMORY_CONTEXT_SCHEMA = "skeleton.task_memory_context.v1"
TASK_MEMORY_CONTEXT_RESULT_SCHEMA = "skeleton.task_memory_context.private_result.v1"
TASK_MEMORY_CONTEXT_PROFILES = frozenset({"public_control", "private_runtime", "none"})
MAX_CONTEXT_RECORDS = 10
MAX_CONTEXT_CHARS = 6000

_PUBLIC_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()#@+-]{0,1200}$")
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "private key",
    "api_key",
    "ssh-rsa",
    "bearer ",
    "/home/",
    "/tmp/",
    ".sqlite",
)


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
        if namespace_filter and namespace not in namespace_filter:
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
    _assert_public_receipt_safe(receipt)
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
    if profile != "public_control":
        return None
    if not isinstance(value, Mapping):
        return None
    if value.get("egress_classification") != "PUBLIC_SAFE_CONTROL":
        return None
    text = value.get("text")
    if not isinstance(text, str) or not _PUBLIC_SAFE_TEXT_RE.fullmatch(text):
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise TaskMemoryContextError("public_control selected secret-like value")
    return text


def _validate_namespaces(namespaces: Sequence[str] | None) -> set[str]:
    if not namespaces:
        return set()
    return {safe_token(namespace, "namespace") for namespace in namespaces}


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
    return TaskMemoryContextResult(
        schema=TASK_MEMORY_CONTEXT_RESULT_SCHEMA,
        receipt=receipt,
        private_values=[],
    )


def _assert_public_receipt_safe(receipt: Mapping[str, Any]) -> None:
    serialized = json.dumps(receipt, sort_keys=True)
    lowered = serialized.lower()
    if any(marker in lowered for marker in ("/home/", "/tmp/", ".sqlite", "password", "credential", "api_key")):
        raise PrivateMemoryStackError("task context public receipt failed privacy validation")

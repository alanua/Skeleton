from __future__ import annotations

import functools
import inspect
import math
import re
from collections.abc import Mapping
from typing import Any

from core import cognee_worker_bootstrap as _bootstrap

_PATCH_MARKER = "__skeleton_cognee_search_response_compat__"
_OPAQUE_DATASET_RE = re.compile(r"^sk_[0-9a-f]{48}$")
_ENVELOPE_KEYS = frozenset(
    {"dataset_id", "dataset_name", "dataset_tenant_id", "search_result"}
)


def install_cognee_search_response_compat() -> bool:
    """Normalize only pinned single-tenant CHUNKS results to the strict envelope."""

    current = _bootstrap._stage_wrapper
    if getattr(current, _PATCH_MARKER, False):
        return False

    def compatible_stage_wrapper(
        operation: Any, operation_name: str, reason: str
    ) -> Any:
        wrapped = current(operation, operation_name, reason)
        if operation_name != "search":
            return wrapped

        @functools.wraps(wrapped)
        async def compatible(*args: Any, **kwargs: Any) -> Any:
            result = wrapped(*args, **kwargs)
            resolved = await result if inspect.isawaitable(result) else result
            return _normalize_direct_chunks(resolved, kwargs)

        setattr(compatible, _bootstrap._WRAPPER_MARKER, True)
        return compatible

    setattr(compatible_stage_wrapper, _PATCH_MARKER, True)
    _bootstrap._stage_wrapper = compatible_stage_wrapper
    return True


def _normalize_direct_chunks(result: Any, kwargs: Mapping[str, Any]) -> Any:
    query_type = kwargs.get("query_type")
    if getattr(query_type, "value", None) != "CHUNKS":
        return result

    datasets = kwargs.get("datasets")
    if (
        not isinstance(datasets, (list, tuple))
        or len(datasets) != 1
        or not isinstance(datasets[0], str)
        or _OPAQUE_DATASET_RE.fullmatch(datasets[0]) is None
    ):
        return result
    dataset_name = datasets[0]

    if result == []:
        return result
    if not isinstance(result, list):
        return result
    if _is_existing_envelope(result):
        return result

    minimized = [_minimize_chunk(item) for item in result]
    if any(item is None for item in minimized):
        return result

    return [
        {
            "dataset_id": None,
            "dataset_name": dataset_name,
            "dataset_tenant_id": None,
            "search_result": [item for item in minimized if item is not None],
        }
    ]


def _is_existing_envelope(result: list[Any]) -> bool:
    if len(result) != 1 or not isinstance(result[0], Mapping):
        return False
    envelope = result[0]
    return (
        "search_result" in envelope
        and not (set(envelope) - _ENVELOPE_KEYS)
        and isinstance(envelope.get("search_result"), list)
    )


def _minimize_chunk(value: Any) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    text = value.get("text")
    if not isinstance(text, str):
        return None
    minimized: dict[str, object] = {"text": text}
    score = value.get("score")
    if (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(float(score))
    ):
        minimized["score"] = float(score)
    return minimized

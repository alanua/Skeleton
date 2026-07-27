from __future__ import annotations

import re
from typing import Mapping

from core import cognee_worker_bootstrap as _bootstrap

_ALLOWED_ERROR_TYPES = frozenset(
    {
        "assertion_error",
        "bool_type",
        "dict_type",
        "enum",
        "extra_forbidden",
        "int_type",
        "is_instance_of",
        "list_type",
        "literal_error",
        "missing",
        "model_type",
        "string_type",
        "union_tag_invalid",
        "union_tag_not_found",
        "url_parsing",
        "url_scheme",
        "uuid_type",
        "value_error",
    }
)
_ALLOWED_LOCATIONS = frozenset(
    {
        "api_base",
        "base_url",
        "content",
        "data",
        "dataset",
        "dataset_id",
        "dataset_name",
        "documents",
        "embedding_dimensions",
        "embedding_endpoint",
        "embedding_model",
        "embedding_provider",
        "endpoint",
        "extension",
        "file_path",
        "graph_db_config",
        "id",
        "incremental_loading",
        "input",
        "llm_endpoint",
        "llm_model",
        "llm_provider",
        "metadata",
        "mime_type",
        "model",
        "name",
        "node_set",
        "payload",
        "preferred_loaders",
        "provider",
        "status",
        "text",
        "user",
        "vector_db_config",
    }
)
_SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_PATCH_MARKER = "__skeleton_pydantic_validation_detail__"


def install_pydantic_validation_error_detail() -> bool:
    current = _bootstrap._safe_exception_reason
    if getattr(current, _PATCH_MARKER, False):
        return False

    def detailed_reason(base_reason: str, exc: BaseException) -> str:
        exact = _pydantic_validation_reason(base_reason, exc)
        return exact if exact is not None else current(base_reason, exc)

    setattr(detailed_reason, _PATCH_MARKER, True)
    _bootstrap._safe_exception_reason = detailed_reason
    return True


def _pydantic_validation_reason(
    base_reason: str, exc: BaseException
) -> str | None:
    exception_type = type(exc)
    if (
        exception_type.__module__.split(".", 1)[0] != "pydantic_core"
        or exception_type.__name__ != "ValidationError"
    ):
        return None

    suffixes = ["pydantic_core", "validation_error"]
    try:
        errors_method = getattr(exc, "errors", None)
        errors = (
            errors_method(
                include_url=False,
                include_context=False,
                include_input=False,
            )
            if callable(errors_method)
            else None
        )
    except Exception:
        errors = None
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        error_type = errors[0].get("type")
        if isinstance(error_type, str) and error_type in _ALLOWED_ERROR_TYPES:
            suffixes.append(error_type)
        location = errors[0].get("loc")
        if isinstance(location, (list, tuple)):
            for item in reversed(location):
                if isinstance(item, str) and item in _ALLOWED_LOCATIONS:
                    suffixes.append(item)
                    break
    candidate = "_".join((base_reason, *suffixes))
    return candidate if _SAFE_REASON_RE.fullmatch(candidate) else base_reason

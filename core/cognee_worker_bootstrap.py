from __future__ import annotations

import functools
import importlib
import inspect
import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, MutableMapping
from urllib.parse import urlparse

_REQUIRED_VALUES = {
    "LLM_PROVIDER": "ollama",
    "DB_PROVIDER": "sqlite",
    "GRAPH_DATABASE_PROVIDER": "kuzu",
    "VECTOR_DB_PROVIDER": "lancedb",
    "ALLOW_HTTP_REQUESTS": "false",
    "REQUIRE_AUTHENTICATION": "false",
}
_ALLOWED_EMBEDDING_PROVIDERS = frozenset({"ollama", "fastembed"})
_STAGE_REASONS = {
    "add": "cognee_add_exception",
    "cognify": "cognee_cognify_exception",
    "search": "cognee_search_exception",
    "forget": "cognee_forget_exception",
}
_SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_WRAPPER_MARKER = "__skeleton_cognee_stage_wrapper__"
_STALE_COMPATIBILITY_KWARGS = frozenset({"data_cache", "run_in_background"})


def configure_cognee_worker_environment(
    env: MutableMapping[str, str] | None = None,
) -> bool:
    """Apply the closed local Cognee worker compatibility profile.

    The function is intentionally a no-op unless the complete private local-worker
    fingerprint is present. It never changes providers, models, endpoints, paths,
    credentials, database choices, or network policy.
    """

    values = os.environ if env is None else env
    if any(
        values.get(key, "").strip().casefold() != expected
        for key, expected in _REQUIRED_VALUES.items()
    ):
        return False
    if (
        values.get("EMBEDDING_PROVIDER", "").strip().casefold()
        not in _ALLOWED_EMBEDDING_PROVIDERS
    ):
        return False
    if not _private_roots_are_bounded(values):
        return False
    if not _loopback_endpoint(values.get("LLM_ENDPOINT", "")):
        return False
    if (
        values.get("EMBEDDING_PROVIDER", "").strip().casefold() == "ollama"
        and not _loopback_endpoint(values.get("EMBEDDING_ENDPOINT", ""))
    ):
        return False

    values["ENABLE_BACKEND_ACCESS_CONTROL"] = "False"
    values["CACHING"] = "False"
    values["LLM_INSTRUCTOR_MODE"] = "json_schema_mode"
    install_cognee_operation_wrappers()
    return True


def install_cognee_operation_wrappers(cognee_module: Any | None = None) -> bool:
    """Wrap pinned Cognee public operations with bounded compatibility and errors."""

    if cognee_module is None:
        try:
            cognee_module = importlib.import_module("cognee")
        except Exception:
            return False
    installed = False
    for name, reason in _STAGE_REASONS.items():
        original = getattr(cognee_module, name, None)
        if not callable(original) or getattr(original, _WRAPPER_MARKER, False):
            continue
        setattr(cognee_module, name, _stage_wrapper(original, name, reason))
        installed = True
    return installed


def _stage_wrapper(operation: Any, operation_name: str, reason: str) -> Any:
    @functools.wraps(operation)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        normalized_args, normalized_kwargs = _normalize_operation_arguments(
            operation_name, args, kwargs
        )
        try:
            result = operation(*normalized_args, **normalized_kwargs)
            return await result if inspect.isawaitable(result) else result
        except Exception as exc:
            existing_reason = getattr(exc, "reason_code", None)
            if isinstance(existing_reason, str) and _SAFE_REASON_RE.fullmatch(
                existing_reason
            ):
                raise
            main_module = sys.modules.get("__main__")
            error_type = getattr(main_module, "CogneeLocalRuntimeError", None)
            if isinstance(error_type, type) and issubclass(error_type, Exception):
                raise error_type(reason, "Cognee operation failed") from exc
            raise

    setattr(wrapped, _WRAPPER_MARKER, True)
    return wrapped


def _normalize_operation_arguments(
    operation_name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    normalized_args = tuple(args)
    normalized_kwargs = dict(kwargs)
    if operation_name in {"add", "cognify"}:
        for key in _STALE_COMPATIBILITY_KWARGS:
            normalized_kwargs.pop(key, None)
    if operation_name == "add":
        if normalized_args:
            first = _collapse_single_text(normalized_args[0])
            normalized_args = (first, *normalized_args[1:])
        elif "data" in normalized_kwargs:
            normalized_kwargs["data"] = _collapse_single_text(
                normalized_kwargs["data"]
            )
    return normalized_args, normalized_kwargs


def _collapse_single_text(value: Any) -> Any:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 1
        and isinstance(value[0], str)
    ):
        return value[0]
    return value


def _private_roots_are_bounded(env: MutableMapping[str, str]) -> bool:
    home_value = env.get("HOME", "").strip()
    data_value = env.get("DATA_ROOT_DIRECTORY", "").strip()
    system_value = env.get("SYSTEM_ROOT_DIRECTORY", "").strip()
    if not home_value or not data_value or not system_value:
        return False
    try:
        home = Path(home_value).expanduser().resolve()
        data = Path(data_value).expanduser().resolve()
        system = Path(system_value).expanduser().resolve()
        data.relative_to(home)
        system.relative_to(home)
    except (OSError, RuntimeError, ValueError):
        return False
    return data != home and system != home and data != system


def _loopback_endpoint(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    host = parsed.hostname.strip("[]")
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

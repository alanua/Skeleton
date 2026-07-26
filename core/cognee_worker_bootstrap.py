from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import MutableMapping
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


def configure_cognee_worker_environment(
    env: MutableMapping[str, str] | None = None,
) -> bool:
    """Apply the closed local Cognee worker compatibility profile.

    The function is intentionally a no-op unless the complete private local-worker
    fingerprint is present. It never changes providers, models, endpoints, paths,
    credentials, database choices, or network policy.
    """

    values = os.environ if env is None else env
    if any(values.get(key, "").strip().casefold() != expected for key, expected in _REQUIRED_VALUES.items()):
        return False
    if values.get("EMBEDDING_PROVIDER", "").strip().casefold() not in _ALLOWED_EMBEDDING_PROVIDERS:
        return False
    if not _private_roots_are_bounded(values):
        return False
    if not _loopback_endpoint(values.get("LLM_ENDPOINT", "")):
        return False
    if values.get("EMBEDDING_PROVIDER", "").strip().casefold() == "ollama" and not _loopback_endpoint(
        values.get("EMBEDDING_ENDPOINT", "")
    ):
        return False

    values["ENABLE_BACKEND_ACCESS_CONTROL"] = "False"
    values["CACHING"] = "False"
    values["LLM_INSTRUCTOR_MODE"] = "json_schema_mode"
    return True


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

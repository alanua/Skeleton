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
    "STORAGE_BACKEND": "local",
    "ALLOW_HTTP_REQUESTS": "false",
    "ALLOW_CYPHER_QUERY": "false",
    "ENABLE_BACKEND_ACCESS_CONTROL": "true",
    "REQUIRE_AUTHENTICATION": "false",
    "ACCEPT_LOCAL_FILE_PATH": "false",
    "TELEMETRY_DISABLED": "1",
}
_ALLOWED_EMBEDDING_PROVIDERS = frozenset({"ollama", "fastembed"})


def enable_cognee_internal_file_access(
    env: MutableMapping[str, str] | None = None,
) -> bool:
    """Permit only Cognee's own files inside the bounded isolated worker.

    The worker request contract never accepts a path or arbitrary Cognee input. It
    accepts a validated projection document, serializes that document to one JSON
    string, and invokes Cognee locally. Cognee then persists that string beneath its
    configured private data root and reopens the resulting internal ``file://`` URI.
    """

    values = os.environ if env is None else env
    if any(
        values.get(key, "").strip().casefold() != expected
        for key, expected in _REQUIRED_VALUES.items()
    ):
        return False

    embedding_provider = values.get("EMBEDDING_PROVIDER", "").strip().casefold()
    if embedding_provider not in _ALLOWED_EMBEDDING_PROVIDERS:
        return False
    if not _loopback_endpoint(values.get("LLM_ENDPOINT", "")):
        return False
    if embedding_provider == "ollama" and not _loopback_endpoint(
        values.get("EMBEDDING_ENDPOINT", "")
    ):
        return False
    if not _bounded_private_roots(values):
        return False

    values["ACCEPT_LOCAL_FILE_PATH"] = "True"
    return True


def _bounded_private_roots(values: MutableMapping[str, str]) -> bool:
    raw_home = values.get("HOME", "").strip()
    raw_data = values.get("DATA_ROOT_DIRECTORY", "").strip()
    raw_system = values.get("SYSTEM_ROOT_DIRECTORY", "").strip()
    raw_tmp = values.get("TMPDIR", "").strip()
    if not raw_home or not raw_data or not raw_system or not raw_tmp:
        return False
    try:
        home = Path(raw_home).expanduser().resolve(strict=False)
        data = Path(raw_data).expanduser().resolve(strict=False)
        system = Path(raw_system).expanduser().resolve(strict=False)
        temporary = Path(raw_tmp).expanduser().resolve(strict=False)
        data.relative_to(home)
        system.relative_to(home)
        temporary.relative_to(home)
    except (OSError, RuntimeError, ValueError):
        return False
    return (
        home.is_absolute()
        and data != home
        and system != home
        and temporary != home
        and len({data, system, temporary}) == 3
    )


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

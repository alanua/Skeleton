from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import venv
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from core.private_memory_history import content_hash


PINNED_COGNEE_VERSION = "1.4.0"
COGNEE_PACKAGE_REQUIREMENT = f"cognee=={PINNED_COGNEE_VERSION}"
COGNEE_RUNTIME_SCHEMA = "skeleton.cognee_local_runtime.v1"
COGNEE_ACTIVATION_RECEIPT_SCHEMA = "skeleton.five_layer_memory_activation_receipt.v1"
ACTIVATION_MARKER_SCHEMA = "skeleton.five_layer_private_memory.activation_marker.v1"
ACTIVATION_MARKER_NAME = "five_layer_private_memory_activation.json"
COGNEE_DATA_DIR = "cognee"
COGNEE_CACHE_DIR = "cognee_cache"
COGNEE_CONFIG_DIR = "cognee_config"
COGNEE_VENV_DIR = "cognee_venv"

_URL_ENV_KEYS = (
    "SKELETON_COGNEE_LLM_ENDPOINT",
    "SKELETON_COGNEE_EMBEDDING_ENDPOINT",
)
_MODEL_ENV_KEYS = (
    "SKELETON_COGNEE_LLM_MODEL",
    "SKELETON_COGNEE_EMBEDDING_MODEL",
)
_MODEL_CREDENTIAL_PREFIXES = (
    "OPENAI_",
    "ANTHROPIC_",
    "GOOGLE_",
    "GEMINI_",
    "COHERE_",
    "MISTRAL_",
    "AZURE_OPENAI_",
    "TOGETHER_",
    "VOYAGE_",
)
_CREDENTIAL_ENV_RE = re.compile(r"(?:^|_)(API_KEY|TOKEN|SECRET|CREDENTIALS?)$")
_LOCAL_MODEL_PROVIDER_KEYS = frozenset((*_URL_ENV_KEYS, *_MODEL_ENV_KEYS))
_SAFE_LINE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class CogneeLocalRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CogneeRuntimePaths:
    private_root: Path
    data_dir: Path
    cache_dir: Path
    config_dir: Path
    venv_dir: Path
    projection_db: Path
    activation_marker: Path


@dataclass(frozen=True)
class CogneeProviderConfig:
    llm_endpoint: str
    llm_model: str
    embedding_endpoint: str
    embedding_model: str

    @property
    def fingerprint(self) -> str:
        return content_hash(
            {
                "llm_endpoint": self.llm_endpoint,
                "llm_model": self.llm_model,
                "embedding_endpoint": self.embedding_endpoint,
                "embedding_model": self.embedding_model,
            }
        )


class CogneePackageFacade:
    """Version-checked compatibility boundary for the optional Cognee package."""

    def __init__(self, module: object | None = None) -> None:
        self.module = module if module is not None else importlib.import_module("cognee")
        self.version = str(getattr(self.module, "__version__", ""))
        if self.version != PINNED_COGNEE_VERSION:
            raise CogneeLocalRuntimeError("unsupported_cognee_version", "cognee version is not pinned")
        self._project = _first_callable(self.module, ("add", "add_text", "add_texts", "ingest", "project"))
        self._recall = _first_callable(self.module, ("search", "recall", "query"))
        self._health = _first_callable(self.module, ("health", "status"))
        self._forget = _first_callable(self.module, ("prune", "delete", "forget"))
        if self._project is None or self._recall is None:
            raise CogneeLocalRuntimeError("unsupported_cognee_api", "cognee API is unsupported")

    def project(self, *, text: str, metadata: Mapping[str, object]) -> None:
        _call_compat(self._project, text=text, metadata=dict(metadata), data=text)

    def recall(self, *, query: str, limit: int) -> object:
        return _call_compat(self._recall, query=query, limit=limit, text=query)

    def health(self) -> bool:
        if self._health is None:
            return True
        result = _call_compat(self._health)
        if isinstance(result, Mapping) and result.get("status") in {"BLOCKED", "ERROR", "UNAVAILABLE"}:
            return False
        return True

    def forget(self, *, dataset_key: str) -> None:
        if self._forget is not None:
            _call_compat(self._forget, dataset_key=dataset_key, dataset_id=dataset_key)


def cognee_runtime_paths(private_root: str | Path) -> CogneeRuntimePaths:
    root = Path(private_root).expanduser().resolve()
    return CogneeRuntimePaths(
        private_root=root,
        data_dir=root / COGNEE_DATA_DIR,
        cache_dir=root / COGNEE_CACHE_DIR,
        config_dir=root / COGNEE_CONFIG_DIR,
        venv_dir=root / COGNEE_VENV_DIR,
        projection_db=root / COGNEE_DATA_DIR / "projection.sqlite",
        activation_marker=root / ACTIVATION_MARKER_NAME,
    )


def ensure_private_runtime_tree(private_root: str | Path) -> CogneeRuntimePaths:
    paths = cognee_runtime_paths(private_root)
    for directory in (paths.private_root, paths.data_dir, paths.cache_dir, paths.config_dir):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    return paths


def validate_local_provider_config(env: Mapping[str, str] | None = None) -> CogneeProviderConfig:
    env_map = dict(os.environ if env is None else env)
    missing = [key for key in (*_URL_ENV_KEYS, *_MODEL_ENV_KEYS) if not env_map.get(key, "").strip()]
    if missing:
        raise CogneeLocalRuntimeError("local_provider_config_missing", "explicit local provider configuration is required")
    for key in _URL_ENV_KEYS:
        if not _is_loopback_url(env_map[key].strip()):
            raise CogneeLocalRuntimeError("non_loopback_provider_endpoint", "provider endpoint must be loopback only")
    for key, value in env_map.items():
        if key in _LOCAL_MODEL_PROVIDER_KEYS or not key.startswith(_MODEL_CREDENTIAL_PREFIXES):
            continue
        if _CREDENTIAL_ENV_RE.search(key) and str(value).strip():
            raise CogneeLocalRuntimeError("inherited_model_credentials_rejected", "model credentials must not be inherited")
    for key in ("SKELETON_COGNEE_TELEMETRY", "COGNEE_TELEMETRY"):
        telemetry = env_map.get(key, "0").strip().casefold()
        if telemetry in {"1", "true", "yes", "on", "enabled"}:
            raise CogneeLocalRuntimeError("telemetry_rejected", "telemetry must be disabled")
    return CogneeProviderConfig(
        llm_endpoint=env_map["SKELETON_COGNEE_LLM_ENDPOINT"].strip(),
        llm_model=env_map["SKELETON_COGNEE_LLM_MODEL"].strip(),
        embedding_endpoint=env_map["SKELETON_COGNEE_EMBEDDING_ENDPOINT"].strip(),
        embedding_model=env_map["SKELETON_COGNEE_EMBEDDING_MODEL"].strip(),
    )


def install_or_verify_pinned_cognee(
    private_root: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    installer: Callable[[list[str], Mapping[str, str]], tuple[int, str]] | None = None,
) -> dict[str, object]:
    paths = ensure_private_runtime_tree(private_root)
    if installer is None:
        if not paths.venv_dir.exists():
            venv.EnvBuilder(with_pip=True, clear=False).create(paths.venv_dir)
        python = paths.venv_dir / "bin" / "python"
        command = [str(python), "-m", "pip", "install", "--upgrade", COGNEE_PACKAGE_REQUIREMENT]

        def _default_installer(cmd: list[str], install_env: Mapping[str, str]) -> tuple[int, str]:
            proc = subprocess.run(cmd, env=dict(install_env), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, check=False)
            return proc.returncode, proc.stdout

        installer = _default_installer
    else:
        command = [str(paths.venv_dir / "bin" / "python"), "-m", "pip", "install", COGNEE_PACKAGE_REQUIREMENT]
    code, output = installer(command, _install_env(paths, env or os.environ))
    if code != 0:
        raise CogneeLocalRuntimeError("dependency_install_failed", "cognee dependency installation failed")
    if _looks_private(output):
        raise CogneeLocalRuntimeError("dependency_install_output_private", "dependency output was not public safe")
    return {"dependency": COGNEE_PACKAGE_REQUIREMENT, "installed": True, "runtime_venv": True}


def atomic_write_activation_marker(
    private_root: str | Path,
    *,
    expected_head_sha: str,
    provider_config: CogneeProviderConfig,
    enabled: bool = True,
) -> dict[str, object]:
    paths = ensure_private_runtime_tree(private_root)
    marker = {
        "schema": ACTIVATION_MARKER_SCHEMA,
        "enabled": bool(enabled),
        "source_sha": _safe_sha(expected_head_sha),
        "cognee_version": PINNED_COGNEE_VERSION,
        "provider_config_hash": provider_config.fingerprint,
        "mandatory_runner_bootstrap": bool(enabled),
    }
    _atomic_private_json(paths.activation_marker, marker)
    return marker


def read_activation_marker(private_root: str | Path) -> dict[str, object] | None:
    path = cognee_runtime_paths(private_root).activation_marker
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != ACTIVATION_MARKER_SCHEMA:
        raise CogneeLocalRuntimeError("activation_marker_invalid", "activation marker is invalid")
    return data


def restore_activation_marker(private_root: str | Path, previous: Mapping[str, object] | None) -> bool:
    path = cognee_runtime_paths(private_root).activation_marker
    if previous is None:
        path.unlink(missing_ok=True)
        return not path.exists()
    _atomic_private_json(path, previous)
    return read_activation_marker(private_root) == dict(previous)


def live_aggregate_status(private_root: str | Path) -> dict[str, object]:
    marker = read_activation_marker(private_root)
    return {
        "activation_marker_present": marker is not None,
        "activation_enabled": bool(marker.get("enabled")) if isinstance(marker, Mapping) else False,
        "cognee_version": str(marker.get("cognee_version", "")) if isinstance(marker, Mapping) else "",
        "canonical_count": 0,
        "semantic_count": 0,
        "graph_count": 0,
    }


def activation_receipt(
    *,
    status: str,
    reason: str,
    source_sha: str = "0" * 40,
    counts: Mapping[str, int] | None = None,
    rollback_verified: bool = False,
    cognee_selected: bool = False,
    mempalace_fallback_proven: bool = False,
    graphify_confirmed: bool = False,
    dependency_installed: bool = False,
    live_status_checked: bool = False,
) -> dict[str, object]:
    return {
        "schema": COGNEE_ACTIVATION_RECEIPT_SCHEMA,
        "status": status,
        "reason_codes": [_safe_reason(reason)],
        "source_sha": _safe_sha(source_sha),
        "dependency_versions": {"cognee": PINNED_COGNEE_VERSION},
        "backend_versions": {"cognee": PINNED_COGNEE_VERSION, "runtime_schema": COGNEE_RUNTIME_SCHEMA},
        "booleans": {
            "dependency_installed": dependency_installed,
            "cognee_selected": cognee_selected,
            "mempalace_fallback_proven": mempalace_fallback_proven,
            "graphify_confirmed": graphify_confirmed,
            "live_status_checked": live_status_checked,
            "rollback_verified": rollback_verified,
            "private_leak_detected": False,
        },
        "aggregate_counts": dict(counts or {"canonical_count": 0, "semantic_count": 0, "graph_count": 0}),
        "resource_totals": {"disk_bytes": 0, "ram_bytes": 0},
        "rollback": {"verified": rollback_verified, "status": "verified" if rollback_verified else "not_verified"},
    }


def _is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip("[]").casefold()
    return host == "localhost" or host == "::1" or host.startswith("127.")


def _install_env(paths: CogneeRuntimePaths, env: Mapping[str, str]) -> dict[str, str]:
    safe = {
        "PATH": env.get("PATH", os.environ.get("PATH", "")),
        "HOME": str(paths.private_root),
        "TMPDIR": str(paths.cache_dir),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }
    return safe


def _first_callable(module: object, names: tuple[str, ...]) -> Callable[..., object] | None:
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


def _call_compat(fn: Callable[..., object], **kwargs: object) -> object:
    try:
        return fn(**kwargs)
    except TypeError:
        for key in ("text", "data", "query"):
            if key in kwargs:
                return fn(kwargs[key])
        return fn()


def _atomic_private_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(name)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def _safe_sha(value: str) -> str:
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", value or "") else "0" * 40


def _safe_reason(value: str) -> str:
    text = str(value or "blocked")
    return text if _SAFE_LINE_RE.fullmatch(text) else "blocked"


def _looks_private(value: str) -> bool:
    return "/home/" in value or "/tmp/" in value or "\\" in value or "sk-" in value

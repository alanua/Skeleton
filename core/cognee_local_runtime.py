from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import inspect
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

PINNED_COGNEE_VERSION = "1.4.0"
COGNEE_PACKAGE_REQUIREMENT = f"cognee=={PINNED_COGNEE_VERSION}"
COGNEE_RUNTIME_SCHEMA = "skeleton.cognee_local_runtime.v2"
COGNEE_WORKER_REQUEST_SCHEMA = "skeleton.cognee_worker.request.v1"
COGNEE_WORKER_RESPONSE_SCHEMA = "skeleton.cognee_worker.response.v1"
COGNEE_PROJECTION_DOCUMENT_SCHEMA = "skeleton.cognee_projection.document.v1"
COGNEE_ACTIVATION_RECEIPT_SCHEMA = "skeleton.five_layer_memory_activation_receipt.v2"
ACTIVATION_MARKER_SCHEMA = "skeleton.five_layer_private_memory.activation_marker.v2"
ACTIVATION_MARKER_NAME = "five_layer_private_memory_activation.json"
MAX_WORKER_REQUEST_BYTES = 64 * 1024
MAX_WORKER_RESPONSE_BYTES = 256 * 1024
WORKER_TIMEOUT_SECONDS = 180
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_CLOUD_CREDENTIAL_PREFIXES = (
    "OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_", "COHERE_",
    "MISTRAL_", "AZURE_", "TOGETHER_", "VOYAGE_", "AWS_",
)
_CLOUD_CONFIG_PREFIXES = ("FALLBACK_", "LLM_EXTRA_", "EMBEDDING_EXTRA_")
_STAGE_CONFIG_RE = re.compile(
    r"^LLM_(EXTRACTION|SUMMARIZATION|QUERY)_(PROVIDER|MODEL|ENDPOINT|API_KEY|API_VERSION)$"
)


class CogneeLocalRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CogneeRuntimePaths:
    private_root: Path
    data_dir: Path
    system_dir: Path
    receipts_dir: Path
    cache_dir: Path
    venv_dir: Path
    activation_marker: Path


@dataclass(frozen=True)
class CogneeProviderConfig:
    llm_provider: str
    llm_model: str
    llm_endpoint: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_endpoint: str | None
    huggingface_tokenizer: str | None
    fastembed_cache: str | None

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(
            {
                "llm_provider": self.llm_provider,
                "llm_model": self.llm_model,
                "llm_endpoint": self.llm_endpoint,
                "embedding_provider": self.embedding_provider,
                "embedding_model": self.embedding_model,
                "embedding_dimensions": self.embedding_dimensions,
                "embedding_endpoint": self.embedding_endpoint,
                "huggingface_tokenizer": self.huggingface_tokenizer,
                "fastembed_cache": bool(self.fastembed_cache),
            }
        )


def cognee_runtime_paths(private_root: str | Path) -> CogneeRuntimePaths:
    root = Path(private_root).expanduser().resolve()
    runtime = root / "cognee_runtime"
    return CogneeRuntimePaths(
        private_root=root,
        data_dir=runtime / "data",
        system_dir=runtime / "system",
        receipts_dir=runtime / "receipts",
        cache_dir=runtime / "cache",
        venv_dir=runtime / "venv",
        activation_marker=root / ACTIVATION_MARKER_NAME,
    )


def ensure_private_runtime_tree(private_root: str | Path) -> CogneeRuntimePaths:
    paths = cognee_runtime_paths(private_root)
    for directory in (
        paths.private_root,
        paths.data_dir,
        paths.system_dir,
        paths.receipts_dir,
        paths.cache_dir,
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    return paths


def validate_local_provider_config(env: Mapping[str, str] | None = None) -> CogneeProviderConfig:
    values = dict(os.environ if env is None else env)
    llm_provider = values.get("SKELETON_COGNEE_LLM_PROVIDER", "").strip().casefold()
    llm_model = values.get("SKELETON_COGNEE_LLM_MODEL", "").strip()
    llm_endpoint = values.get("SKELETON_COGNEE_LLM_ENDPOINT", "").strip()
    embedding_provider = values.get("SKELETON_COGNEE_EMBEDDING_PROVIDER", "").strip().casefold()
    embedding_model = values.get("SKELETON_COGNEE_EMBEDDING_MODEL", "").strip()
    dimensions_text = values.get("SKELETON_COGNEE_EMBEDDING_DIMENSIONS", "").strip()
    embedding_endpoint = values.get("SKELETON_COGNEE_EMBEDDING_ENDPOINT", "").strip() or None
    tokenizer = values.get("SKELETON_COGNEE_HUGGINGFACE_TOKENIZER", "").strip() or None
    fastembed_cache = values.get("SKELETON_COGNEE_FASTEMBED_CACHE", "").strip() or None

    if llm_provider != "ollama":
        raise CogneeLocalRuntimeError("local_llm_provider_invalid", "only local Ollama LLM is supported")
    if not llm_model or not _is_loopback_url(llm_endpoint):
        raise CogneeLocalRuntimeError("local_llm_config_invalid", "explicit loopback Ollama configuration is required")
    if embedding_provider not in {"ollama", "fastembed"}:
        raise CogneeLocalRuntimeError("local_embedding_provider_invalid", "embedding provider must be ollama or fastembed")
    if not embedding_model:
        raise CogneeLocalRuntimeError("local_embedding_model_missing", "explicit local embedding model is required")
    try:
        dimensions = int(dimensions_text)
    except ValueError as exc:
        raise CogneeLocalRuntimeError("embedding_dimensions_invalid", "embedding dimensions must be explicit") from exc
    if dimensions < 8 or dimensions > 65536:
        raise CogneeLocalRuntimeError("embedding_dimensions_invalid", "embedding dimensions are out of range")

    if embedding_provider == "ollama":
        if not embedding_endpoint or not _is_loopback_url(embedding_endpoint):
            raise CogneeLocalRuntimeError("local_embedding_endpoint_invalid", "Ollama embeddings require a loopback endpoint")
        if "/" not in embedding_model and not tokenizer:
            raise CogneeLocalRuntimeError(
                "local_tokenizer_missing",
                "non-Hugging-Face Ollama embedding models require an explicit local tokenizer",
            )
    else:
        if embedding_endpoint:
            raise CogneeLocalRuntimeError("fastembed_endpoint_forbidden", "FastEmbed must not use an HTTP endpoint")
        if not fastembed_cache:
            raise CogneeLocalRuntimeError("fastembed_cache_missing", "FastEmbed requires an explicit local cache")
        if not Path(fastembed_cache).expanduser().resolve().is_dir():
            raise CogneeLocalRuntimeError("fastembed_cache_missing", "FastEmbed cache is unavailable")

    for key, value in values.items():
        if not str(value).strip():
            continue
        upper = key.upper()
        if upper.startswith(_CLOUD_CREDENTIAL_PREFIXES):
            raise CogneeLocalRuntimeError("inherited_cloud_credentials_rejected", "cloud credentials are forbidden")
        if upper.startswith(_CLOUD_CONFIG_PREFIXES) or _STAGE_CONFIG_RE.fullmatch(upper):
            raise CogneeLocalRuntimeError("fallback_or_stage_config_rejected", "cloud/fallback model routing is forbidden")
        if upper in {"COGNEE_TELEMETRY", "SKELETON_COGNEE_TELEMETRY"} and str(value).strip().casefold() in {
            "1", "true", "yes", "on", "enabled"
        }:
            raise CogneeLocalRuntimeError("telemetry_rejected", "telemetry must remain disabled")

    return CogneeProviderConfig(
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_endpoint=llm_endpoint,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimensions=dimensions,
        embedding_endpoint=embedding_endpoint,
        huggingface_tokenizer=tokenizer,
        fastembed_cache=fastembed_cache,
    )


def _child_env(paths: CogneeRuntimePaths, config: CogneeProviderConfig) -> dict[str, str]:
    source_root = Path(__file__).resolve().parents[1]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(paths.private_root),
        "TMPDIR": str(paths.cache_dir),
        "PYTHONPATH": str(source_root),
        "TELEMETRY_DISABLED": "1",
        "ACCEPT_LOCAL_FILE_PATH": "False",
        "ALLOW_HTTP_REQUESTS": "False",
        "ALLOW_CYPHER_QUERY": "False",
        "ENABLE_BACKEND_ACCESS_CONTROL": "True",
        "REQUIRE_AUTHENTICATION": "False",
        "DB_PROVIDER": "sqlite",
        "GRAPH_DATABASE_PROVIDER": "kuzu",
        "GRAPH_DATASET_DATABASE_HANDLER": "kuzu",
        "VECTOR_DB_PROVIDER": "lancedb",
        "VECTOR_DATASET_DATABASE_HANDLER": "lancedb",
        "STORAGE_BACKEND": "local",
        "DATA_ROOT_DIRECTORY": str(paths.data_dir),
        "SYSTEM_ROOT_DIRECTORY": str(paths.system_dir),
        "LLM_PROVIDER": config.llm_provider,
        "LLM_MODEL": config.llm_model,
        "LLM_ENDPOINT": config.llm_endpoint,
        "LLM_API_KEY": "local-only",
        "EMBEDDING_PROVIDER": config.embedding_provider,
        "EMBEDDING_MODEL": config.embedding_model,
        "EMBEDDING_DIMENSIONS": str(config.embedding_dimensions),
        "EMBEDDING_API_KEY": "local-only",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "ANONYMIZED_TELEMETRY": "False",
    }
    if config.embedding_endpoint:
        env["EMBEDDING_ENDPOINT"] = config.embedding_endpoint
    if config.huggingface_tokenizer:
        env["HUGGINGFACE_TOKENIZER"] = config.huggingface_tokenizer
    if config.fastembed_cache:
        env["FASTEMBED_CACHE_PATH"] = config.fastembed_cache
    return env


def install_or_verify_pinned_cognee(
    private_root: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    installer: Callable[[list[str], Mapping[str, str]], tuple[int, str]] | None = None,
) -> dict[str, object]:
    paths = ensure_private_runtime_tree(private_root)
    config = validate_local_provider_config(env)
    python = paths.venv_dir / "bin" / "python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(paths.venv_dir)
    install_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(paths.private_root),
        "TMPDIR": str(paths.cache_dir),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }
    command = [str(python), "-m", "pip", "install", "--upgrade", COGNEE_PACKAGE_REQUIREMENT]
    if installer is None:
        def installer(cmd: list[str], child_env: Mapping[str, str]) -> tuple[int, str]:
            completed = subprocess.run(
                cmd,
                env=dict(child_env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=600,
                check=False,
            )
            return completed.returncode, completed.stdout
    code, output = installer(command, install_env)
    if code != 0:
        raise CogneeLocalRuntimeError("dependency_install_failed", "Cognee installation failed")
    if _looks_private(output):
        raise CogneeLocalRuntimeError("dependency_install_output_private", "installer output is not public-safe")
    compatibility = CogneeWorkerClient(private_root, env=env).compatibility()
    if compatibility.get("version") != PINNED_COGNEE_VERSION or compatibility.get("compatible") is not True:
        raise CogneeLocalRuntimeError("unsupported_cognee_api", "Cognee compatibility check failed")
    return {
        "dependency": COGNEE_PACKAGE_REQUIREMENT,
        "installed": True,
        "compatible": True,
        "provider_config_hash": config.fingerprint,
    }


class CogneeWorkerClient:
    def __init__(
        self,
        private_root: str | Path,
        *,
        env: Mapping[str, str] | None = None,
        runner: Callable[[list[str], str, Mapping[str, str]], tuple[int, str, str]] | None = None,
    ) -> None:
        self.paths = ensure_private_runtime_tree(private_root)
        self.config = validate_local_provider_config(env)
        self.python = self.paths.venv_dir / "bin" / "python"
        self._runner = runner

    def compatibility(self) -> dict[str, object]:
        return self._invoke("compatibility", {})

    def project(self, *, dataset_name: str, document: Mapping[str, object]) -> dict[str, object]:
        result = self._invoke("project", {"dataset_name": dataset_name, "document": dict(document)})
        self._record_projection(dataset_name, document)
        return result

    def recall(
        self,
        *,
        dataset_name: str,
        opaque_scope_hash: str,
        query: str,
        current_canonical_revision: int,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        result = self._invoke(
            "recall",
            {
                "dataset_name": dataset_name,
                "opaque_scope_hash": opaque_scope_hash,
                "query": query,
                "current_canonical_revision": current_canonical_revision,
                "limit": limit,
            },
        )
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            raise CogneeLocalRuntimeError("worker_response_invalid", "candidate response is invalid")
        return tuple(dict(item) for item in candidates if isinstance(item, Mapping))

    def health(self, *, dataset_name: str, current_canonical_revision: int) -> dict[str, object]:
        receipt = self._read_receipt(dataset_name)
        indexed_revision = int(receipt.get("indexed_canonical_revision", 0)) if receipt else 0
        event_count = int(receipt.get("event_count", 0)) if receipt else 0
        result = self._invoke(
            "health",
            {
                "dataset_name": dataset_name,
                "current_canonical_revision": current_canonical_revision,
                "indexed_canonical_revision": indexed_revision,
            },
        )
        return {
            "ready": result.get("ready") is True and indexed_revision == current_canonical_revision,
            "indexed_canonical_revision": indexed_revision,
            "event_count": event_count,
            "reason": str(result.get("reason") or "ready"),
        }

    def forget(self, *, dataset_name: str) -> int:
        receipt = self._read_receipt(dataset_name)
        count = int(receipt.get("event_count", 0)) if receipt else 0
        self._invoke("forget", {"dataset_name": dataset_name})
        self._receipt_path(dataset_name).unlink(missing_ok=True)
        return count

    def _invoke(self, operation: str, payload: Mapping[str, object]) -> dict[str, object]:
        if operation not in {"compatibility", "project", "recall", "health", "forget"}:
            raise CogneeLocalRuntimeError("worker_operation_invalid", "worker operation is invalid")
        if not self.python.is_file():
            raise CogneeLocalRuntimeError("cognee_venv_unavailable", "private Cognee runtime is unavailable")
        request = {
            "schema": COGNEE_WORKER_REQUEST_SCHEMA,
            "operation": operation,
            "payload": dict(payload),
        }
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(encoded.encode("utf-8")) > MAX_WORKER_REQUEST_BYTES:
            raise CogneeLocalRuntimeError("worker_request_oversized", "worker request is oversized")
        command = [str(self.python), "-m", "core.cognee_local_runtime", "--worker"]
        env = _child_env(self.paths, self.config)
        if self._runner is None:
            completed = subprocess.run(
                command,
                input=encoded,
                text=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=WORKER_TIMEOUT_SECONDS,
                check=False,
            )
            code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        else:
            code, stdout, stderr = self._runner(command, encoded, env)
        if code != 0:
            raise CogneeLocalRuntimeError("cognee_worker_failed", _sanitize_error(stderr or stdout))
        if len(stdout.encode("utf-8")) > MAX_WORKER_RESPONSE_BYTES:
            raise CogneeLocalRuntimeError("worker_response_oversized", "worker response is oversized")
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CogneeLocalRuntimeError("worker_response_invalid", "worker response is invalid") from exc
        if not isinstance(response, dict) or set(response) != {"schema", "ok", "result"}:
            raise CogneeLocalRuntimeError("worker_response_invalid", "worker response contract is invalid")
        if response.get("schema") != COGNEE_WORKER_RESPONSE_SCHEMA or response.get("ok") is not True:
            result = response.get("result")
            reason = result.get("reason") if isinstance(result, Mapping) else "cognee_worker_failed"
            raise CogneeLocalRuntimeError(str(reason), "Cognee worker operation failed")
        result = response.get("result")
        if not isinstance(result, dict):
            raise CogneeLocalRuntimeError("worker_response_invalid", "worker result is invalid")
        return result

    def _receipt_path(self, dataset_name: str) -> Path:
        _validate_dataset_name(dataset_name)
        return self.paths.receipts_dir / f"{dataset_name}.json"

    def _read_receipt(self, dataset_name: str) -> dict[str, object] | None:
        path = self._receipt_path(dataset_name)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def _record_projection(self, dataset_name: str, document: Mapping[str, object]) -> None:
        path = self._receipt_path(dataset_name)
        existing = self._read_receipt(dataset_name) or {}
        entries = existing.get("entries")
        if not isinstance(entries, dict):
            entries = {}
        projection_key = _canonical_hash(
            {
                "opaque_scope_hash": document.get("opaque_scope_hash"),
                "canonical_revision": document.get("canonical_revision"),
                "content_hash": document.get("content_hash"),
                "projection_text_hash": document.get("projection_text_hash"),
            }
        )
        entries[projection_key] = {
            "canonical_revision": int(document["canonical_revision"]),
            "content_hash": str(document["content_hash"]),
            "projection_text_hash": str(document["projection_text_hash"]),
            "status": "projected",
        }
        receipt = {
            "schema": "skeleton.cognee_projection_receipt_ledger.v1",
            "opaque_dataset_hash": _canonical_hash(dataset_name),
            "indexed_canonical_revision": max(
                int(item.get("canonical_revision", 0))
                for item in entries.values()
                if isinstance(item, Mapping)
            ),
            "event_count": len(entries),
            "entries": entries,
            "updated_at_epoch": int(time.time()),
        }
        _atomic_private_json(path, receipt)


def opaque_scope_hash(project_id: str, dataset_id: str) -> str:
    return _canonical_hash({"project_id": project_id, "dataset_id": dataset_id})


def opaque_dataset_name(project_id: str, dataset_id: str) -> str:
    return f"sk_{opaque_scope_hash(project_id, dataset_id)[:48]}"


def projection_document(
    *,
    project_id: str,
    dataset_id: str,
    canonical_ref: str,
    canonical_revision: int,
    content_hash: str,
    projection_text_hash: str,
    bounded_text: str,
) -> dict[str, object]:
    scope_hash = opaque_scope_hash(project_id, dataset_id)
    provenance = [
        {
            "canonical_ref": canonical_ref,
            "canonical_revision": canonical_revision,
            "value_hash": content_hash,
            "content_hash": content_hash,
            "source_kind": "canonical_sqlite",
        }
    ]
    return {
        "schema": COGNEE_PROJECTION_DOCUMENT_SCHEMA,
        "opaque_scope_hash": scope_hash,
        "canonical_ref": canonical_ref,
        "canonical_revision": canonical_revision,
        "value_hash": content_hash,
        "content_hash": content_hash,
        "projection_text_hash": projection_text_hash,
        "source_kind": "canonical_sqlite",
        "bounded_text": bounded_text,
        "provenance": provenance,
    }


def atomic_write_activation_marker(
    private_root: str | Path,
    *,
    expected_head_sha: str,
    provider_config: CogneeProviderConfig,
    enabled: bool = True,
) -> dict[str, object]:
    paths = ensure_private_runtime_tree(private_root)
    if not _SHA_RE.fullmatch(expected_head_sha or ""):
        raise CogneeLocalRuntimeError("activation_source_sha_invalid", "activation source SHA is invalid")
    marker = {
        "schema": ACTIVATION_MARKER_SCHEMA,
        "enabled": bool(enabled),
        "source_sha": expected_head_sha.lower(),
        "cognee_version": PINNED_COGNEE_VERSION,
        "provider_config_hash": provider_config.fingerprint,
        "mandatory_runner_bootstrap": bool(enabled),
        "updated_at_epoch": int(time.time()),
    }
    _atomic_private_json(paths.activation_marker, marker)
    return marker


def read_activation_marker(private_root: str | Path) -> dict[str, object] | None:
    path = cognee_runtime_paths(private_root).activation_marker
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CogneeLocalRuntimeError("activation_marker_invalid", "activation marker is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != ACTIVATION_MARKER_SCHEMA:
        raise CogneeLocalRuntimeError("activation_marker_invalid", "activation marker is invalid")
    return dict(payload)


def restore_activation_marker(private_root: str | Path, previous: Mapping[str, object] | None) -> bool:
    path = cognee_runtime_paths(private_root).activation_marker
    if previous is None:
        path.unlink(missing_ok=True)
        return not path.exists()
    _atomic_private_json(path, previous)
    return read_activation_marker(private_root) == dict(previous)


def live_aggregate_status(private_root: str | Path) -> dict[str, object]:
    paths = ensure_private_runtime_tree(private_root)
    marker = read_activation_marker(private_root)
    semantic_count = 0
    indexed_revision = 0
    for path in paths.receipts_dir.glob("sk_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            semantic_count += int(payload.get("event_count", 0))
            indexed_revision = max(indexed_revision, int(payload.get("indexed_canonical_revision", 0)))
    return {
        "activation_marker_present": marker is not None,
        "activation_enabled": bool(marker.get("enabled")) if isinstance(marker, Mapping) else False,
        "cognee_version": str(marker.get("cognee_version", "")) if isinstance(marker, Mapping) else "",
        "semantic_count": semantic_count,
        "indexed_canonical_revision": indexed_revision,
    }


def activation_receipt(
    *,
    status: str,
    reason: str,
    source_sha: str,
    booleans: Mapping[str, bool],
    counts: Mapping[str, int],
    resource_totals: Mapping[str, int],
    rollback_verified: bool,
) -> dict[str, object]:
    return {
        "schema": COGNEE_ACTIVATION_RECEIPT_SCHEMA,
        "status": status,
        "reason_codes": [_safe_reason(reason)],
        "source_sha": source_sha.lower() if _SHA_RE.fullmatch(source_sha or "") else "0" * 40,
        "dependency_versions": {"cognee": PINNED_COGNEE_VERSION},
        "backend_versions": {"runtime_schema": COGNEE_RUNTIME_SCHEMA},
        "booleans": {key: bool(value) for key, value in booleans.items()},
        "aggregate_counts": {key: _bounded_count(value) for key, value in counts.items()},
        "resource_totals": {key: _bounded_count(value, upper=10**12) for key, value in resource_totals.items()},
        "rollback": {
            "verified": bool(rollback_verified),
            "status": "verified" if rollback_verified else "not_verified",
        },
    }


def _worker_main() -> int:
    raw = sys.stdin.buffer.read(MAX_WORKER_REQUEST_BYTES + 1)
    if len(raw) > MAX_WORKER_REQUEST_BYTES:
        return _write_worker_error("worker_request_oversized")
    try:
        request = json.loads(raw.decode("utf-8"))
        operation, payload = _validate_worker_request(request)
        _install_socket_guard()
        result = asyncio.run(_dispatch_worker(operation, payload))
        response = {"schema": COGNEE_WORKER_RESPONSE_SCHEMA, "ok": True, "result": result}
    except CogneeLocalRuntimeError as exc:
        response = {
            "schema": COGNEE_WORKER_RESPONSE_SCHEMA,
            "ok": False,
            "result": {"reason": _safe_reason(exc.reason_code)},
        }
    except Exception:
        response = {
            "schema": COGNEE_WORKER_RESPONSE_SCHEMA,
            "ok": False,
            "result": {"reason": "cognee_worker_exception"},
        }
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0


def _write_worker_error(reason: str) -> int:
    sys.stdout.write(
        json.dumps(
            {
                "schema": COGNEE_WORKER_RESPONSE_SCHEMA,
                "ok": False,
                "result": {"reason": _safe_reason(reason)},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _validate_worker_request(request: object) -> tuple[str, dict[str, object]]:
    if not isinstance(request, dict) or set(request) != {"schema", "operation", "payload"}:
        raise CogneeLocalRuntimeError("worker_request_invalid", "worker request is invalid")
    if request.get("schema") != COGNEE_WORKER_REQUEST_SCHEMA:
        raise CogneeLocalRuntimeError("worker_request_invalid", "worker request schema is invalid")
    operation = request.get("operation")
    payload = request.get("payload")
    if operation not in {"compatibility", "project", "recall", "health", "forget"} or not isinstance(payload, dict):
        raise CogneeLocalRuntimeError("worker_request_invalid", "worker operation is invalid")
    allowed = {
        "compatibility": set(),
        "project": {"dataset_name", "document"},
        "recall": {"dataset_name", "opaque_scope_hash", "query", "current_canonical_revision", "limit"},
        "health": {"dataset_name", "current_canonical_revision", "indexed_canonical_revision"},
        "forget": {"dataset_name"},
    }[operation]
    if set(payload) != allowed:
        raise CogneeLocalRuntimeError("worker_request_fields_invalid", "worker payload fields are invalid")
    return str(operation), dict(payload)


async def _dispatch_worker(operation: str, payload: Mapping[str, object]) -> dict[str, object]:
    cognee = _load_cognee()
    if operation == "compatibility":
        return _compatibility(cognee)
    dataset_name = _validate_dataset_name(payload.get("dataset_name"))
    if operation == "project":
        document = _validate_projection_document(payload.get("document"))
        document_json = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        await cognee.add(
            data=document_json,
            dataset_name=dataset_name,
            incremental_loading=True,
            run_in_background=False,
            data_cache=True,
        )
        await cognee.cognify(
            datasets=[dataset_name],
            incremental_loading=True,
            run_in_background=False,
            data_cache=True,
        )
        return {"projected": True}
    if operation == "recall":
        scope_hash = _strict_hash(payload.get("opaque_scope_hash"), "opaque_scope_hash")
        query = _bounded_string(payload.get("query"), "query", 512)
        revision = _non_negative_int(payload.get("current_canonical_revision"), "current_canonical_revision")
        limit = _positive_int(payload.get("limit"), "limit", maximum=8)
        raw_results = await cognee.search(
            query_text=query,
            query_type=cognee.SearchType.CHUNKS,
            datasets=[dataset_name],
            top_k=limit,
            only_context=False,
            verbose=False,
        )
        return {"candidates": _parse_search_results(raw_results, dataset_name, scope_hash, revision, limit)}
    if operation == "health":
        current_revision = _non_negative_int(payload.get("current_canonical_revision"), "current_canonical_revision")
        indexed_revision = _non_negative_int(payload.get("indexed_canonical_revision"), "indexed_canonical_revision")
        _compatibility(cognee)
        if current_revision == 0 and indexed_revision == 0:
            return {"ready": True, "reason": "ready"}
        try:
            await cognee.search(
                query_text="skeleton memory readiness",
                query_type=cognee.SearchType.CHUNKS,
                datasets=[dataset_name],
                top_k=1,
                only_context=False,
                verbose=False,
            )
        except Exception as exc:
            raise CogneeLocalRuntimeError("cognee_health_failed", "Cognee dataset readiness failed") from exc
        return {
            "ready": indexed_revision == current_revision,
            "reason": "ready" if indexed_revision == current_revision else "projection_stale",
        }
    result = await cognee.forget(dataset=dataset_name)
    if not isinstance(result, Mapping) or result.get("status") != "success":
        raise CogneeLocalRuntimeError("cognee_forget_failed", "Cognee dataset deletion failed")
    return {"forgotten": True}


def _load_cognee() -> Any:
    version = importlib.metadata.version("cognee")
    if version != PINNED_COGNEE_VERSION:
        raise CogneeLocalRuntimeError("unsupported_cognee_version", "Cognee version is not pinned")
    module = importlib.import_module("cognee")
    _compatibility(module)
    return module


def _compatibility(cognee: Any) -> dict[str, object]:
    for name in ("add", "cognify", "search", "forget"):
        if not inspect.iscoroutinefunction(getattr(cognee, name, None)):
            raise CogneeLocalRuntimeError("unsupported_cognee_api", "Cognee async API is incompatible")
    search_type = getattr(getattr(cognee, "SearchType", None), "CHUNKS", None)
    if getattr(search_type, "value", None) != "CHUNKS":
        raise CogneeLocalRuntimeError("unsupported_cognee_api", "Cognee CHUNKS search type is incompatible")
    return {"compatible": True, "version": PINNED_COGNEE_VERSION, "chunks": True}


def _parse_search_results(
    raw_results: object,
    dataset_name: str,
    scope_hash: str,
    current_revision: int,
    limit: int,
) -> list[dict[str, object]]:
    if raw_results in (None, []):
        return []
    if not isinstance(raw_results, list) or len(raw_results) != 1:
        raise CogneeLocalRuntimeError("cognee_search_shape_invalid", "Cognee search result shape is invalid")
    envelope = raw_results[0]
    if not isinstance(envelope, Mapping):
        raise CogneeLocalRuntimeError("cognee_search_shape_invalid", "Cognee search envelope is invalid")
    allowed_keys = {"dataset_id", "dataset_name", "dataset_tenant_id", "search_result"}
    if set(envelope) - allowed_keys or envelope.get("dataset_name") != dataset_name:
        raise CogneeLocalRuntimeError("cognee_search_scope_invalid", "Cognee returned a foreign dataset")
    payloads = envelope.get("search_result")
    if not isinstance(payloads, list):
        raise CogneeLocalRuntimeError("cognee_search_shape_invalid", "Cognee search payload is invalid")
    candidates: list[dict[str, object]] = []
    for index, payload in enumerate(payloads[:limit]):
        if not isinstance(payload, Mapping) or set(payload) - {"text", "score", "id", "type"}:
            raise CogneeLocalRuntimeError("cognee_search_payload_invalid", "Cognee chunk payload is invalid")
        text = payload.get("text")
        if not isinstance(text, str):
            raise CogneeLocalRuntimeError("cognee_search_payload_invalid", "Cognee chunk text is invalid")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CogneeLocalRuntimeError("cognee_projection_document_invalid", "Cognee projection document is invalid") from exc
        checked = _validate_projection_document(document)
        if checked["opaque_scope_hash"] != scope_hash:
            raise CogneeLocalRuntimeError("cognee_search_scope_invalid", "Cognee result scope is invalid")
        revision = int(checked["canonical_revision"])
        if revision > current_revision:
            raise CogneeLocalRuntimeError("projection_stale", "Cognee returned a future revision")
        candidates.append(
            {
                "canonical_ref": checked["canonical_ref"],
                "canonical_revision": revision,
                "content_hash": checked["content_hash"],
                "projection_text_hash": checked["projection_text_hash"],
                "score": float(payload.get("score", max(0.0, 1.0 - index / max(1, limit)))),
                "provenance": checked["provenance"],
            }
        )
    return candidates


def _validate_projection_document(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CogneeLocalRuntimeError("projection_document_invalid", "projection document must be an object")
    required = {
        "schema", "opaque_scope_hash", "canonical_ref", "canonical_revision",
        "value_hash", "content_hash", "projection_text_hash", "source_kind",
        "bounded_text", "provenance",
    }
    if set(value) != required or value.get("schema") != COGNEE_PROJECTION_DOCUMENT_SCHEMA:
        raise CogneeLocalRuntimeError("projection_document_invalid", "projection document schema is invalid")
    scope_hash = _strict_hash(value.get("opaque_scope_hash"), "opaque_scope_hash")
    canonical_ref = _bounded_string(value.get("canonical_ref"), "canonical_ref", 256)
    revision = _positive_int(value.get("canonical_revision"), "canonical_revision")
    value_hash = _strict_hash(value.get("value_hash"), "value_hash")
    content_hash = _strict_hash(value.get("content_hash"), "content_hash")
    if value_hash != content_hash:
        raise CogneeLocalRuntimeError("projection_document_hash_invalid", "canonical hash bindings disagree")
    text_hash = _strict_hash(value.get("projection_text_hash"), "projection_text_hash")
    bounded_text = _bounded_string(value.get("bounded_text"), "bounded_text", 4096)
    if hashlib.sha256(bounded_text.encode("utf-8")).hexdigest() != text_hash:
        raise CogneeLocalRuntimeError("projection_text_hash_mismatch", "projection text hash is invalid")
    if value.get("source_kind") != "canonical_sqlite":
        raise CogneeLocalRuntimeError("projection_source_invalid", "projection source is invalid")
    provenance = value.get("provenance")
    expected = {
        "canonical_ref": canonical_ref,
        "canonical_revision": revision,
        "value_hash": content_hash,
        "content_hash": content_hash,
        "source_kind": "canonical_sqlite",
    }
    if not isinstance(provenance, list) or provenance != [expected]:
        raise CogneeLocalRuntimeError("projection_provenance_invalid", "projection provenance is invalid")
    return {
        "schema": COGNEE_PROJECTION_DOCUMENT_SCHEMA,
        "opaque_scope_hash": scope_hash,
        "canonical_ref": canonical_ref,
        "canonical_revision": revision,
        "value_hash": value_hash,
        "content_hash": content_hash,
        "projection_text_hash": text_hash,
        "source_kind": "canonical_sqlite",
        "bounded_text": bounded_text,
        "provenance": [expected],
    }


def _install_socket_guard() -> None:
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def validate_address(address: object) -> None:
        if isinstance(address, str):
            return
        if not isinstance(address, tuple) or not address:
            raise OSError("non-loopback network blocked")
        host = str(address[0]).strip("[]")
        if host.casefold() == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise OSError("non-loopback network blocked")

    class GuardedSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, address: object) -> None:
            validate_address(address)
            return super().connect(address)  # type: ignore[arg-type]

        def connect_ex(self, address: object) -> int:
            validate_address(address)
            return super().connect_ex(address)  # type: ignore[arg-type]

    def guarded_create_connection(address: object, *args: object, **kwargs: object) -> socket.socket:
        validate_address(address)
        return original_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> list[tuple[Any, ...]]:
        text = str(host).strip("[]")
        if text.casefold() != "localhost":
            try:
                if not ipaddress.ip_address(text).is_loopback:
                    raise OSError("DNS/non-loopback network blocked")
            except ValueError as exc:
                raise OSError("DNS/non-loopback network blocked") from exc
        results = original_getaddrinfo(host, *args, **kwargs)
        for item in results:
            validate_address(item[4])
        return results

    socket.socket = GuardedSocket  # type: ignore[assignment]
    socket.create_connection = guarded_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]


def _validate_dataset_name(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sk_[0-9a-f]{48}", value) is None:
        raise CogneeLocalRuntimeError("dataset_name_invalid", "opaque dataset name is invalid")
    return value


def _strict_hash(value: object, field: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value.casefold()) is None:
        raise CogneeLocalRuntimeError("invalid_hash", f"{field} is invalid")
    return value.casefold()


def _bounded_string(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CogneeLocalRuntimeError("invalid_text", f"{field} is invalid")
    if any(ord(char) < 32 for char in value):
        raise CogneeLocalRuntimeError("invalid_text", f"{field} contains control characters")
    return value


def _positive_int(value: object, field: str, maximum: int = 2**31 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise CogneeLocalRuntimeError("invalid_integer", f"{field} is invalid")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CogneeLocalRuntimeError("invalid_integer", f"{field} is invalid")
    return value


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_private_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(name)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def _is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    host = parsed.hostname.strip("[]")
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _sanitize_error(value: str) -> str:
    del value
    return "Cognee worker failed"


def _looks_private(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in ("/home/", "/tmp/", "api_key", "secret", "token="))


def _safe_reason(value: object) -> str:
    text = str(value or "blocked")
    return text if _SAFE_REASON_RE.fullmatch(text) else "blocked"


def _bounded_count(value: object, *, upper: int = 1_000_000) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= upper else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args(argv)
    if not args.worker:
        parser.error("--worker is required")
    return _worker_main()


if __name__ == "__main__":
    raise SystemExit(main())

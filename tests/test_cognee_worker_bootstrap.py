from __future__ import annotations

import os
import runpy
from pathlib import Path

from core.cognee_worker_bootstrap import configure_cognee_worker_environment

ROOT = Path(__file__).resolve().parents[1]


def _worker_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "private"
    return {
        "HOME": str(home),
        "DATA_ROOT_DIRECTORY": str(home / "data"),
        "SYSTEM_ROOT_DIRECTORY": str(home / "system"),
        "LLM_PROVIDER": "ollama",
        "LLM_MODEL": "qwen2.5:1.5b",
        "LLM_ENDPOINT": "http://127.0.0.1:11434/v1",
        "EMBEDDING_PROVIDER": "ollama",
        "EMBEDDING_MODEL": "nomic-embed-text:latest",
        "EMBEDDING_ENDPOINT": "http://127.0.0.1:11434/api/embed",
        "DB_PROVIDER": "sqlite",
        "GRAPH_DATABASE_PROVIDER": "kuzu",
        "VECTOR_DB_PROVIDER": "lancedb",
        "ALLOW_HTTP_REQUESTS": "False",
        "REQUIRE_AUTHENTICATION": "False",
        "ENABLE_BACKEND_ACCESS_CONTROL": "True",
    }


def test_bootstrap_applies_exact_local_worker_profile(tmp_path: Path) -> None:
    env = _worker_env(tmp_path)
    assert configure_cognee_worker_environment(env) is True
    assert env["ENABLE_BACKEND_ACCESS_CONTROL"] == "False"
    assert env["CACHING"] == "False"
    assert env["LLM_INSTRUCTOR_MODE"] == "json_schema_mode"
    assert env["LLM_MODEL"] == "qwen2.5:1.5b"
    assert env["LLM_ENDPOINT"] == "http://127.0.0.1:11434/v1"
    assert env["EMBEDDING_ENDPOINT"] == "http://127.0.0.1:11434/api/embed"


def test_bootstrap_is_noop_outside_exact_worker_fingerprint(tmp_path: Path) -> None:
    env = _worker_env(tmp_path)
    env["LLM_ENDPOINT"] = "https://example.invalid/v1"
    before = dict(env)
    assert configure_cognee_worker_environment(env) is False
    assert env == before


def test_repository_sitecustomize_delegates_to_closed_bootstrap(
    monkeypatch, tmp_path: Path
) -> None:
    for key, value in _worker_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    runpy.run_path(str(ROOT / "sitecustomize.py"))
    assert os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] == "False"
    assert os.environ["CACHING"] == "False"
    assert os.environ["LLM_INSTRUCTOR_MODE"] == "json_schema_mode"


def test_repository_sitecustomize_is_noop_for_normal_python(monkeypatch) -> None:
    monkeypatch.delenv("DATA_ROOT_DIRECTORY", raising=False)
    monkeypatch.setenv("ENABLE_BACKEND_ACCESS_CONTROL", "keep")
    before = dict(os.environ)
    runpy.run_path(str(ROOT / "sitecustomize.py"))
    assert os.environ == before

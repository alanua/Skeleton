from __future__ import annotations

import asyncio
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.cognee_worker_bootstrap import (
    configure_cognee_worker_environment,
    install_cognee_operation_wrappers,
)

ROOT = Path(__file__).resolve().parents[1]


class WorkerError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


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


@pytest.mark.parametrize(
    ("operation", "expected_reason"),
    (
        ("add", "cognee_add_exception"),
        ("cognify", "cognee_cognify_exception"),
        ("search", "cognee_search_exception"),
        ("forget", "cognee_forget_exception"),
    ),
)
def test_operation_wrappers_map_unknown_failures_to_safe_stage(
    monkeypatch, operation: str, expected_reason: str
) -> None:
    async def failing_operation(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("private worker detail")

    module = SimpleNamespace(**{operation: failing_operation})
    monkeypatch.setattr(
        sys.modules["__main__"], "CogneeLocalRuntimeError", WorkerError, raising=False
    )
    assert install_cognee_operation_wrappers(module) is True

    with pytest.raises(WorkerError) as caught:
        asyncio.run(getattr(module, operation)())
    assert caught.value.reason_code == expected_reason
    assert "private worker detail" not in str(caught.value)


def test_operation_wrapper_preserves_existing_safe_reason(monkeypatch) -> None:
    error = WorkerError("existing_safe_reason", "bounded")

    async def failing_search():
        raise error

    module = SimpleNamespace(search=failing_search)
    monkeypatch.setattr(
        sys.modules["__main__"], "CogneeLocalRuntimeError", WorkerError, raising=False
    )
    assert install_cognee_operation_wrappers(module) is True

    with pytest.raises(WorkerError) as caught:
        asyncio.run(module.search())
    assert caught.value is error


def test_operation_wrapper_installation_is_idempotent() -> None:
    async def add():
        return None

    module = SimpleNamespace(add=add)
    assert install_cognee_operation_wrappers(module) is True
    wrapped = module.add
    assert install_cognee_operation_wrappers(module) is False
    assert module.add is wrapped


def test_add_wrapper_normalizes_only_stale_compatibility_arguments() -> None:
    captured: dict[str, object] = {}

    async def add(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "added"

    module = SimpleNamespace(add=add)
    assert install_cognee_operation_wrappers(module) is True
    original_data = ["synthetic projection"]
    original_kwargs = {
        "dataset_name": "sk_" + "a" * 48,
        "incremental_loading": False,
        "data_cache": True,
        "run_in_background": False,
    }

    result = asyncio.run(module.add(original_data, **original_kwargs))

    assert result == "added"
    assert captured["args"] == ("synthetic projection",)
    assert captured["kwargs"] == {
        "dataset_name": "sk_" + "a" * 48,
        "incremental_loading": False,
    }
    assert original_data == ["synthetic projection"]
    assert original_kwargs["data_cache"] is True
    assert original_kwargs["run_in_background"] is False


def test_add_wrapper_normalizes_keyword_data_without_mutation() -> None:
    captured: dict[str, object] = {}

    async def add(**kwargs):
        captured.update(kwargs)
        return "added"

    module = SimpleNamespace(add=add)
    assert install_cognee_operation_wrappers(module) is True
    original_data = ("synthetic projection",)

    result = asyncio.run(
        module.add(
            data=original_data,
            dataset_name="sk_" + "b" * 48,
            incremental_loading=True,
            data_cache=True,
            run_in_background=False,
        )
    )

    assert result == "added"
    assert captured == {
        "data": "synthetic projection",
        "dataset_name": "sk_" + "b" * 48,
        "incremental_loading": True,
    }
    assert original_data == ("synthetic projection",)


def test_cognify_wrapper_removes_only_stale_compatibility_arguments() -> None:
    captured: dict[str, object] = {}

    async def cognify(**kwargs):
        captured.update(kwargs)
        return "cognified"

    module = SimpleNamespace(cognify=cognify)
    assert install_cognee_operation_wrappers(module) is True
    datasets = ["sk_" + "c" * 48]

    result = asyncio.run(
        module.cognify(
            datasets=datasets,
            incremental_loading=False,
            data_cache=True,
            run_in_background=False,
        )
    )

    assert result == "cognified"
    assert captured == {
        "datasets": datasets,
        "incremental_loading": False,
    }

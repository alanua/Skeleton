from __future__ import annotations

from pathlib import Path

from core.cognee_internal_file_access import enable_cognee_internal_file_access


def _worker_env(root: Path) -> dict[str, str]:
    return {
        "HOME": str(root),
        "TMPDIR": str(root / "cache"),
        "DATA_ROOT_DIRECTORY": str(root / "data"),
        "SYSTEM_ROOT_DIRECTORY": str(root / "system"),
        "LLM_PROVIDER": "ollama",
        "LLM_ENDPOINT": "http://127.0.0.1:11434/v1",
        "EMBEDDING_PROVIDER": "ollama",
        "EMBEDDING_ENDPOINT": "http://127.0.0.1:11434/api/embed",
        "DB_PROVIDER": "sqlite",
        "GRAPH_DATABASE_PROVIDER": "kuzu",
        "VECTOR_DB_PROVIDER": "lancedb",
        "STORAGE_BACKEND": "local",
        "ALLOW_HTTP_REQUESTS": "False",
        "ALLOW_CYPHER_QUERY": "False",
        "ENABLE_BACKEND_ACCESS_CONTROL": "True",
        "REQUIRE_AUTHENTICATION": "False",
        "ACCEPT_LOCAL_FILE_PATH": "False",
        "TELEMETRY_DISABLED": "1",
    }


def test_exact_isolated_worker_enables_only_internal_file_access(tmp_path: Path) -> None:
    env = _worker_env(tmp_path / "private")
    before = dict(env)

    assert enable_cognee_internal_file_access(env) is True
    assert env["ACCEPT_LOCAL_FILE_PATH"] == "True"
    assert {key: value for key, value in env.items() if key != "ACCEPT_LOCAL_FILE_PATH"} == {
        key: value for key, value in before.items() if key != "ACCEPT_LOCAL_FILE_PATH"
    }


def test_external_endpoint_fails_closed(tmp_path: Path) -> None:
    env = _worker_env(tmp_path / "private")
    env["LLM_ENDPOINT"] = "https://example.invalid/v1"

    assert enable_cognee_internal_file_access(env) is False
    assert env["ACCEPT_LOCAL_FILE_PATH"] == "False"


def test_root_outside_private_home_fails_closed(tmp_path: Path) -> None:
    env = _worker_env(tmp_path / "private")
    env["DATA_ROOT_DIRECTORY"] = str(tmp_path / "outside")

    assert enable_cognee_internal_file_access(env) is False
    assert env["ACCEPT_LOCAL_FILE_PATH"] == "False"


def test_wrong_worker_fingerprint_fails_closed(tmp_path: Path) -> None:
    env = _worker_env(tmp_path / "private")
    env["STORAGE_BACKEND"] = "remote"

    assert enable_cognee_internal_file_access(env) is False
    assert env["ACCEPT_LOCAL_FILE_PATH"] == "False"


def test_pre_enabled_value_is_not_accepted(tmp_path: Path) -> None:
    env = _worker_env(tmp_path / "private")
    env["ACCEPT_LOCAL_FILE_PATH"] = "True"

    assert enable_cognee_internal_file_access(env) is False
    assert env["ACCEPT_LOCAL_FILE_PATH"] == "True"

from __future__ import annotations

from pathlib import Path

from core.runner_child_environment import sanitize_codegen_child_environment


def test_sanitize_codegen_child_environment_removes_only_home_edge_prefix() -> None:
    environment = {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "SKELETON_HOME_EDGE_01_HOSTNAME": "live-home-edge",
        "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE": "/private/key",
        "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "synthetic-hmac-marker",
        "SKELETON_UNRELATED_SETTING": "kept-skeleton-value",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
        "UNRELATED_HOME_EDGE_01_VALUE": "kept",
        "ARBITRARY_OVERLAY_VALUE": "kept-overlay-value",
    }

    sanitized = sanitize_codegen_child_environment(environment)

    assert sanitized == {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "SKELETON_UNRELATED_SETTING": "kept-skeleton-value",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
        "UNRELATED_HOME_EDGE_01_VALUE": "kept",
        "ARBITRARY_OVERLAY_VALUE": "kept-overlay-value",
    }
    assert environment["SKELETON_HOME_EDGE_01_HOSTNAME"] == "live-home-edge"
    assert environment["SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"] == "synthetic-hmac-marker"
    assert environment["SKELETON_UNRELATED_SETTING"] == "kept-skeleton-value"


def test_provider_shim_is_installed_only_from_trusted_runtime_tools(
    tmp_path: Path, monkeypatch,
) -> None:
    import core.runner_child_environment as module

    tool_paths = {
        "codex": "/trusted/bin/codex",
        "openhands": "/trusted/bin/openhands",
    }
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name, path=None: tool_paths.get(name),
    )
    monkeypatch.setattr(module, "should_attempt_codex_runtime_recovery", lambda _env: False)

    shim_dir = tmp_path / "shim"
    sanitized = sanitize_codegen_child_environment(
        {
            "PATH": "/trusted/bin:/usr/bin",
            "SKELETON_CODEGEN_PROVIDER_SHIM_DIR": str(shim_dir),
        }
    )

    shim = shim_dir / "codex"
    assert shim.is_file()
    assert shim.stat().st_mode & 0o777 == 0o700
    text = shim.read_text(encoding="utf-8")
    assert "provider_fallback=openhands" in text
    assert "you've hit your usage limit" in text
    assert "--headless" in text
    assert sanitized["SKELETON_REAL_CODEX_BIN"] == "/trusted/bin/codex"
    assert sanitized["SKELETON_OPENHANDS_BIN"] == "/trusted/bin/openhands"
    assert sanitized["PATH"].startswith(f"{shim_dir}:")


def test_provider_shim_not_installed_without_openhands(tmp_path: Path, monkeypatch) -> None:
    import core.runner_child_environment as module

    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name, path=None: "/trusted/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(module, "should_attempt_codex_runtime_recovery", lambda _env: False)

    shim_dir = tmp_path / "shim"
    sanitized = sanitize_codegen_child_environment(
        {
            "PATH": "/trusted/bin:/usr/bin",
            "SKELETON_CODEGEN_PROVIDER_SHIM_DIR": str(shim_dir),
        }
    )

    assert sanitized["PATH"] == "/trusted/bin:/usr/bin"
    assert "SKELETON_REAL_CODEX_BIN" not in sanitized
    assert "SKELETON_OPENHANDS_BIN" not in sanitized
    assert not (shim_dir / "codex").exists()

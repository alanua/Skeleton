from __future__ import annotations

from pathlib import Path

import core.runner_child_environment as child_env
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


def test_codegen_environment_installs_fixed_fallback_only_when_both_tools_exist(
    tmp_path: Path, monkeypatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    codex.write_text("", encoding="utf-8")
    openhands.write_text("", encoding="utf-8")

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        if name == "codex":
            return str(codex)
        if name == "openhands":
            return str(openhands)
        return None

    monkeypatch.setattr(child_env.shutil, "which", fake_which)
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)

    sanitized = sanitize_codegen_child_environment(
        {
            "HOME": str(tmp_path),
            "PATH": str(bin_dir),
            "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "must-not-survive",
        }
    )

    wrapper_dir = tmp_path / ".local" / "state" / "skeleton-runner" / "codegen-fallback-bin"
    wrapper = wrapper_dir / "codex"
    assert wrapper.is_file()
    assert sanitized["PATH"].split(":", 1)[0] == str(wrapper_dir)
    assert sanitized["SKELETON_REAL_CODEX_BIN"] == str(codex.resolve())
    assert sanitized["SKELETON_OPENHANDS_BIN"] == str(openhands.resolve())
    assert "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET" not in sanitized
    assert wrapper.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")

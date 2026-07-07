from __future__ import annotations

from core.runner_child_environment import sanitize_codegen_child_environment


def test_sanitize_codegen_child_environment_removes_only_home_edge_prefix() -> None:
    environment = {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "SKELETON_HOME_EDGE_01_HOSTNAME": "live-home-edge",
        "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE": "/private/key",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
        "UNRELATED_HOME_EDGE_01_VALUE": "kept",
    }

    sanitized = sanitize_codegen_child_environment(environment)

    assert sanitized == {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
        "UNRELATED_HOME_EDGE_01_VALUE": "kept",
    }
    assert environment["SKELETON_HOME_EDGE_01_HOSTNAME"] == "live-home-edge"

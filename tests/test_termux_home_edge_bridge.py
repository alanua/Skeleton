from pathlib import Path

import pytest

from scripts.termux_home_edge_bridge import BridgeConfig, build_controller_argv


def config(tmp_path: Path) -> BridgeConfig:
    controller = tmp_path / "home_edge_exec.py"
    controller.write_text("# fixture\n", encoding="utf-8")
    return BridgeConfig(
        phone_node_id="android-phone-fixture",
        home_edge_node_id="home-edge-01",
        controller_path=controller,
    )


def test_builds_bounded_read_only_probe(tmp_path: Path):
    argv = build_controller_argv(
        config(tmp_path),
        "whoami",
        request_id="req-fixture",
        idempotency_key="idem-fixture",
        timeout=10,
    )

    assert "read_only" in argv
    assert "desktop-user" in argv
    assert argv[-1] == "whoami"
    assert "/bin/sh" not in argv
    assert "ssh" not in argv


def test_rejects_arbitrary_probe(tmp_path: Path):
    with pytest.raises(RuntimeError, match="unsupported probe"):
        build_controller_argv(
            config(tmp_path),
            "rm",
            request_id="req-fixture",
            idempotency_key="idem-fixture",
            timeout=10,
        )


def test_requires_request_and_idempotency_ids(tmp_path: Path):
    with pytest.raises(RuntimeError, match="required"):
        build_controller_argv(
            config(tmp_path),
            "whoami",
            request_id="",
            idempotency_key="idem-fixture",
            timeout=10,
        )


def test_rejects_unbounded_timeout(tmp_path: Path):
    with pytest.raises(RuntimeError, match="between 1 and 30"):
        build_controller_argv(
            config(tmp_path),
            "whoami",
            request_id="req-fixture",
            idempotency_key="idem-fixture",
            timeout=31,
        )

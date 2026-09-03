import json
from pathlib import Path

import pytest

from scripts.termux_home_edge_bridge import BridgeConfig, build_controller_argv, run_probe


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
    assert "--timeout-seconds" in argv
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


def test_config_fails_closed_without_phone_identity(monkeypatch, tmp_path: Path):
    controller = tmp_path / "home_edge_exec.py"
    controller.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.delenv("SKELETON_PHONE_NODE_ID", raising=False)
    monkeypatch.setenv("SKELETON_HOME_EDGE_NODE_ID", "home-edge-01")
    monkeypatch.setenv("SKELETON_HOME_EDGE_CONTROLLER", str(controller))

    with pytest.raises(RuntimeError, match="SKELETON_PHONE_NODE_ID"):
        BridgeConfig.from_env()


def test_config_rejects_wrong_home_edge_identity(monkeypatch, tmp_path: Path):
    controller = tmp_path / "home_edge_exec.py"
    controller.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setenv("SKELETON_PHONE_NODE_ID", "android-phone-fixture")
    monkeypatch.setenv("SKELETON_HOME_EDGE_NODE_ID", "wrong-node")
    monkeypatch.setenv("SKELETON_HOME_EDGE_CONTROLLER", str(controller))

    with pytest.raises(RuntimeError, match="home-edge-01"):
        BridgeConfig.from_env()


def test_run_probe_accepts_only_ok_json_receipt(monkeypatch, tmp_path: Path):
    class Completed:
        returncode = 0
        stdout = json.dumps({"status": "ok", "receipt_hash": "fixture"})

    monkeypatch.setattr("scripts.termux_home_edge_bridge.subprocess.run", lambda *args, **kwargs: Completed())

    receipt = run_probe(config(tmp_path), ["fixture-controller"])
    assert receipt["status"] == "ok"


def test_run_probe_rejects_non_ok_receipt(monkeypatch, tmp_path: Path):
    class Completed:
        returncode = 0
        stdout = json.dumps({"status": "blocked"})

    monkeypatch.setattr("scripts.termux_home_edge_bridge.subprocess.run", lambda *args, **kwargs: Completed())

    with pytest.raises(RuntimeError, match="did not return an ok receipt"):
        run_probe(config(tmp_path), ["fixture-controller"])

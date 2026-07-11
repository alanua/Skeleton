from __future__ import annotations

import json
import os
import pwd
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.home_edge.executor import (
    HomeEdgeExecEngine,
    HomeEdgeExecError,
    HomeEdgeExecRequest,
    ExecutionUser,
    _CompletedProcess,
    _child_environment,
    sign_request,
)

SECRET = "private-secret"


@pytest.fixture(autouse=True)
def desktop_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_DESKTOP_USER", pwd.getpwuid(os.geteuid()).pw_name)


def request(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "request_id": "req-1",
        "node_id": "home-edge-01",
        "argv": [sys.executable, "-c", "print('ok')"],
        "timeout_seconds": 5,
        "execution_lane": "read_only",
        "idempotency_key": "idem-1",
        "run_as": "desktop-user",
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": "nonce-1",
    }
    data.update(overrides)
    return data


def signed_request(**overrides: object) -> dict[str, object]:
    data = request(**overrides)
    data["signature"] = sign_request(HomeEdgeExecRequest.from_mapping(data), SECRET)
    return data


def engine(tmp_path: Path, **overrides: object) -> HomeEdgeExecEngine:
    values = {
        "audit_log": tmp_path / "audit.jsonl",
        "idempotency_cache": tmp_path / "state.json",
        "hmac_secret": SECRET,
        "current_user": ExecutionUser.DESKTOP_USER,
    }
    values.update(overrides)
    return HomeEdgeExecEngine(**values)


def test_arbitrary_argv_execution_and_audit(tmp_path: Path) -> None:
    exec_engine = engine(tmp_path)

    receipt = exec_engine.execute(
        signed_request(
            argv=[sys.executable, "-c", "import os; print(os.environ['TERM'])"],
            environment={"TERM": "xterm-private"},
        )
    )

    assert receipt.status == "ok"
    assert receipt.stdout.strip() == "xterm-private"
    assert receipt.exit_code == 0
    assert (tmp_path / "audit.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_script_mode_with_stdin_and_bounded_output(tmp_path: Path) -> None:
    exec_engine = engine(tmp_path, audit_log=None)

    receipt = exec_engine.execute(
        signed_request(
            mode="script",
            argv=[],
            script="import sys; print(sys.stdin.read()); print('x' * 100)",
            script_interpreter="python3",
            stdin_text="hello",
            max_output_bytes=1024,
        )
    )

    assert receipt.status == "ok"
    assert "hello" in receipt.stdout
    assert "x" * 100 in receipt.stdout


def test_timeout_returns_bounded_receipt(tmp_path: Path) -> None:
    exec_engine = engine(tmp_path, audit_log=None)

    receipt = exec_engine.execute(signed_request(argv=[sys.executable, "-c", "import time; time.sleep(2)"], timeout_seconds=1))

    assert receipt.status == "timeout"
    assert receipt.error == "timeout after 1s"


def test_cancel_file_terminates_running_process(tmp_path: Path) -> None:
    cancel_dir = tmp_path / "cancel"
    cancel_dir.mkdir()
    cancel_file = cancel_dir / "cancel-me.cancel"
    exec_engine = engine(tmp_path, audit_log=None, cancel_dir=cancel_dir)
    script = (
        "import pathlib, sys, time; "
        f"pathlib.Path({str(cancel_file)!r}).write_text('1'); "
        "time.sleep(5)"
    )

    receipt = exec_engine.execute(
        signed_request(
            request_id="cancel-me",
            idempotency_key="cancel-key",
            nonce="cancel-nonce",
            argv=[sys.executable, "-c", script],
            timeout_seconds=5,
        )
    )

    assert receipt.status == "cancelled"
    assert receipt.error == "cancelled"
    assert "[cancelled]" in receipt.stderr


def test_root_lane_command_prefix_is_universal_not_handler() -> None:
    parsed = HomeEdgeExecRequest.from_mapping(
        request(execution_lane="privileged_mutation", operator_approval_ref="approval", run_as="root", argv=["id", "-u"])
    )

    assert parsed.command_argv(current_user=ExecutionUser.DESKTOP_USER)[:3] == ["sudo", "--non-interactive", "--"]


def test_non_root_root_request_uses_non_interactive_sudo_and_fails_normally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exec_engine = engine(tmp_path, current_user=ExecutionUser.DESKTOP_USER, audit_log=None)
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return _CompletedProcess(returncode=127, stdout=b"", stderr=b"sudo: command not found\n")

    monkeypatch.setattr("core.home_edge.executor._run_bounded_process", fake_run)
    receipt = exec_engine.execute(
        signed_request(
            request_id="sudo-missing",
            idempotency_key="sudo-missing",
            nonce="sudo-missing",
            run_as="root",
            operator_approval_ref="root-read-only:approval",
            argv=["id", "-u"],
        )
    )

    assert commands[0][:3] == ["sudo", "--non-interactive", "--"]
    assert receipt.status == "failed"
    assert "sudo: command not found" in receipt.stderr


def test_destructive_requires_operator_approval() -> None:
    with pytest.raises(HomeEdgeExecError, match="operator approval"):
        HomeEdgeExecRequest.from_mapping(request(execution_lane="destructive", run_as="root"))


def test_wrong_node_rejects_before_execution(tmp_path: Path) -> None:
    exec_engine = engine(tmp_path)

    with pytest.raises(HomeEdgeExecError, match="node_id"):
        exec_engine.execute({**request(node_id="other-node"), "signature": "sha256=" + "0" * 64})
    assert not (tmp_path / "audit.jsonl").exists()


def test_unsigned_stale_bad_signature_and_replayed_requests_reject_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exec_engine = engine(tmp_path)
    launches = 0

    def fake_run(*_args, **_kwargs):
        nonlocal launches
        launches += 1
        return _CompletedProcess(returncode=0, stdout=b"once\n", stderr=b"")

    monkeypatch.setattr("core.home_edge.executor._run_bounded_process", fake_run)
    unsigned = request()
    with pytest.raises(HomeEdgeExecError, match="timestamp, nonce and signature"):
        exec_engine.execute(unsigned)

    stale = signed_request(timestamp=(datetime.now(UTC) - timedelta(hours=1)).isoformat(), nonce="stale")
    with pytest.raises(HomeEdgeExecError, match="stale"):
        exec_engine.execute(stale)

    bad = signed_request(nonce="bad")
    bad["signature"] = "sha256:" + "0" * 64
    with pytest.raises(HomeEdgeExecError, match="signature mismatch"):
        exec_engine.execute(bad)

    public_changed = signed_request(nonce="public-changed", idempotency_key="public-changed", public=False)
    public_changed["public"] = True
    with pytest.raises(HomeEdgeExecError, match="signature mismatch"):
        exec_engine.execute(public_changed)

    fresh = signed_request(argv=[sys.executable, "-c", "print('once')"])
    first = exec_engine.execute(fresh)
    assert first.idempotency == "executed"
    replayed_without_idempotency = signed_request(idempotency_key=None, nonce="nonce-unique")
    exec_engine.execute(replayed_without_idempotency)
    with pytest.raises(HomeEdgeExecError, match="nonce was already used"):
        exec_engine.execute(replayed_without_idempotency)
    assert launches == 2


def test_idempotent_replay_requires_same_payload_digest(tmp_path: Path) -> None:
    exec_engine = engine(tmp_path, audit_log=None)

    first = signed_request(idempotency_key="same-key", nonce="same-nonce", argv=[sys.executable, "-c", "print('one')"])
    replay = dict(first)
    exec_engine.execute(first)
    assert exec_engine.execute(replay).idempotency == "replayed"

    changed = signed_request(idempotency_key="same-key", nonce="other-nonce", argv=[sys.executable, "-c", "print('two')"])
    with pytest.raises(HomeEdgeExecError, match="different payload"):
        exec_engine.execute(changed)


def test_concurrent_identical_idempotent_requests_execute_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exec_engine = engine(tmp_path, audit_log=None)
    payload = signed_request(idempotency_key="concurrent", nonce="concurrent-nonce")
    launches = 0

    def fake_run(*_args, **_kwargs):
        nonlocal launches
        launches += 1
        return _CompletedProcess(returncode=0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr("core.home_edge.executor._run_bounded_process", fake_run)
    results = []
    threads = [threading.Thread(target=lambda: results.append(exec_engine.execute(payload).idempotency)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert launches == 1
    assert sorted(results) == ["executed", "replayed"]


def test_private_words_allowed_but_public_receipt_hides_private_fields(tmp_path: Path) -> None:
    exec_engine = engine(tmp_path, audit_log=None)

    private = exec_engine.execute(signed_request(argv=[sys.executable, "-c", "print('/private/path token=abc')"]))
    public = exec_engine.execute(
        signed_request(
            request_id="public-1",
            idempotency_key="idem-public",
            nonce="nonce-public",
            public=True,
            cwd="/private/work",
            stdin_text="secret-stdin",
            environment={"TERM": "secret-env"},
            argv=[sys.executable, "-c", "print('token=abc'); import sys; print('/private/error', file=sys.stderr)"],
        )
    )

    public_mapping = public.to_mapping()
    serialized = json.dumps(public_mapping, sort_keys=True)
    assert "/private/path token=abc" in private.to_mapping()["stdout"]
    assert "stdout" not in public_mapping
    assert "stderr" not in public_mapping
    assert "argv" not in public_mapping
    assert "token=abc" not in serialized
    assert "secret-stdin" not in serialized
    assert "secret-env" not in serialized
    assert "/private" not in serialized


def test_request_schema_rejects_both_stdin_forms() -> None:
    with pytest.raises(HomeEdgeExecError, match="stdin_text or stdin_base64"):
        HomeEdgeExecRequest.from_mapping(request(stdin_text="a", stdin_base64="Yg=="))


def test_idempotency_cache_is_private_file(tmp_path: Path) -> None:
    cache = tmp_path / "state.json"
    exec_engine = engine(tmp_path, audit_log=None, idempotency_cache=cache)

    exec_engine.execute(signed_request())

    if os.name == "posix":
        assert oct(cache.stat().st_mode & 0o777) == "0o600"
    assert json.loads(cache.read_text(encoding="utf-8"))["idempotency"]["idem-1"]["receipt"]["status"] == "ok"


def test_root_process_switches_desktop_request_instead_of_running_as_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exec_engine = engine(tmp_path, current_user=ExecutionUser.ROOT, audit_log=None)
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return _CompletedProcess(returncode=0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr("core.home_edge.executor._run_bounded_process", fake_run)
    receipt = exec_engine.execute(signed_request(nonce="root-switch", idempotency_key="root-switch"))

    assert receipt.status == "ok"
    assert commands[0][:3] == ["sudo", "--non-interactive", "-u"]


def test_mutation_lanes_require_approval_and_enforce_identity() -> None:
    with pytest.raises(HomeEdgeExecError, match="operator approval"):
        HomeEdgeExecRequest.from_mapping(request(execution_lane="routine_mutation"))
    with pytest.raises(HomeEdgeExecError, match="desktop-user"):
        HomeEdgeExecRequest.from_mapping(request(execution_lane="routine_mutation", operator_approval_ref="approval", run_as="root"))
    with pytest.raises(HomeEdgeExecError, match="must run as root"):
        HomeEdgeExecRequest.from_mapping(
            request(execution_lane="privileged_mutation", operator_approval_ref="approval", run_as="desktop-user")
        )
    with pytest.raises(HomeEdgeExecError, match="root-read-only"):
        HomeEdgeExecRequest.from_mapping(request(run_as="root"))
    parsed = HomeEdgeExecRequest.from_mapping(
        request(execution_lane="privileged_mutation", operator_approval_ref="approval", run_as="root")
    )
    assert parsed.run_as is ExecutionUser.ROOT


def test_desktop_pipewire_waydroid_environment_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    account = pwd.getpwuid(os.geteuid())
    monkeypatch.setenv("SKELETON_HOME_EDGE_DISPLAY", ":7")
    monkeypatch.setenv("SKELETON_HOME_EDGE_WAYLAND_DISPLAY", "wayland-7")
    monkeypatch.setenv("SKELETON_HOME_EDGE_XAUTHORITY", f"{account.pw_dir}/.Xauthority")
    monkeypatch.setenv("SECRET_TOKEN", "must-not-leak")

    env = _child_environment(
        {"PIPEWIRE_REMOTE": "pipewire-0", "XDG_SESSION_TYPE": "wayland"},
        run_as=ExecutionUser.DESKTOP_USER,
    )

    assert env["HOME"] == account.pw_dir
    assert env["USER"] == account.pw_name
    assert env["LOGNAME"] == account.pw_name
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{account.pw_uid}"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{account.pw_uid}/bus"
    assert env["DISPLAY"] == ":7"
    assert env["WAYLAND_DISPLAY"] == "wayland-7"
    assert env["PIPEWIRE_REMOTE"] == "pipewire-0"
    assert "SECRET_TOKEN" not in env

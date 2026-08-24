from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.home_edge import esp_lab_activation as activation
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_PATH = ROOT / "scripts/home_edge_esp_lab_activation_signer_payload.py"
WRAPPER_PATH = ROOT / "scripts/home_edge_esp_lab_activation_signer"
INSTALLER_PATH = ROOT / "scripts/install_home_edge_esp_lab_activation_signer.sh"
SECRET = "synthetic-esp-lab-stage1-key"


def _load_payload():
    spec = importlib.util.spec_from_file_location("esp_lab_activation_signer", PAYLOAD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installer_bytes() -> bytes:
    return subprocess.check_output(["git", "show", f"HEAD:{activation.INSTALLER_REPO_PATH}"], cwd=ROOT)


def _esp_module_bytes() -> bytes:
    return subprocess.check_output(["git", "show", f"HEAD:{activation.ESP_MODULE_REPO_PATH}"], cwd=ROOT)


def _signed(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
    request = HomeEdgeExecRequest.from_mapping(dict(unsigned))
    return HomeEdgeExecRequest.from_mapping({**dict(unsigned), "signature": sign_request(request, SECRET)})


def _ok_receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id=activation.REQUEST_ID,
        node_id=activation.TARGET_NODE,
        execution_lane=activation.EXECUTION_LANE,
        exit_code=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="e" * 64,
    )


def _result(**updates: Any) -> str:
    data: dict[str, Any] = {
        "schema": activation.RESULT_SCHEMA,
        "runtime_state": "READY",
        "source_sha": activation.APPROVED_SOURCE_SHA,
        "candidate_count": 0,
        "device_canary": "awaiting_physical_device",
        "dependency_installed_by_operation": False,
        "idempotent_reuse": False,
    }
    data.update(updates)
    return json.dumps(data, separators=(",", ":"))


def test_controller_builds_exact_request_calls_fixed_signer_and_executor_once(monkeypatch: pytest.MonkeyPatch) -> None:
    signer_calls: list[Mapping[str, Any]] = []
    executor_calls: list[Mapping[str, Any]] = []

    def signer(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
        signer_calls.append(dict(unsigned))
        return _signed(unsigned)

    def executor(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        executor_calls.append(dict(request))
        return _ok_receipt(_result(candidate_count=2, device_canary="serial_candidates_present"))

    monkeypatch.setattr(activation, "_sign_activation_request_with_installed_signer", signer)
    monkeypatch.setattr(activation, "execute_home_edge_request", executor)

    public = activation.activate_esp_lab_stage1(repo_root=ROOT)

    assert public["status"] == "DONE"
    assert public["candidate_count"] == 2
    assert len(signer_calls) == 1
    assert len(executor_calls) == 1
    unsigned = signer_calls[0]
    assert unsigned["node_id"] == "home-edge-01"
    assert unsigned["execution_lane"] == "privileged_mutation"
    assert unsigned["run_as"] == "root"
    assert unsigned["mode"] == "script"
    assert unsigned["script_interpreter"] == "bash"
    assert unsigned["timeout_seconds"] == 300
    assert unsigned["max_output_bytes"] == 8192
    assert unsigned["public"] is False
    assert unsigned["operator_approval_ref"] == activation.OPERATOR_APPROVAL_REF
    assert unsigned["idempotency_key"] == activation.IDEMPOTENCY_KEY
    assert unsigned["script"].encode("utf-8") == _installer_bytes()
    activation._validate_stage1_payload_text(unsigned["stdin_text"])
    assert executor_calls[0]["signature"].startswith("sha256=")


def test_controller_has_no_direct_hmac_or_sign_request_path() -> None:
    text = Path(activation.__file__).read_text(encoding="utf-8")

    assert "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET" not in text
    assert "EXEC_HMAC_SECRET_ENV" not in text
    assert "sign_request" not in text
    assert '"/usr/bin/sudo", "-n", str(INSTALLED_SIGNER_EXECUTABLE)' in text
    assert "ssh" not in text


def test_payload_uses_zero_byte_init_and_never_reads_repo_init(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_git(repo_root: Path, *args: str) -> bytes:
        if any("core/__init__.py" in arg for arg in args):
            raise AssertionError("repo core/__init__.py must not be read")
        return activation._git(repo_root, *args)

    monkeypatch.setattr(activation, "_git", fail_git)
    payload = activation.build_stage1_payload(esp_module=_esp_module_bytes())

    assert payload["files"][0] == {
        "path": "core/__init__.py",
        "sha256": activation.INIT_SHA256,
        "base64": "",
    }
    assert payload["files"][1]["path"] == "core/home_edge/esp_lab.py"


def test_installer_and_esp_module_pins_match_reviewed_head() -> None:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip() == activation.APPROVED_SOURCE_SHA
    assert subprocess.check_output(["git", "hash-object", "--no-filters", activation.INSTALLER_REPO_PATH], cwd=ROOT).decode().strip() == activation.INSTALLER_GIT_BLOB_SHA
    assert subprocess.check_output(["git", "hash-object", "--no-filters", activation.ESP_MODULE_REPO_PATH], cwd=ROOT).decode().strip() == activation.ESP_MODULE_GIT_BLOB_SHA
    assert activation._reviewed_git_blob(ROOT, activation.INSTALLER_REPO_PATH, expected_source_sha=activation.APPROVED_SOURCE_SHA, expected_blob_sha=activation.INSTALLER_GIT_BLOB_SHA) == _installer_bytes()
    assert activation._reviewed_git_blob(ROOT, activation.ESP_MODULE_REPO_PATH, expected_source_sha=activation.APPROVED_SOURCE_SHA, expected_blob_sha=activation.ESP_MODULE_GIT_BLOB_SHA) == _esp_module_bytes()


@pytest.mark.parametrize(
    ("command", "output"),
    [
        (("rev-parse", "HEAD"), b"b" * 40 + b"\n"),
        (("status", "--porcelain", "--", activation.INSTALLER_REPO_PATH), b" M scripts/install_home_edge_esp_lab.sh\n"),
        (("ls-tree", activation.APPROVED_SOURCE_SHA, "--", activation.INSTALLER_REPO_PATH), b"100755 blob bad\tpath\n"),
    ],
)
def test_wrong_sha_dirty_or_blob_mismatch_blocks_before_signer(
    monkeypatch: pytest.MonkeyPatch,
    command: tuple[str, ...],
    output: bytes,
) -> None:
    calls: list[str] = []
    real_git = activation._git

    def fake_git(repo_root: Path, *args: str) -> bytes:
        calls.append(" ".join(args))
        if args == command:
            return output
        return real_git(repo_root, *args)

    monkeypatch.setattr(activation, "_git", fake_git)
    monkeypatch.setattr(activation, "_sign_activation_request_with_installed_signer", lambda _: pytest.fail("signer must not run"))

    public = activation.activate_esp_lab_stage1(repo_root=ROOT)

    assert public["status"] == "BLOCKED"
    assert calls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operator_approval_ref", "WRONG"),
        ("node_id", "other"),
        ("execution_lane", "routine_mutation"),
        ("run_as", "desktop-user"),
        ("mode", "argv"),
        ("script_interpreter", "python3"),
        ("timeout_seconds", 299),
        ("max_output_bytes", 8193),
        ("public", True),
        ("idempotency_key", "near-match"),
    ],
)
def test_signer_rejects_authority_mutations_before_secret_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    payload = _load_payload()
    installer = tmp_path / "install_home_edge_esp_lab.sh"
    installer.write_bytes(_installer_bytes())
    installer.chmod(0o644)
    monkeypatch.setattr(payload, "INSTALLED_INSTALLER_SOURCE", installer)
    monkeypatch.setattr(payload, "_safe_regular", lambda st, *, max_bytes, require_root=False, allow_empty=False: payload.stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes)
    monkeypatch.setattr(payload, "read_secret", lambda: pytest.fail("credential must not be read"))
    request = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)
    request[field] = value

    with pytest.raises(SystemExit) as exc:
        payload.validate_authority(request)

    assert exc.value.code == 2


def test_signer_rejects_malformed_extra_script_and_payload_before_secret_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _load_payload()
    installer = tmp_path / "install_home_edge_esp_lab.sh"
    installer.write_bytes(_installer_bytes())
    installer.chmod(0o644)
    monkeypatch.setattr(payload, "INSTALLED_INSTALLER_SOURCE", installer)
    monkeypatch.setattr(payload, "_safe_regular", lambda st, *, max_bytes, require_root=False, allow_empty=False: payload.stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes)
    monkeypatch.setattr(payload, "read_secret", lambda: pytest.fail("credential must not be read"))
    request = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)
    bad_cases = []
    extra = dict(request)
    extra["extra"] = True
    bad_cases.append(extra)
    bad_script = dict(request)
    bad_script["script"] += "\n"
    bad_cases.append(bad_script)
    bad_source = dict(request)
    stdin_payload = json.loads(bad_source["stdin_text"])
    stdin_payload["source_sha"] = "b" * 40
    bad_source["stdin_text"] = json.dumps(stdin_payload, separators=(",", ":"))
    bad_cases.append(bad_source)
    bad_init = dict(request)
    stdin_payload = json.loads(bad_init["stdin_text"])
    stdin_payload["files"][0]["base64"] = "eA=="
    bad_init["stdin_text"] = json.dumps(stdin_payload, separators=(",", ":"))
    bad_cases.append(bad_init)
    bad_module = dict(request)
    stdin_payload = json.loads(bad_module["stdin_text"])
    stdin_payload["files"][1]["sha256"] = "0" * 64
    bad_module["stdin_text"] = json.dumps(stdin_payload, separators=(",", ":"))
    bad_cases.append(bad_module)

    for bad in bad_cases:
        with pytest.raises(SystemExit) as exc:
            payload.validate_authority(bad)
        assert exc.value.code == 2


def test_signer_returns_envelope_only_and_never_executes_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _load_payload()
    installer = tmp_path / "install_home_edge_esp_lab.sh"
    installer.write_bytes(_installer_bytes())
    installer.chmod(0o644)
    monkeypatch.setattr(payload, "INSTALLED_INSTALLER_SOURCE", installer)
    monkeypatch.setattr(payload, "_safe_regular", lambda st, *, max_bytes, require_root=False, allow_empty=False: payload.stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes)
    unsigned = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)

    payload.validate_authority(unsigned)
    signature = payload.sign(unsigned, SECRET)

    assert signature == sign_request(HomeEdgeExecRequest.from_mapping(unsigned), SECRET)
    assert "execute_home_edge_request" not in PAYLOAD_PATH.read_text(encoding="utf-8")
    assert "subprocess" not in PAYLOAD_PATH.read_text(encoding="utf-8")


def test_controller_rejects_altered_signed_authority_before_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    def signer(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
        altered = dict(unsigned)
        altered["timeout_seconds"] = 299
        return _signed(altered)

    monkeypatch.setattr(activation, "_sign_activation_request_with_installed_signer", signer)
    monkeypatch.setattr(activation, "execute_home_edge_request", lambda _: pytest.fail("executor must not run"))

    public = activation.activate_esp_lab_stage1(repo_root=ROOT)

    assert public["status"] == "BLOCKED"
    assert public["reason"] == "activation_signer_signed_authority_mismatch"


def test_executor_success_exact_result_done_and_fail_closed_cases() -> None:
    assert activation.public_result_from_executor_receipt(_ok_receipt(_result()).to_mapping())["status"] == "DONE"
    assert activation.public_result_from_executor_receipt({**_ok_receipt(_result()).to_mapping(), "status": "blocked"})["status"] == "BLOCKED"
    assert activation.public_result_from_executor_receipt({**_ok_receipt(_result()).to_mapping(), "exit_code": 2})["status"] == "BLOCKED"
    assert activation.public_result_from_executor_receipt(_ok_receipt("{not-json").to_mapping())["status"] == "BLOCKED"
    assert activation.public_result_from_executor_receipt(_ok_receipt(_result(schema="wrong")).to_mapping())["status"] == "BLOCKED"


def test_public_report_excludes_private_evidence() -> None:
    public = activation.public_result_from_executor_receipt(_ok_receipt(_result()).to_mapping())
    rendered = json.dumps(public, sort_keys=True)

    for token in ("stdout", "/dev/", "ttyUSB", "VID", "PID", "MAC", "signature", "credential", "secret", "product", "topology"):
        assert token not in rendered


def test_installer_static_fixed_paths_sudoers_visudo_rollback_and_no_generic_sudo() -> None:
    text = INSTALLER_PATH.read_text(encoding="utf-8")

    assert 'RUNNER_USER="agent"' in text
    assert 'PROTECTED_INSTALLER_PATH="/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh"' in text
    assert 'INSTALL_ROOT="/usr/local/lib/skeleton/home-edge/esp-lab-stage1"' in text
    assert 'EXEC_ROOT="/usr/local/libexec/skeleton/home-edge/esp-lab-stage1"' in text
    assert 'SUDOERS_PATH="/etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer"' in text
    assert 'NOPASSWD: $EXEC_ROOT/signer ""' in text
    assert "visudo -cf" in text
    assert "BACKUPS_READY=0" in text and "ACTIVATION_STARTED=0" in text
    assert 'if [[ $COMMITTED -eq 0 && $ACTIVATION_STARTED -eq 1 ]]; then' in text
    assert "ALL=(ALL)" not in text
    assert "NOPASSWD: ALL" not in text
    assert "*" not in text.split("NOPASSWD:", 1)[1].split("\n", 1)[0]


def test_installer_allowed_argv_only() -> None:
    text = INSTALLER_PATH.read_text(encoding="utf-8")

    assert "--repo-root" in text
    assert "Unknown argument" in text
    assert "RUNNER_USER=\"${" not in text
    assert "DEST" not in text
    assert "COMMAND" not in text
    assert "SERVICE" not in text.replace("RUNNER_SERVICE", "")


def test_installed_signer_works_with_repo_unavailable_or_mutated_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _load_payload()
    installer = tmp_path / "installed" / "install_home_edge_esp_lab.sh"
    installer.parent.mkdir()
    installer.write_bytes(_installer_bytes())
    installer.chmod(0o644)
    monkeypatch.setattr(payload, "INSTALLED_INSTALLER_SOURCE", installer)
    monkeypatch.setattr(payload, "_safe_regular", lambda st, *, max_bytes, require_root=False, allow_empty=False: payload.stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes)
    unsigned = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)

    assert payload.expected_installer_script().encode("utf-8") == _installer_bytes()
    payload.validate_authority(unsigned)


def test_static_payload_and_wrapper_are_repo_import_independent() -> None:
    text = PAYLOAD_PATH.read_text(encoding="utf-8")
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")

    assert "from core." not in text
    assert "import core." not in text
    assert "PYTHONPATH" not in text + wrapper
    assert "/home/agent/" not in text + wrapper
    assert str(activation.INSTALLED_SIGNER_PAYLOAD) in wrapper


def test_payload_wrapper_installer_syntax() -> None:
    subprocess.run(["/usr/bin/python3", "-m", "py_compile", str(PAYLOAD_PATH)], check=True)
    subprocess.run(["/bin/sh", "-n", str(WRAPPER_PATH)], check=True)
    subprocess.run(["/bin/bash", "-n", str(INSTALLER_PATH)], check=True)

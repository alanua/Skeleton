from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.home_edge import esp_lab_activation as activation
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_PATH = ROOT / "scripts/home_edge_esp_lab_activation_signer_payload.py"
WRAPPER_PATH = ROOT / "scripts/home_edge_esp_lab_activation_signer"
INSTALLER_PATH = ROOT / "scripts/install_home_edge_esp_lab_activation_signer.sh"
STAGE1_INSTALLER_PATH = ROOT / "scripts/install_home_edge_esp_lab.sh"
SECRET = "synthetic-esp-lab-stage1-key"
SIGNER_TRUSTED_ANCESTOR_SHA = "725dfc3aedbce194c7afcc229eb44b1eec4f463a"
SIGNER_INSTALLER_BLOB_SHA = "ef285000113c1254170b8924b4c3ab8d82250423"
SIGNER_PAYLOAD_BLOB_SHA = "9e349149ea17c38284c8bda1051b3d0de9688d4c"
SIGNER_WRAPPER_BLOB_SHA = "d248088477a7c59219a9c19c47bcfc464c6dcd27"
SIGNER_STAGE1_INSTALLER_BLOB_SHA = "4db8042020915dbcdd261accc5c87a75682fa115"
OLD_STAGE1_INSTALLER_BLOB_SHA = "e2c2378660df0cbaaf02e4556a1d1887a258b863"


def _load_payload():
    spec = importlib.util.spec_from_file_location("esp_lab_activation_signer", PAYLOAD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installer_bytes() -> bytes:
    return STAGE1_INSTALLER_PATH.read_bytes()


def _esp_module_bytes() -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{activation.APPROVED_SOURCE_SHA}:{activation.ESP_MODULE_REPO_PATH}"],
        cwd=ROOT,
    )


def _old_stage1_installer_bytes() -> bytes:
    return subprocess.check_output(["git", "cat-file", "-p", OLD_STAGE1_INSTALLER_BLOB_SHA], cwd=ROOT)


def _git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", "--no-filters", str(path)], cwd=ROOT).decode().strip()


def _git_tree_blob(path: str) -> str:
    return subprocess.check_output(["git", "hash-object", "--no-filters", path], cwd=ROOT).decode().strip()


def _stage1_payload_text() -> str:
    return json.dumps(activation.build_stage1_payload(esp_module=_esp_module_bytes()), separators=(",", ":"))


def _prepare_stage1_installer_test_root(tmp_path: Path) -> Path:
    root = tmp_path / "stage1-root"
    (root / "etc").mkdir(parents=True)
    (root / "usr/bin").mkdir(parents=True)
    (root / "sys/class/tty").mkdir(parents=True)
    (root / "usr/local/bin").mkdir(parents=True)
    (root / "usr/bin/apt-get").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (root / "usr/bin/esptool").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (root / "usr/bin/apt-get").chmod(0o755)
    (root / "usr/bin/esptool").chmod(0o755)
    root.chmod(0o700)
    return root


def _run_stage1_installer_test_mode(root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "SKELETON_ESP_LAB_INSTALLER_TEST_MODE": "1",
            "SKELETON_ESP_LAB_TEST_ROOT": str(root),
        }
    )
    return subprocess.run(
        ["/bin/bash", str(STAGE1_INSTALLER_PATH)],
        input=_stage1_payload_text(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=30,
        check=False,
    )


def _write_debian_13_os_release(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('ID=debian\nVERSION_ID="13"\n', encoding="utf-8")


def test_stage1_installer_accepts_regular_debian_13_os_release(tmp_path: Path) -> None:
    root = _prepare_stage1_installer_test_root(tmp_path)
    _write_debian_13_os_release(root / "etc/os-release")

    result = _run_stage1_installer_test_mode(root)

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["runtime_state"] == "READY"


def test_stage1_installer_accepts_only_canonical_debian_13_os_release_symlink(tmp_path: Path) -> None:
    root = _prepare_stage1_installer_test_root(tmp_path)
    _write_debian_13_os_release(root / "usr/lib/os-release")
    (root / "etc/os-release").symlink_to("../usr/lib/os-release")

    result = _run_stage1_installer_test_mode(root)

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["runtime_state"] == "READY"


@pytest.mark.parametrize("case", ["missing", "dangling", "other-target", "canonical-target-symlink"])
def test_stage1_installer_rejects_unavailable_or_unsafe_os_release_paths(tmp_path: Path, case: str) -> None:
    root = _prepare_stage1_installer_test_root(tmp_path)
    if case == "dangling":
        (root / "etc/os-release").symlink_to("../usr/lib/os-release")
    elif case == "other-target":
        _write_debian_13_os_release(root / "var/lib/os-release")
        (root / "etc/os-release").symlink_to("../var/lib/os-release")
    elif case == "canonical-target-symlink":
        _write_debian_13_os_release(root / "usr/share/os-release")
        (root / "usr/lib").mkdir(parents=True)
        (root / "usr/lib/os-release").symlink_to("../share/os-release")
        (root / "etc/os-release").symlink_to("../usr/lib/os-release")

    result = _run_stage1_installer_test_mode(root)

    assert result.returncode == 2
    assert result.stderr == "BLOCKED: os release is unavailable\n"


@pytest.mark.parametrize("body", ['ID=debian\nVERSION_ID="12"\n', 'ID=ubuntu\nVERSION_ID="24.04"\n'])
def test_stage1_installer_rejects_unsupported_host_os_after_safe_os_release_resolution(tmp_path: Path, body: str) -> None:
    root = _prepare_stage1_installer_test_root(tmp_path)
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/lib/os-release").write_text(body, encoding="utf-8")
    (root / "etc/os-release").symlink_to("../usr/lib/os-release")

    result = _run_stage1_installer_test_mode(root)

    assert result.returncode == 2
    assert result.stderr == "BLOCKED: host os is unsupported\n"


def _make_signer_installer_preflight_fixture(
    tmp_path: Path,
    *,
    missing_signer_files: bool = False,
    payload_bytes: bytes | None = None,
    wrapper_bytes: bytes | None = None,
    installer_bytes: bytes | None = None,
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    if not missing_signer_files:
        (scripts / "home_edge_esp_lab_activation_signer_payload.py").write_bytes(
            payload_bytes if payload_bytes is not None else PAYLOAD_PATH.read_bytes()
        )
        wrapper = scripts / "home_edge_esp_lab_activation_signer"
        wrapper.write_bytes(wrapper_bytes if wrapper_bytes is not None else WRAPPER_PATH.read_bytes())
        wrapper.chmod(0o755)
    stage1 = scripts / "install_home_edge_esp_lab.sh"
    stage1.write_bytes(installer_bytes if installer_bytes is not None else STAGE1_INSTALLER_PATH.read_bytes())
    stage1.chmod(0o755)
    object_store = repo / ".fake-git-objects"
    object_store.mkdir()
    (object_store / SIGNER_INSTALLER_BLOB_SHA).write_bytes(INSTALLER_PATH.read_bytes())
    (object_store / SIGNER_PAYLOAD_BLOB_SHA).write_bytes(PAYLOAD_PATH.read_bytes())
    (object_store / SIGNER_WRAPPER_BLOB_SHA).write_bytes(WRAPPER_PATH.read_bytes())
    (object_store / SIGNER_STAGE1_INSTALLER_BLOB_SHA).write_bytes(STAGE1_INSTALLER_PATH.read_bytes())

    protected = tmp_path / "protected-installer.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = tmp_path / "fake-git"
    fake_systemctl = tmp_path / "fake-systemctl"
    (fake_bin / "getent").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "visudo").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    fake_systemctl.write_text("#!/usr/bin/env sh\nprintf 'agent\\n'\n", encoding="utf-8")
    fake_git.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env sh
            if [ "$1" = "-C" ]; then
              shift 2
            fi
            if [ "$1" = "rev-parse" ] && [ "$2" = "--verify" ] && [ "$3" = "HEAD^{{commit}}" ]; then
              printf '%s\\n' "${{FAKE_HEAD:-06eb2d97d38b9fab0985c47e163070034a0a4611}}"
              exit 0
            fi
            if [ "$1" = "status" ] && [ "$2" = "--porcelain" ]; then
              printf '%s' "${{FAKE_STATUS:-}}"
              exit 0
            fi
            if [ "$1" = "merge-base" ] && [ "$2" = "--is-ancestor" ]; then
              [ "${{FAKE_ANCESTOR_OK:-1}}" = "1" ]
              exit $?
            fi
            if [ "$1" = "ls-tree" ] && [ "$2" = "HEAD" ] && [ "$3" = "--" ]; then
                case "$4" in
                  scripts/install_home_edge_esp_lab_activation_signer.sh)
                    [ "${{FAKE_OLD_CHECKOUT:-0}}" = "1" ] && exit 0
                    printf '100755 blob %s\\t%s\\n' "${{FAKE_SIGNER_INSTALLER_TREE_BLOB:-{SIGNER_INSTALLER_BLOB_SHA}}}" "$4"
                    exit 0
                    ;;
                  scripts/home_edge_esp_lab_activation_signer_payload.py)
                    [ "${{FAKE_OLD_CHECKOUT:-0}}" = "1" ] && exit 0
                    printf '100644 blob %s\\t%s\\n' "${{FAKE_PAYLOAD_TREE_BLOB:-{SIGNER_PAYLOAD_BLOB_SHA}}}" "$4"
                    exit 0
                    ;;
                  scripts/home_edge_esp_lab_activation_signer)
                    [ "${{FAKE_OLD_CHECKOUT:-0}}" = "1" ] && exit 0
                    printf '100755 blob %s\\t%s\\n' "${{FAKE_WRAPPER_TREE_BLOB:-{SIGNER_WRAPPER_BLOB_SHA}}}" "$4"
                    exit 0
                    ;;
                  scripts/install_home_edge_esp_lab.sh)
                    printf '100755 blob %s\\t%s\\n' "${{FAKE_INSTALLER_TREE_BLOB:-{SIGNER_STAGE1_INSTALLER_BLOB_SHA}}}" "$4"
                    exit 0
                    ;;
                esac
            fi
            if [ "$1" = "cat-file" ] && [ "$2" = "-t" ]; then
              [ -f "{object_store}/$3" ] && printf 'blob\\n' && exit 0
              exit 1
            fi
            if [ "$1" = "cat-file" ] && [ "$2" = "-s" ]; then
              [ -f "{object_store}/$3" ] || exit 1
              wc -c < "{object_store}/$3"
              exit 0
            fi
            if [ "$1" = "cat-file" ] && [ "$2" = "-p" ]; then
              [ -f "{object_store}/$3" ] || exit 1
              cat "{object_store}/$3"
              exit 0
            fi
            if [ "$1" = "hash-object" ] && [ "$2" = "--no-filters" ] && [ "$3" = "--stdin" ]; then
              exec /usr/bin/git hash-object --no-filters --stdin
            fi
            printf 'unexpected git argv: %s\\n' "$*" >&2
            exit 99
            """
        ),
        encoding="utf-8",
    )
    for executable in (fake_bin / "getent", fake_bin / "visudo", fake_git, fake_systemctl):
        executable.chmod(0o755)

    text = INSTALLER_PATH.read_text(encoding="utf-8")
    text = text.replace(
        'PROTECTED_INSTALLER_PATH="/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh"',
        f'PROTECTED_INSTALLER_PATH="{protected}"',
    )
    text = text.replace(
        'INSTALL_ROOT="/usr/local/lib/skeleton/home-edge/esp-lab-stage1"',
        f'INSTALL_ROOT="{tmp_path / "install-root"}"',
    )
    text = text.replace(
        'EXEC_ROOT="/usr/local/libexec/skeleton/home-edge/esp-lab-stage1"',
        f'EXEC_ROOT="{tmp_path / "exec-root"}"',
    )
    text = text.replace(
        'SUDOERS_PATH="/etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer"',
        f'SUDOERS_PATH="{tmp_path / "sudoers"}"',
    )
    text = text.replace('/usr/bin/systemctl', str(fake_systemctl))
    text = text.replace('/usr/bin/git', str(fake_git))
    text = text.replace('"$RUNUSER_BIN" -u "$RUNNER_USER" -- "$GIT_BIN"', '"$GIT_BIN"')
    text = text.replace('env -i HOME=/nonexistent', 'env HOME=/nonexistent')
    text = text.replace('if [[ ${EUID:-$(id -u)} -ne 0 ]]; then', 'if [[ 0 -ne 0 ]]; then')
    text = text.replace(
        'if [[ "$protected_uid" != "0" || "$protected_gid" != "0" || $((8#$protected_mode & 8#022)) -ne 0 ]]; then',
        'if [[ $((8#$protected_mode & 8#022)) -ne 0 ]]; then',
    )
    text = text.replace(
        'validate_source_blob "$INSTALLER_REL" $((256 * 1024)) "$INSTALLER_BLOB_SHA" "100755"\n',
        'validate_source_blob "$INSTALLER_REL" $((256 * 1024)) "$INSTALLER_BLOB_SHA" "100755"\nprintf \'PREFLIGHT_OK\\n\'\nexit 0\n',
    )
    protected.write_text(text, encoding="utf-8")
    protected.chmod(0o755)
    return protected, repo


def _signed(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
    request = HomeEdgeExecRequest.from_mapping(dict(unsigned))
    return HomeEdgeExecRequest.from_mapping({**dict(unsigned), "signature": sign_request(request, SECRET)})


def _simulate_clean_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    real_git = activation._git

    def fake_git(repo_root: Path, *args: str) -> bytes:
        if args == ("status", "--porcelain"):
            return b""
        if args == ("ls-tree", "HEAD", "--", activation.INSTALLER_REPO_PATH):
            return f"100755 blob {activation.INSTALLER_GIT_BLOB_SHA}\t{activation.INSTALLER_REPO_PATH}\n".encode()
        if args == ("cat-file", "-p", activation.INSTALLER_GIT_BLOB_SHA):
            return _installer_bytes()
        return real_git(repo_root, *args)

    monkeypatch.setattr(activation, "_git", fake_git)


def _ok_receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id="synthetic-esp-lab-request",
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


def _failed_receipt(stdout: str = "", stderr: str = "") -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="failed",
        request_id="synthetic-esp-lab-request",
        node_id=activation.TARGET_NODE,
        execution_lane=activation.EXECUTION_LANE,
        exit_code=2,
        stdout=stdout,
        stderr=stderr,
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
    _simulate_clean_checkout(monkeypatch)
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
    assert _attempt_token(unsigned["request_id"]) is not None
    attempt_token = _attempt_token(unsigned["request_id"])
    assert unsigned["nonce"] == activation._nonce(attempt_token)
    assert unsigned["idempotency_key"] == activation._idempotency_key(attempt_token)
    assert unsigned["script"].encode("utf-8") == _installer_bytes()
    activation._validate_stage1_payload_text(unsigned["stdin_text"])
    assert executor_calls[0]["signature"].startswith("sha256=")


def test_controller_rejects_previous_stage1_installer_blob_in_authority() -> None:
    unsigned = activation.build_activation_request(
        installer_script=_installer_bytes(),
        esp_module=_esp_module_bytes(),
    ).to_mapping(include_signature=False)
    unsigned["script"] = _old_stage1_installer_bytes().decode("utf-8")

    with pytest.raises(ValueError, match="activation_signer_authority_mismatch"):
        activation._validate_activation_authority(unsigned, include_signature=False)


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


def _attempt_token(request_id: Any) -> str:
    assert isinstance(request_id, str)
    assert request_id.startswith(activation.REQUEST_ID_PREFIX)
    token = request_id.removeprefix(activation.REQUEST_ID_PREFIX)
    assert activation.ATTEMPT_TOKEN_RE.fullmatch(token) is not None
    return token


def test_pr_validation_descendant_head_reads_current_installer_and_pinned_runtime_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    _simulate_clean_checkout(monkeypatch)
    head = subprocess.check_output(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=ROOT).decode().strip()
    assert head != activation.APPROVED_SOURCE_SHA
    subprocess.run(["git", "merge-base", "--is-ancestor", activation.APPROVED_SOURCE_SHA, "HEAD"], cwd=ROOT, check=True)
    assert _git_blob(STAGE1_INSTALLER_PATH) == activation.INSTALLER_GIT_BLOB_SHA
    assert subprocess.check_output(["git", "ls-tree", activation.APPROVED_SOURCE_SHA, "--", activation.ESP_MODULE_REPO_PATH], cwd=ROOT).decode().split()[2] == activation.ESP_MODULE_GIT_BLOB_SHA
    assert activation._reviewed_current_tree_git_blob(ROOT, activation.INSTALLER_REPO_PATH, trusted_ancestor_sha=activation.INSTALLER_TRUSTED_ANCESTOR_SHA, expected_blob_sha=activation.INSTALLER_GIT_BLOB_SHA) == _installer_bytes()
    assert activation._reviewed_git_blob(ROOT, activation.ESP_MODULE_REPO_PATH, expected_source_sha=activation.APPROVED_SOURCE_SHA, expected_blob_sha=activation.ESP_MODULE_GIT_BLOB_SHA) == _esp_module_bytes()


@pytest.mark.parametrize(
    ("command", "output"),
    [
        (("rev-parse", "--verify", "HEAD^{commit}"), b"not-a-commit\n"),
        (("status", "--porcelain"), b" M scripts/install_home_edge_esp_lab.sh\n"),
        (("ls-tree", "HEAD", "--", activation.INSTALLER_REPO_PATH), b"100755 blob bad\tpath\n"),
    ],
)
def test_bad_head_dirty_or_blob_mismatch_blocks_before_signer(
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


def test_non_descendant_blocks_before_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    _simulate_clean_checkout(monkeypatch)

    def fake_check_call(*args: Any, **kwargs: Any) -> int:
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(activation.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(activation, "_sign_activation_request_with_installed_signer", lambda _: pytest.fail("signer must not run"))

    public = activation.activate_esp_lab_stage1(repo_root=ROOT)

    assert public["status"] == "BLOCKED"
    assert public["reason"] == "approved_source_not_ancestor"


def test_mutated_worktree_source_blocks_but_approved_object_bytes_remain(monkeypatch: pytest.MonkeyPatch) -> None:
    real_git = activation._git

    def fake_git(repo_root: Path, *args: str) -> bytes:
        if args == ("status", "--porcelain"):
            return b" M core/home_edge/esp_lab.py\n"
        return real_git(repo_root, *args)

    monkeypatch.setattr(activation, "_git", fake_git)
    monkeypatch.setattr(activation, "_sign_activation_request_with_installed_signer", lambda _: pytest.fail("signer must not run"))

    assert activation._git_blob_sha1(_esp_module_bytes()) == activation.ESP_MODULE_GIT_BLOB_SHA
    public = activation.activate_esp_lab_stage1(repo_root=ROOT)

    assert public["status"] == "BLOCKED"
    assert public["reason"] == "reviewed_checkout_dirty"


def test_future_descendant_head_reads_pinned_objects_not_head_or_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_head_installer = b"#!/usr/bin/env bash\necho changed\n"
    fake_head_module = b"changed = True\n"
    seen: list[tuple[str, ...]] = []
    real_git = activation._git

    def fake_git(repo_root: Path, *args: str) -> bytes:
        seen.append(args)
        if args == ("rev-parse", "--verify", "HEAD^{commit}"):
            return b"f" * 40 + b"\n"
        if args == ("status", "--porcelain"):
            return b""
        if args == ("ls-tree", "HEAD", "--", activation.INSTALLER_REPO_PATH):
            return f"100755 blob {activation.INSTALLER_GIT_BLOB_SHA}\t{activation.INSTALLER_REPO_PATH}\n".encode()
        if args == ("cat-file", "-p", activation.INSTALLER_GIT_BLOB_SHA):
            return _installer_bytes()
        if args == ("show", f"HEAD:{activation.INSTALLER_REPO_PATH}"):
            return fake_head_installer
        if args == ("show", f"HEAD:{activation.ESP_MODULE_REPO_PATH}"):
            return fake_head_module
        return real_git(repo_root, *args)

    monkeypatch.setattr(activation, "_git", fake_git)

    installer = activation._reviewed_current_tree_git_blob(ROOT, activation.INSTALLER_REPO_PATH, trusted_ancestor_sha=activation.INSTALLER_TRUSTED_ANCESTOR_SHA, expected_blob_sha=activation.INSTALLER_GIT_BLOB_SHA)
    module = activation._reviewed_git_blob(ROOT, activation.ESP_MODULE_REPO_PATH, expected_source_sha=activation.APPROVED_SOURCE_SHA, expected_blob_sha=activation.ESP_MODULE_GIT_BLOB_SHA)

    assert installer == _installer_bytes()
    assert module == _esp_module_bytes()
    assert ("show", f"HEAD:{activation.INSTALLER_REPO_PATH}") not in seen
    assert ("show", f"HEAD:{activation.ESP_MODULE_REPO_PATH}") not in seen


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


def test_two_fresh_attempts_keep_source_payload_but_change_executor_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _simulate_clean_checkout(monkeypatch)
    first = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)
    second = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)

    first_token = _attempt_token(first["request_id"])
    second_token = _attempt_token(second["request_id"])
    assert first_token != second_token
    assert first["nonce"] == activation._nonce(first_token)
    assert second["nonce"] == activation._nonce(second_token)
    assert first["idempotency_key"] == activation._idempotency_key(first_token)
    assert second["idempotency_key"] == activation._idempotency_key(second_token)
    assert first["timestamp"] != second["timestamp"]
    assert first["script"] == second["script"]
    assert json.loads(first["stdin_text"]) == json.loads(second["stdin_text"])

    signer_calls: list[Mapping[str, Any]] = []
    executor_calls: list[Mapping[str, Any]] = []

    def signer(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
        signer_calls.append(dict(unsigned))
        return _signed(unsigned)

    def executor(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        executor_calls.append(dict(request))
        reuse = len(executor_calls) == 2
        return _ok_receipt(_result(idempotent_reuse=reuse))

    monkeypatch.setattr(activation, "_sign_activation_request_with_installed_signer", signer)
    monkeypatch.setattr(activation, "execute_home_edge_request", executor)

    first_public = activation.activate_esp_lab_stage1(repo_root=ROOT)
    second_public = activation.activate_esp_lab_stage1(repo_root=ROOT)

    assert first_public["status"] == "DONE"
    assert first_public["idempotent_reuse"] is False
    assert second_public["status"] == "DONE"
    assert second_public["idempotent_reuse"] is True
    assert len(signer_calls) == 2
    assert len(executor_calls) == 2
    assert signer_calls[0]["idempotency_key"] != signer_calls[1]["idempotency_key"]
    assert json.loads(signer_calls[0]["stdin_text"]) == json.loads(signer_calls[1]["stdin_text"])


def test_signer_rejects_cross_mixed_attempt_identifiers_before_secret_read(
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
    first = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)
    second = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)

    cases = []
    mixed_nonce = dict(first)
    mixed_nonce["nonce"] = second["nonce"]
    cases.append(mixed_nonce)
    mixed_idempotency = dict(first)
    mixed_idempotency["idempotency_key"] = second["idempotency_key"]
    cases.append(mixed_idempotency)
    caller_selected = dict(first)
    caller_selected["request_id"] = f"{activation.TASK_ID}-{activation.APPROVED_SOURCE_SHA}"
    cases.append(caller_selected)

    for bad in cases:
        with pytest.raises(SystemExit) as exc:
            payload.validate_authority(bad)
        assert exc.value.code == 2


@pytest.mark.parametrize(
    "timestamp",
    [
        "not-a-timestamp",
        datetime.now(UTC).replace(tzinfo=None).isoformat(),
        (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    ],
)
def test_signer_rejects_bad_timestamp_before_secret_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timestamp: str,
) -> None:
    payload = _load_payload()
    installer = tmp_path / "install_home_edge_esp_lab.sh"
    installer.write_bytes(_installer_bytes())
    installer.chmod(0o644)
    monkeypatch.setattr(payload, "INSTALLED_INSTALLER_SOURCE", installer)
    monkeypatch.setattr(payload, "_safe_regular", lambda st, *, max_bytes, require_root=False, allow_empty=False: payload.stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes)
    monkeypatch.setattr(payload, "read_secret", lambda: pytest.fail("credential must not be read"))
    request = activation.build_activation_request(installer_script=_installer_bytes(), esp_module=_esp_module_bytes()).to_mapping(include_signature=False)
    request["timestamp"] = timestamp

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
    _simulate_clean_checkout(monkeypatch)

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


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        ("BLOCKED: payload hash mismatch\n", "stage1_payload_invalid"),
        ("BLOCKED: host os is unsupported\n", "stage1_host_os_unsupported"),
        ("BLOCKED: existing runtime target is unsafe\n", "stage1_runtime_target_unsafe"),
        ("BLOCKED: wrapper canary failed\n", "stage1_wrapper_canary_failed"),
    ],
)
def test_executor_failed_nonzero_classifies_exact_stage1_stderr_signatures(stderr: str, reason: str) -> None:
    public = activation.public_result_from_executor_receipt(_failed_receipt(stderr=stderr).to_mapping())

    assert public["status"] == "BLOCKED"
    assert public["runtime_state"] == "BLOCKED"
    assert public["reason"] == reason


def test_executor_failed_nonzero_rejects_unknown_stderr_without_exposing_text() -> None:
    receipt = _failed_receipt(stderr="BLOCKED: private /dev/ttyUSB0 serial evidence\n").to_mapping()

    public = activation.public_result_from_executor_receipt(receipt)

    assert public["status"] == "BLOCKED"
    assert public["runtime_state"] == "BLOCKED"
    assert public["reason"] == "executor_receipt_not_ok"
    assert "private" not in json.dumps(public, sort_keys=True)
    assert "ttyUSB0" not in json.dumps(public, sort_keys=True)


@pytest.mark.parametrize(
    "stderr",
    [
        "BLOCKED: payload hash mismatch",
        "BLOCKED: payload hash mismatch\nprivate",
        "BLOCKED: payload hash mismatch\r\n",
    ],
)
def test_executor_failed_nonzero_rejects_malformed_stderr_signatures(stderr: str) -> None:
    public = activation.public_result_from_executor_receipt(_failed_receipt(stderr=stderr).to_mapping())

    assert public["status"] == "BLOCKED"
    assert public["runtime_state"] == "BLOCKED"
    assert public["reason"] == "executor_receipt_not_ok"


@pytest.mark.parametrize(
    "stderr",
    [
        None,
        b"BLOCKED: payload hash mismatch\n",
        ["BLOCKED: payload hash mismatch\n"],
    ],
)
def test_executor_failed_nonzero_rejects_non_string_stderr(stderr: Any) -> None:
    receipt = _failed_receipt(stderr="BLOCKED: payload hash mismatch\n").to_mapping()
    receipt["stderr"] = stderr

    public = activation.public_result_from_executor_receipt(receipt)

    assert public["status"] == "BLOCKED"
    assert public["runtime_state"] == "BLOCKED"
    assert public["reason"] == "executor_receipt_not_ok"


def test_executor_failed_nonzero_rejects_oversize_stderr_without_exposing_text() -> None:
    private_marker = "/dev/ttyUSB0"
    receipt = _failed_receipt(stderr="A" * activation.MAX_EXECUTOR_OUTPUT_BYTES + private_marker).to_mapping()

    public = activation.public_result_from_executor_receipt(receipt)

    assert public["status"] == "BLOCKED"
    assert public["runtime_state"] == "BLOCKED"
    assert public["reason"] == "executor_receipt_not_ok"
    assert private_marker not in json.dumps(public, sort_keys=True)


def test_executor_failed_nonzero_never_converts_embedded_ready_to_success() -> None:
    public = activation.public_result_from_executor_receipt(
        _failed_receipt(stdout=_result(), stderr="BLOCKED: payload hash mismatch\n").to_mapping()
    )

    assert public["status"] == "BLOCKED"
    assert public["runtime_state"] == "BLOCKED"
    assert public["reason"] == "stage1_payload_invalid"


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
    assert '"$VISUDO_BIN" -cf' in text
    assert "BACKUPS_READY=0" in text and "ACTIVATION_STARTED=0" in text
    assert 'if [[ $COMMITTED -eq 0 && $ACTIVATION_STARTED -eq 1 ]]; then' in text
    assert "ALL=(ALL)" not in text
    assert "NOPASSWD: ALL" not in text
    assert "*" not in text.split("NOPASSWD:", 1)[1].split("\n", 1)[0]


def test_signer_installer_static_stage1_sha_is_ancestor_boundary_not_exact_head() -> None:
    text = INSTALLER_PATH.read_text(encoding="utf-8")

    assert f'TRUSTED_ANCESTOR_SHA="{SIGNER_TRUSTED_ANCESTOR_SHA}"' in text
    assert 'rev-parse --verify "HEAD^{commit}"' in text
    assert "status --porcelain" in text
    assert 'merge-base --is-ancestor "$TRUSTED_ANCESTOR_SHA" "$CURRENT_HEAD"' in text
    assert 'rev-parse HEAD)" !=' not in text
    assert 'CURRENT_HEAD" != "$TRUSTED_ANCESTOR_SHA"' not in text
    assert 'SOURCE_SHA="' not in text


def test_current_pr_head_signer_payload_wrapper_and_stage1_installer_blobs_match_constants() -> None:
    assert _git_blob(INSTALLER_PATH) == SIGNER_INSTALLER_BLOB_SHA
    assert _git_tree_blob("scripts/home_edge_esp_lab_activation_signer_payload.py") == SIGNER_PAYLOAD_BLOB_SHA
    assert _git_tree_blob("scripts/home_edge_esp_lab_activation_signer") == SIGNER_WRAPPER_BLOB_SHA
    assert _git_tree_blob("scripts/install_home_edge_esp_lab.sh") == SIGNER_STAGE1_INSTALLER_BLOB_SHA
    assert _git_blob(PAYLOAD_PATH) == SIGNER_PAYLOAD_BLOB_SHA
    assert _git_blob(WRAPPER_PATH) == SIGNER_WRAPPER_BLOB_SHA
    assert _git_blob(STAGE1_INSTALLER_PATH) == SIGNER_STAGE1_INSTALLER_BLOB_SHA


def _run_signer_installer_preflight(
    tmp_path: Path,
    *,
    env: Mapping[str, str] | None = None,
    missing_signer_files: bool = False,
    payload_bytes: bytes | None = None,
    wrapper_bytes: bytes | None = None,
    installer_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[str]:
    protected, repo = _make_signer_installer_preflight_fixture(
        tmp_path,
        missing_signer_files=missing_signer_files,
        payload_bytes=payload_bytes,
        wrapper_bytes=wrapper_bytes,
        installer_bytes=installer_bytes,
    )
    run_env = os.environ.copy()
    run_env["PATH"] = f"{tmp_path / 'bin'}:{run_env['PATH']}"
    if env:
        run_env.update(env)
    return subprocess.run(
        [str(protected), "--repo-root", str(repo)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=run_env,
        check=False,
    )


def test_signer_installer_preflight_accepts_clean_descendant_with_exact_blobs(tmp_path: Path) -> None:
    result = _run_signer_installer_preflight(tmp_path)

    assert result.returncode == 0
    assert result.stdout == "PREFLIGHT_OK\n"
    assert result.stderr == ""


def test_signer_installer_preflight_rejects_exact_old_stage1_checkout_without_signer_files(tmp_path: Path) -> None:
    result = _run_signer_installer_preflight(
        tmp_path,
        missing_signer_files=True,
        env={"FAKE_HEAD": SIGNER_TRUSTED_ANCESTOR_SHA, "FAKE_OLD_CHECKOUT": "1"},
    )

    assert result.returncode == 2
    assert "reviewed signer source tree entry does not match approved blob" in result.stderr


@pytest.mark.parametrize(
    ("env", "updates", "message"),
    [
        ({"FAKE_ANCESTOR_OK": "0"}, {}, "trusted Stage1B source is not an ancestor"),
        ({"FAKE_STATUS": " M scripts/home_edge_esp_lab_activation_signer_payload.py\n"}, {}, "reviewed source checkout is dirty"),
        ({"FAKE_WRAPPER_TREE_BLOB": "0" * 40}, {}, "reviewed signer source tree entry does not match approved blob"),
        ({"FAKE_INSTALLER_TREE_BLOB": "1" * 40}, {}, "reviewed signer source tree entry does not match approved blob"),
    ],
)
def test_signer_installer_preflight_rejects_bad_provenance_before_activation(
    tmp_path: Path,
    env: Mapping[str, str],
    updates: Mapping[str, bytes],
    message: str,
) -> None:
    result = _run_signer_installer_preflight(tmp_path, env=env, **updates)

    assert result.returncode == 2
    assert message in result.stderr
    assert "PREFLIGHT_OK" not in result.stdout


def test_signer_installer_preflight_ignores_mutated_or_symlink_worktree_source(tmp_path: Path) -> None:
    protected, repo = _make_signer_installer_preflight_fixture(tmp_path)
    payload = repo / "scripts/home_edge_esp_lab_activation_signer_payload.py"
    payload.unlink()
    payload.symlink_to(repo / "scripts/install_home_edge_esp_lab.sh")
    run_env = os.environ.copy()
    run_env["PATH"] = f"{tmp_path / 'bin'}:{run_env['PATH']}"

    result = subprocess.run(
        [str(protected), "--repo-root", str(repo)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=run_env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "PREFLIGHT_OK\n"
    assert result.stderr == ""


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

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_home_edge_esp_lab.sh"
ESP_MODULE = ROOT / "core/home_edge/esp_lab.py"
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
PAYLOAD_SCHEMA = "skeleton.home_edge.esp_lab_stage1_payload.v1"
MANIFEST_SCHEMA = "skeleton.home_edge.esp_lab_stage1_manifest.v1"
RESULT_SCHEMA = "skeleton.home_edge.esp_lab_stage1_activation_result.v1"


def _source_sha() -> str:
    return hashlib.sha1(ESP_MODULE.read_bytes(), usedforsecurity=False).hexdigest()


def _payload(
    *,
    init: bytes = b"",
    init_sha: str | None = None,
    esp: bytes | None = None,
    source_sha: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> bytes:
    esp_body = ESP_MODULE.read_bytes() if esp is None else esp
    if files is None:
        files = [
            {
                "path": "core/__init__.py",
                "sha256": init_sha or hashlib.sha256(init).hexdigest(),
                "base64": base64.b64encode(init).decode("ascii"),
            },
            {
                "path": "core/home_edge/esp_lab.py",
                "sha256": hashlib.sha256(esp_body).hexdigest(),
                "base64": base64.b64encode(esp_body).decode("ascii"),
            },
        ]
    return json.dumps(
        {"schema": PAYLOAD_SCHEMA, "source_sha": source_sha or _source_sha(), "files": files},
        separators=(",", ":"),
    ).encode("utf-8")


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "install-root"
    (root / "etc").mkdir(parents=True)
    (root / "sys/class/tty").mkdir(parents=True)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/local/bin").mkdir(parents=True)
    (root / "opt/skeleton").mkdir(parents=True)
    (root / "etc/os-release").write_text('ID=debian\nVERSION_ID="13"\n', encoding="utf-8")
    apt = root / "usr/bin/apt-get"
    apt.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$SKELETON_ESP_LAB_TEST_ROOT/apt.log\"\n"
        "if [[ \"$1\" == install ]]; then\n"
        "  printf '#!/usr/bin/env bash\\nexit 0\\n' > \"$SKELETON_ESP_LAB_TEST_ROOT/usr/bin/esptool\"\n"
        "  chmod 0755 \"$SKELETON_ESP_LAB_TEST_ROOT/usr/bin/esptool\"\n"
        "fi\n",
        encoding="utf-8",
    )
    apt.chmod(0o755)
    root.chmod(0o755)
    return root


def _env(root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHONPATH": "",
        "SKELETON_ESP_LAB_INSTALLER_TEST_MODE": "1",
        "SKELETON_ESP_LAB_TEST_ROOT": str(root),
        "PYTEST_CURRENT_TEST": os.environ.get("PYTEST_CURRENT_TEST", "test"),
    }
    if extra:
        env.update(extra)
    return env


def _run_once(root: Path, payload: bytes | None = None, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    input_data = (payload if payload is not None else _payload()).decode("utf-8")
    return subprocess.run(
        ["bash", str(INSTALLER)],
        input=input_data,
        cwd=cwd or Path("/tmp"),
        env=_env(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _runtime(root: Path) -> Path:
    return root / "opt/skeleton/esp-lab" / _source_sha()


def _manifest_bytes() -> bytes:
    esp = ESP_MODULE.read_bytes()
    manifest = {
        "files": {
            "core/__init__.py": {"sha256": EMPTY_SHA, "size": 0},
            "core/home_edge/esp_lab.py": {"sha256": hashlib.sha256(esp).hexdigest(), "size": len(esp)},
        },
        "schema": MANIFEST_SCHEMA,
        "source_sha": _source_sha(),
    }
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def test_payload_helper_uses_real_module_and_zero_byte_init_rejection_precedes_mutation(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    assert _payload().find(base64.b64encode(ESP_MODULE.read_bytes())) != -1
    bad_body = _run_once(root, _payload(init=b"x"))
    assert bad_body.returncode != 0
    bad_hash = _run_once(root, _payload(init_sha="0" * 64))
    assert bad_hash.returncode != 0
    assert not (root / "apt.log").exists()
    assert not (root / "opt/skeleton/esp-lab").exists()
    assert not (root / "usr/local/bin/skeleton-esp-lab").exists()


def test_first_install_exact_tree_manifest_modes_and_real_wrapper_outside_repo(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    completed = _run_once(root)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "schema": RESULT_SCHEMA,
        "runtime_state": "READY",
        "source_sha": _source_sha(),
        "candidate_count": 0,
        "device_canary": "awaiting_physical_device",
        "dependency_installed_by_operation": True,
        "idempotent_reuse": False,
    }
    runtime = _runtime(root)
    assert sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*")) == [
        "core",
        "core/__init__.py",
        "core/home_edge",
        "core/home_edge/esp_lab.py",
        "manifest.json",
    ]
    assert (runtime / "core/__init__.py").read_bytes() == b""
    assert (ROOT / "core/__init__.py").read_bytes() != b""
    assert (runtime / "core/home_edge/esp_lab.py").read_bytes() == ESP_MODULE.read_bytes()
    assert (runtime / "manifest.json").read_bytes() == _manifest_bytes()
    for path in [runtime, runtime / "core", runtime / "core/home_edge"]:
        assert stat.S_IMODE(path.lstat().st_mode) == 0o555
    for path in [runtime / "core/__init__.py", runtime / "core/home_edge/esp_lab.py", runtime / "manifest.json"]:
        assert stat.S_IMODE(path.lstat().st_mode) == 0o444
    wrapper = root / "usr/local/bin/skeleton-esp-lab"
    outside = tmp_path / "outside"
    outside.mkdir()
    wrapped = subprocess.run(
        [str(wrapper), "discover", "--sysfs-root", str(root / "sys/class/tty")],
        cwd=outside,
        env={"PYTHONPATH": ""},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert wrapped.returncode == 0
    assert json.loads(wrapped.stdout) == []


def test_candidate_count_only_and_canary_argv_semantics(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    tty = root / "sys/class/tty/ttyUSB7"
    tty.mkdir()
    (tty / "product").write_text("Secret ESP Path Product", encoding="utf-8")
    completed = _run_once(root)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["candidate_count"] == 1
    assert result["device_canary"] == "serial_candidates_present"
    for private in ("ttyUSB7", "Secret ESP Path Product", "303a", "1001", str(tty)):
        assert private not in completed.stdout
    wrapper = (root / "usr/local/bin/skeleton-esp-lab").read_text(encoding="utf-8")
    assert f'PYTHONPATH={_runtime(root)} exec /usr/bin/python3 -m core.home_edge.esp_lab "$@"' in wrapper


def test_same_sha_rerun_reuses_target_wrapper_and_apt(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    first = _run_once(root)
    assert first.returncode == 0, first.stderr
    runtime = _runtime(root)
    wrapper = root / "usr/local/bin/skeleton-esp-lab"
    before = (runtime.lstat(), wrapper.lstat(), hashlib.sha256(wrapper.read_bytes()).hexdigest(), (root / "apt.log").read_text())
    second = _run_once(root)
    assert second.returncode == 0, second.stderr
    result = json.loads(second.stdout)
    assert result["idempotent_reuse"] is True
    after = (runtime.lstat(), wrapper.lstat(), hashlib.sha256(wrapper.read_bytes()).hexdigest(), (root / "apt.log").read_text())
    assert before[0].st_ino == after[0].st_ino
    assert before[0].st_mtime_ns == after[0].st_mtime_ns
    assert before[1].st_ino == after[1].st_ino
    assert before[1].st_mtime_ns == after[1].st_mtime_ns
    assert before[2:] == after[2:]


def test_malformed_payload_matrix_has_zero_mutation(tmp_path: Path) -> None:
    cases = [
        _payload(files=list(reversed(json.loads(_payload())["files"]))),
        _payload(files=json.loads(_payload())["files"][:1]),
        _payload(files=[*json.loads(_payload())["files"], json.loads(_payload())["files"][1]]),
        _payload(source_sha="A" * 40),
        b'{"schema":"skeleton.home_edge.esp_lab_stage1_payload.v1","source_sha":"'
        + b"1" * 40
        + b'","files":[{"path":"core/__init__.py","sha256":"'
        + EMPTY_SHA.encode()
        + b'","base64":"%%%"}]}',
        _payload(esp=b"x", files=[
            json.loads(_payload())["files"][0],
            {"path": "core/home_edge/esp_lab.py", "sha256": "0" * 64, "base64": base64.b64encode(b"x").decode("ascii")},
        ]),
        b" " * 230001,
    ]
    for index, payload in enumerate(cases):
        root = _make_root(tmp_path / str(index))
        completed = _run_once(root, payload)
        assert completed.returncode != 0
        assert not (root / "apt.log").exists()
        assert not (root / "opt/skeleton/esp-lab").exists()
        assert not (root / "usr/local/bin/skeleton-esp-lab").exists()


def test_wrong_os_has_zero_mutation(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "etc/os-release").write_text('ID=debian\nVERSION_ID="12"\n', encoding="utf-8")
    completed = _run_once(root)
    assert completed.returncode != 0
    assert not (root / "apt.log").exists()
    assert not (root / "opt/skeleton/esp-lab").exists()


def test_corrupt_or_symlink_existing_target_blocked_before_apt_and_wrapper(tmp_path: Path) -> None:
    for name, make_target in [
        ("corrupt", lambda target: (target.mkdir(parents=True), (target / "junk").write_text("x", encoding="utf-8"))),
        ("link", lambda target: target.symlink_to(tmp_path / "missing")),
    ]:
        root = _make_root(tmp_path / name)
        target = _runtime(root)
        target.parent.mkdir(parents=True)
        make_target(target)
        completed = _run_once(root)
        assert completed.returncode != 0
        assert not (root / "apt.log").exists()
        assert not (root / "usr/local/bin/skeleton-esp-lab").exists()


def test_broken_or_nonregular_wrapper_blocked_before_apt_and_target(tmp_path: Path) -> None:
    for name, make_wrapper in [
        ("link", lambda path: path.symlink_to(tmp_path / "missing")),
        ("dir", lambda path: path.mkdir()),
    ]:
        root = _make_root(tmp_path / name)
        wrapper = root / "usr/local/bin/skeleton-esp-lab"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        make_wrapper(wrapper)
        completed = _run_once(root)
        assert completed.returncode != 0
        assert not (root / "apt.log").exists()
        assert not (root / "opt/skeleton/esp-lab").exists()


def test_canary_failure_rolls_back_actual_module_install(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    base = root / "opt/skeleton/esp-lab"
    base.mkdir(parents=True)
    base.chmod(0o555)
    unrelated = base / ("f" * 40)
    base.chmod(0o755)
    unrelated.mkdir()
    base.chmod(0o555)
    prior_wrapper = root / "usr/local/bin/skeleton-esp-lab"
    prior_wrapper.write_text("#!/usr/bin/env bash\nexit 42\n", encoding="utf-8")
    prior_wrapper.chmod(0o711)
    os.utime(prior_wrapper, (1_700_000_000, 1_700_000_000))
    before_wrapper = prior_wrapper.stat()
    before_base = base.stat()
    shutil.rmtree(root / "sys/class/tty")
    (root / "sys/class").mkdir(exist_ok=True)
    (root / "sys/class/tty").write_text("not a directory", encoding="utf-8")
    completed = _run_once(root)
    assert completed.returncode != 0
    assert not _runtime(root).exists()
    assert unrelated.exists()
    assert not (root / "usr/bin/esptool").exists()
    after_wrapper = prior_wrapper.stat()
    assert prior_wrapper.read_text(encoding="utf-8") == "#!/usr/bin/env bash\nexit 42\n"
    assert stat.S_IMODE(after_wrapper.st_mode) == stat.S_IMODE(before_wrapper.st_mode)
    assert after_wrapper.st_mtime_ns == before_wrapper.st_mtime_ns
    after_base = base.stat()
    assert stat.S_IMODE(after_base.st_mode) == stat.S_IMODE(before_base.st_mode)


def test_test_root_guard_and_argv_fail_before_stdin(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    no_guard = subprocess.run(
        ["bash", str(INSTALLER)],
        input=b"",
        env={**os.environ, "SKELETON_ESP_LAB_TEST_ROOT": str(root)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        check=False,
    )
    assert no_guard.returncode != 0
    with_arg = subprocess.run(
        ["bash", str(INSTALLER), "x"],
        input=b"",
        env=_env(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        check=False,
    )
    assert with_arg.returncode != 0
    assert not (root / "apt.log").exists()


def test_installer_source_static_contract() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    forbidden = [
        "repo_root",
        "approved_git_head",
        "protected_installer_path",
        "sudoers",
        "runner user",
        "service",
        "Windows connector",
        "ESPConnect",
        "shared_secret",
        "bind_host",
        "allow_lan",
        "listener",
        "plan mode",
        "--apply",
        "--root",
        "systemctl",
        "visudo",
        "NOPASSWD",
        "git checkout",
        "hash-object",
        "source-tree",
        "curl",
        "wget",
        "git clone",
        "fetch",
        "write-flash",
        "erase-flash",
        "read-flash",
        "__builtins__",
        "__import__",
        "eval",
    ]
    assert all(token not in source for token in forbidden)
    assert "pip install" not in source
    assert "CHECK_OWNERSHIP=1" in source
    assert 'stat -c \'%u:%g\'' in source

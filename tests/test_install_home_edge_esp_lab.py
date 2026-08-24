from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_home_edge_esp_lab.sh"
PAYLOAD_SCHEMA = "skeleton.home_edge.esp_lab_stage1_payload.v1"
RESULT_SCHEMA = "skeleton.home_edge.esp_lab_stage1_activation_result.v1"
MANIFEST_SCHEMA = "skeleton.home_edge.esp_lab_stage1_manifest.v1"
SOURCE_SHA = "a" * 40


MODULE = textwrap.dedent(
    """
    from __future__ import annotations

    import json
    import os
    import sys
    from pathlib import Path

    def main() -> int:
        log = os.environ.get("ESP_LAB_ARGV_LOG")
        if log:
            Path(log).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
        fail = os.environ.get("ESP_LAB_CANARY_FAIL")
        if fail:
            print("not-json")
            return 3
        if sys.argv[1:3] != ["discover", "--sysfs-root"]:
            return 2
        candidates = []
        root = Path(sys.argv[3])
        if root.exists():
            for entry in sorted(root.iterdir()):
                if entry.name.startswith(("ttyUSB", "ttyACM")):
                    candidates.append({"device_ref": "/dev/" + entry.name})
        print(json.dumps(candidates, sort_keys=True))
        return 0

    if __name__ == "__main__":
        raise SystemExit(main())
    """
).lstrip()


def payload(source_sha: str = SOURCE_SHA, module: str = MODULE) -> bytes:
    files: list[dict[str, str]] = []
    for path, data in (
        ("core/__init__.py", b""),
        ("core/home_edge/esp_lab.py", module.encode("utf-8")),
    ):
        files.append(
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "base64": base64.b64encode(data).decode("ascii"),
            }
        )
    return json.dumps({"schema": PAYLOAD_SCHEMA, "source_sha": source_sha, "files": files}, separators=(",", ":")).encode()


def manifest_bytes(source_sha: str = SOURCE_SHA, module: str = MODULE) -> bytes:
    files = []
    for path, data in (
        ("core/__init__.py", b""),
        ("core/home_edge/esp_lab.py", module.encode("utf-8")),
    ):
        files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    return (
        json.dumps(
            {"files": files, "schema": MANIFEST_SCHEMA, "source_sha": source_sha},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


@pytest.fixture()
def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    (root / "etc").mkdir()
    (root / "etc" / "os-release").write_text('ID=debian\nVERSION_ID="13"\n', encoding="utf-8")
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "local" / "bin").mkdir(parents=True)
    (root / "opt" / "skeleton").mkdir(parents=True)
    (root / "sys" / "class" / "tty").mkdir(parents=True)
    python = root / "usr" / "bin" / "python3"
    python.symlink_to(Path("/usr/bin/python3"))
    apt = root / "usr" / "bin" / "apt-get"
    apt.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SKELETON_APT_LOG"\n'
        'if [ "$1" = install ]; then mkdir -p "$SKELETON_ESP_LAB_TEST_ROOT/usr/local/bin"; '
        'printf "#!/usr/bin/env bash\\nexit 0\\n" > "$SKELETON_ESP_LAB_TEST_ROOT/usr/local/bin/esptool"; '
        'chmod 755 "$SKELETON_ESP_LAB_TEST_ROOT/usr/local/bin/esptool"; fi\n',
        encoding="utf-8",
    )
    apt.chmod(0o755)
    return root


def run_installer(root: Path, data: bytes | None = None, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.update(
        {
            "SKELETON_ESP_LAB_INSTALLER_TEST_MODE": "1",
            "SKELETON_ESP_LAB_TEST_ROOT": str(root),
            "PYTEST_CURRENT_TEST": env.get("PYTEST_CURRENT_TEST", "synthetic::test"),
            "SKELETON_APT_LOG": str(root / "apt.log"),
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALLER)],
        input=payload() if data is None else data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=env,
        check=False,
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_real_shell_first_install_exact_tree_manifest_modes_wrapper_and_execution_outside_repo(fake_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "argv.json"
    result = run_installer(fake_root, extra_env={"ESP_LAB_ARGV_LOG": str(log), "PYTHONPATH": str(ROOT)})
    assert result.returncode == 0, result.stderr.decode()
    out = json.loads(result.stdout)
    assert out == {
        "schema": RESULT_SCHEMA,
        "runtime_state": "READY",
        "source_sha": SOURCE_SHA,
        "candidate_count": 0,
        "device_canary": "awaiting_physical_device",
        "dependency_installed_by_operation": True,
        "idempotent_reuse": False,
    }
    target = fake_root / "opt" / "skeleton" / "esp-lab" / SOURCE_SHA
    assert sorted(str(path.relative_to(target)) for path in target.rglob("*")) == [
        "core",
        "core/__init__.py",
        "core/home_edge",
        "core/home_edge/esp_lab.py",
        "manifest.json",
    ]
    assert (target / "manifest.json").read_bytes() == manifest_bytes()
    assert mode(fake_root / "opt" / "skeleton" / "esp-lab") == 0o555
    assert mode(target) == mode(target / "core") == mode(target / "core" / "home_edge") == 0o555
    assert mode(target / "core" / "__init__.py") == mode(target / "core" / "home_edge" / "esp_lab.py") == mode(target / "manifest.json") == 0o444
    wrapper = fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab"
    assert mode(wrapper) == 0o755
    assert wrapper.read_text(encoding="utf-8") == (
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "cd /tmp\n"
        f'PYTHONPATH={target} exec {fake_root}/usr/bin/python3 -m core.home_edge.esp_lab "$@"\n'
    )
    assert json.loads(log.read_text(encoding="utf-8")) == ["discover", "--sysfs-root", str(fake_root / "sys" / "class" / "tty")]
    direct = subprocess.run(
        [str(wrapper), "discover", "--sysfs-root", str(fake_root / "sys" / "class" / "tty")],
        cwd=tmp_path,
        env={"PYTHONPATH": "/hostile"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert direct.returncode == 0
    assert json.loads(direct.stdout) == []


def test_candidate_count_only_canary_exact_argv_and_no_other_serial_actions(fake_root: Path, tmp_path: Path) -> None:
    (fake_root / "sys" / "class" / "tty" / "ttyUSB0").mkdir()
    log = tmp_path / "argv.json"
    result = run_installer(fake_root, extra_env={"ESP_LAB_ARGV_LOG": str(log)})
    assert result.returncode == 0, result.stderr.decode()
    out = json.loads(result.stdout)
    assert out["candidate_count"] == 1
    assert out["device_canary"] == "serial_candidates_present"
    argv = json.loads(log.read_text(encoding="utf-8"))
    assert argv == ["discover", "--sysfs-root", str(fake_root / "sys" / "class" / "tty")]
    assert not any(token in argv for token in ["identify", "read-mac", "flash-id", "inspect", "observe"])
    assert "ttyUSB0" not in result.stdout.decode()


def test_same_sha_rerun_preserves_target_wrapper_and_apt_log(fake_root: Path) -> None:
    first = run_installer(fake_root)
    assert first.returncode == 0, first.stderr.decode()
    target = fake_root / "opt" / "skeleton" / "esp-lab" / SOURCE_SHA
    wrapper = fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab"
    before = (target.stat().st_ino, target.stat().st_mtime_ns, wrapper.stat().st_ino, wrapper.stat().st_mtime_ns, wrapper.read_bytes())
    apt_log = (fake_root / "apt.log").read_bytes()
    second = run_installer(fake_root)
    assert second.returncode == 0, second.stderr.decode()
    assert json.loads(second.stdout)["idempotent_reuse"] is True
    after = (target.stat().st_ino, target.stat().st_mtime_ns, wrapper.stat().st_ino, wrapper.stat().st_mtime_ns, wrapper.read_bytes())
    assert after == before
    assert (fake_root / "apt.log").read_bytes() == apt_log


@pytest.mark.parametrize(
    "mutate",
    [
        lambda obj: [],
        lambda obj: {**obj, "extra": True},
        lambda obj: {**obj, "source_sha": "A" * 40},
        lambda obj: {**obj, "files": list(reversed(obj["files"]))},
        lambda obj: {**obj, "files": [{**obj["files"][0], "base64": "***"}, obj["files"][1]]},
        lambda obj: {**obj, "files": [{**obj["files"][0], "sha256": "0" * 64}, obj["files"][1]]},
        lambda obj: {**obj, "files": [{**obj["files"][0], "base64": base64.b64encode(b"x" * 220001).decode("ascii")}, obj["files"][1]]},
    ],
)
def test_malformed_inputs_cause_zero_mutation(fake_root: Path, mutate: Any) -> None:
    data = json.loads(payload())
    bad = json.dumps(mutate(data), separators=(",", ":")).encode()
    result = run_installer(fake_root, data=bad)
    assert result.returncode != 0
    assert not (fake_root / "opt" / "skeleton" / "esp-lab").exists()
    assert not (fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab").exists()
    assert not (fake_root / "apt.log").exists()


def test_wrong_os_causes_zero_mutation(fake_root: Path) -> None:
    (fake_root / "etc" / "os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n', encoding="utf-8")
    result = run_installer(fake_root)
    assert result.returncode != 0
    assert not (fake_root / "opt" / "skeleton" / "esp-lab").exists()
    assert not (fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab").exists()
    assert not (fake_root / "apt.log").exists()


def test_corrupt_regular_existing_target_untouched(fake_root: Path) -> None:
    target = fake_root / "opt" / "skeleton" / "esp-lab" / SOURCE_SHA
    target.mkdir(parents=True)
    bad = target / "junk"
    bad.write_text("keep", encoding="utf-8")
    before = (bad.read_bytes(), mode(target), mode(bad))
    result = run_installer(fake_root)
    assert result.returncode != 0
    assert (bad.read_bytes(), mode(target), mode(bad)) == before
    assert not (fake_root / "apt.log").exists()


def test_broken_symlink_target_untouched_blocks_before_apt_or_wrapper(fake_root: Path) -> None:
    base = fake_root / "opt" / "skeleton" / "esp-lab"
    base.mkdir()
    link = base / SOURCE_SHA
    link.symlink_to(fake_root / "missing")
    result = run_installer(fake_root)
    assert result.returncode != 0
    assert os.path.islink(link)
    assert os.readlink(link) == str(fake_root / "missing")
    assert not (fake_root / "apt.log").exists()
    assert not (fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab").exists()


def test_non_regular_and_broken_symlink_wrapper_block_untouched(fake_root: Path) -> None:
    wrapper = fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab"
    wrapper.symlink_to(fake_root / "missing-wrapper")
    result = run_installer(fake_root)
    assert result.returncode != 0
    assert os.path.islink(wrapper)
    assert os.readlink(wrapper) == str(fake_root / "missing-wrapper")
    assert not (fake_root / "apt.log").exists()
    wrapper.unlink()
    wrapper.mkdir()
    result = run_installer(fake_root)
    assert result.returncode != 0
    assert wrapper.is_dir()
    assert not (fake_root / "apt.log").exists()


def test_wrong_regular_wrapper_restored_after_later_canary_failure_and_operation_owned_outputs_removed(fake_root: Path) -> None:
    base = fake_root / "opt" / "skeleton" / "esp-lab"
    base.mkdir(mode=0o711)
    unrelated = base / ("b" * 40)
    unrelated.mkdir()
    (unrelated / "keep").write_text("yes", encoding="utf-8")
    wrapper = fake_root / "usr" / "local" / "bin" / "skeleton-esp-lab"
    wrapper.write_text("old-wrapper\n", encoding="utf-8")
    wrapper.chmod(0o700)
    before = (wrapper.read_bytes(), mode(wrapper), mode(base), (unrelated / "keep").read_bytes())
    result = run_installer(fake_root, extra_env={"ESP_LAB_CANARY_FAIL": "1"})
    assert result.returncode != 0
    assert (wrapper.read_bytes(), mode(wrapper), mode(base), (unrelated / "keep").read_bytes()) == before
    assert not (base / SOURCE_SHA).exists()
    assert not (fake_root / "usr" / "local" / "bin" / "esptool").exists()


def test_unguarded_test_root_fails_before_stdin_or_mutation(fake_root: Path) -> None:
    env = os.environ.copy()
    env["SKELETON_ESP_LAB_TEST_ROOT"] = str(fake_root)
    proc = subprocess.run(["bash", str(INSTALLER)], input=b"not-json", stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    assert proc.returncode != 0
    assert b"unguarded_test_environment" in proc.stderr
    assert not (fake_root / "opt" / "skeleton" / "esp-lab").exists()


def test_production_ownership_validator_predicate_directly_tested() -> None:
    ns: dict[str, Any] = {}
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("def is_root_owned")
    end = text.index("\ndef is_safe_existing_base", start)
    exec(text[start:end], ns)
    class Stat:
        st_uid = 1
        st_gid = 0
    assert ns["is_root_owned"](Stat()) is False
    Stat.st_uid = 0
    assert ns["is_root_owned"](Stat()) is True


def test_canonical_manifest_exact_bytes_regression(fake_root: Path) -> None:
    result = run_installer(fake_root)
    assert result.returncode == 0, result.stderr.decode()
    assert (fake_root / "opt" / "skeleton" / "esp-lab" / SOURCE_SHA / "manifest.json").read_bytes() == manifest_bytes()


def test_source_regression_excludes_old_architecture_and_obfuscation() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    forbidden = [
        "repo_root",
        "approved_git_head",
        "protected_installer_path",
        "sudoers_path",
        "runner_user",
        "runner_service",
        "/etc/sudoers.d",
        "visudo",
        "NOPASSWD",
        "systemctl",
        "getent passwd",
        "git checkout",
        "hash-object",
        "scripts/home_edge_esp_lab.py",
        "esp_lab_connector",
        "ESPConnect",
        "shared_secret",
        "bind_host",
        "allow_lan",
        "--apply",
        "--root",
        "curl",
        "wget",
        "git clone",
        "git fetch",
        "write-flash",
        "erase-flash",
        "read-flash",
        "__builtins__",
        "__import__",
        "eval(",
        "exec(",
    ]
    for token in forbidden:
        assert token not in text
    assert not re.search(r"(^|[^A-Za-z0-9_-])pip([^A-Za-z0-9_-]|$)", text)
    assert "import json" in text
    assert '"port"' in text

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "scripts" / "install_home_edge_esp_lab.sh"
SCHEMA = "skeleton.home_edge.esp_lab_stage1_payload.v1"
SHA = "0123456789abcdef0123456789abcdef01234567"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def payload(source_sha: str = SHA, init_body: bytes = b"", module_body: bytes | None = None) -> dict:
    if module_body is None:
        module_body = (
            b"import argparse, json\n"
            b"def main():\n"
            b"    p=argparse.ArgumentParser()\n"
            b"    sub=p.add_subparsers(dest='cmd')\n"
            b"    d=sub.add_parser('discover')\n"
            b"    d.add_argument('--sysfs-root')\n"
            b"    a=p.parse_args()\n"
            b"    if a.cmd == 'discover':\n"
            b"        print(json.dumps([]))\n"
            b"        return\n"
            b"    raise SystemExit(2)\n"
            b"if __name__ == '__main__': main()\n"
        )
    files = []
    for path, body in [
        ("core/__init__.py", init_body),
        ("core/home_edge/esp_lab.py", module_body),
    ]:
        files.append(
            {
                "path": path,
                "sha256": hashlib.sha256(body).hexdigest(),
                "base64": b64(body),
            }
        )
    return {"schema": SCHEMA, "source_sha": source_sha, "files": files}


def make_root(tmp_path: Path, *, host: str = "home-edge-01", os_id: str = "debian", version: str = "13") -> Path:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    (root / "etc").mkdir()
    (root / "etc" / "hostname").write_text(host + "\n")
    (root / "etc" / "os-release").write_text(f'ID="{os_id}"\nVERSION_ID="{version}"\n')
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/local/bin").mkdir(parents=True)
    (root / "sys/class/tty").mkdir(parents=True)
    return root


def fake_apt(root: Path) -> Path:
    log = root / "apt.log"
    apt = root / "usr/bin/apt-get"
    apt.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$(dirname \"$0\")/../../apt.log\"\n"
        "if [[ \"$1\" == install ]]; then touch \"$(dirname \"$0\")/esptool\"; chmod 755 \"$(dirname \"$0\")/esptool\"; fi\n"
        "if [[ \"$1\" == remove ]]; then rm -f \"$(dirname \"$0\")/esptool\"; fi\n"
    )
    apt.chmod(0o755)
    return log


def run_installer(root: Path, body: dict | bytes, *, args: list[str] | None = None, env_extra: dict[str, str] | None = None):
    env = os.environ.copy()
    env.update(
        {
            "SKELETON_ESP_LAB_INSTALLER_TEST_MODE": "1",
            "SKELETON_ESP_LAB_TEST_ROOT": str(root),
            "PYTEST_CURRENT_TEST": os.environ.get("PYTEST_CURRENT_TEST", "test"),
            "PATH": "/usr/bin:/bin",
        }
    )
    if env_extra:
        env.update(env_extra)
    stdin = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
    return subprocess.run(
        ["bash", str(INSTALLER), *(args or [])],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd="/tmp",
        timeout=20,
    )


def runtime(root: Path, sha: str = SHA) -> Path:
    return root / "opt/skeleton/esp-lab" / sha


def wrapper(root: Path) -> Path:
    return root / "usr/local/bin/skeleton-esp-lab"


def assert_modes_and_tree(root: Path):
    target = runtime(root)
    paths = sorted(str(p.relative_to(target)) for p in target.rglob("*"))
    assert paths == ["core", "core/__init__.py", "core/home_edge", "core/home_edge/esp_lab.py", "manifest.json"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o555
    for rel in ["core", "core/home_edge"]:
        assert stat.S_IMODE((target / rel).stat().st_mode) == 0o555
    for rel in ["core/__init__.py", "core/home_edge/esp_lab.py", "manifest.json"]:
        assert stat.S_IMODE((target / rel).stat().st_mode) == 0o444
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["schema"] == "skeleton.home_edge.esp_lab_stage1_manifest.v1"
    assert manifest["source_sha"] == SHA
    assert set(manifest["files"]) == {"core/__init__.py", "core/home_edge/esp_lab.py"}
    assert stat.S_IMODE(wrapper(root).stat().st_mode) == 0o755


def test_first_install_runtime_wrapper_and_canary(tmp_path):
    root = make_root(tmp_path)
    log = fake_apt(root)
    result = run_installer(root, payload())
    assert result.stderr == b""
    out = json.loads(result.stdout)
    assert out == {
        "schema": "skeleton.home_edge.esp_lab_stage1_activation_result.v1",
        "runtime_state": "READY",
        "source_sha": SHA,
        "candidate_count": 0,
        "device_canary": "awaiting_physical_device",
        "dependency_installed_by_operation": True,
        "idempotent_reuse": False,
    }
    assert_modes_and_tree(root)
    text = wrapper(root).read_text()
    assert f"PYTHONPATH={runtime(root)} exec /usr/bin/python3 -m core.home_edge.esp_lab" in text
    direct = subprocess.run(
        [str(wrapper(root)), "discover", "--sysfs-root", str(root / "sys/class/tty")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "ignored"},
        timeout=20,
    )
    assert direct.returncode == 0
    assert json.loads(direct.stdout) == []
    assert "update" in log.read_text()
    assert "install -y --no-install-recommends esptool" in log.read_text()


def test_candidate_canary_count_only_and_no_serial_probe(tmp_path):
    root = make_root(tmp_path)
    fake_apt(root)
    module = (
        b"import json, sys\n"
        b"args=' '.join(sys.argv[1:])\n"
        b"for bad in ['read-mac','flash-id','inspect','observe']:\n"
        b"    assert bad not in args\n"
        b"print(json.dumps([{'device':'/dev/ttyUSB9','product':'secret board'}]))\n"
    )
    result = run_installer(root, payload(module_body=module))
    assert result.returncode == 0, result.stderr
    out_text = result.stdout.decode()
    out = json.loads(out_text)
    assert out["candidate_count"] == 1
    assert out["device_canary"] == "serial_candidates_present"
    assert "/dev/ttyUSB9" not in out_text
    assert "secret board" not in out_text


def test_same_sha_rerun_reuses_without_apt_or_file_changes(tmp_path):
    root = make_root(tmp_path)
    log = fake_apt(root)
    first = run_installer(root, payload())
    assert first.returncode == 0, first.stderr
    before = {
        "target": runtime(root).stat(),
        "wrapper": wrapper(root).stat(),
        "runtime_hash": hashlib.sha256((runtime(root) / "manifest.json").read_bytes()).hexdigest(),
        "wrapper_hash": hashlib.sha256(wrapper(root).read_bytes()).hexdigest(),
        "apt": log.read_text(),
    }
    second = run_installer(root, payload())
    assert second.returncode == 0, second.stderr
    out = json.loads(second.stdout)
    assert out["idempotent_reuse"] is True
    assert runtime(root).stat().st_ino == before["target"].st_ino
    assert runtime(root).stat().st_mtime_ns == before["target"].st_mtime_ns
    assert wrapper(root).stat().st_ino == before["wrapper"].st_ino
    assert wrapper(root).stat().st_mtime_ns == before["wrapper"].st_mtime_ns
    assert hashlib.sha256((runtime(root) / "manifest.json").read_bytes()).hexdigest() == before["runtime_hash"]
    assert hashlib.sha256(wrapper(root).read_bytes()).hexdigest() == before["wrapper_hash"]
    assert log.read_text() == before["apt"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: {**p, "extra": True},
        lambda p: {**p, "files": [*p["files"], p["files"][0]]},
        lambda p: {**p, "files": [p["files"][1], p["files"][0]]},
        lambda p: {**p, "files": [p["files"][0]]},
        lambda p: {**p, "files": [{**p["files"][0], "path": "core/bad.py"}, p["files"][1]]},
        lambda p: {**p, "files": [{**p["files"][0], "base64": "%%%bad%%%"}, p["files"][1]]},
        lambda p: {**p, "files": [{**p["files"][0], "sha256": "0" * 64}, p["files"][1]]},
        lambda p: {**p, "source_sha": SHA.upper()},
        lambda p: {**p, "source_sha": "x"},
    ],
)
def test_malformed_payloads_do_not_mutate(tmp_path, mutate):
    root = make_root(tmp_path)
    log = fake_apt(root)
    result = run_installer(root, mutate(payload()))
    assert result.returncode != 0
    assert not runtime(root).exists()
    assert not wrapper(root).exists()
    assert not log.exists()


def test_oversize_inputs_do_not_mutate(tmp_path):
    root = make_root(tmp_path)
    log = fake_apt(root)
    huge_encoded = json.dumps(payload(module_body=b"x" * 173000)).encode()
    assert len(huge_encoded) > 230000
    assert run_installer(root, huge_encoded).returncode != 0
    huge_decoded = payload(module_body=b"x" * 220001)
    assert run_installer(root, huge_decoded).returncode != 0
    assert not runtime(root).exists()
    assert not wrapper(root).exists()
    assert not log.exists()


@pytest.mark.parametrize("host, os_id, version", [("bad", "debian", "13"), ("home-edge-01", "ubuntu", "13"), ("home-edge-01", "debian", "12")])
def test_bad_host_or_os_do_not_mutate(tmp_path, host, os_id, version):
    root = make_root(tmp_path, host=host, os_id=os_id, version=version)
    log = fake_apt(root)
    result = run_installer(root, payload())
    assert result.returncode != 0
    assert not runtime(root).exists()
    assert not wrapper(root).exists()
    assert not log.exists()


def test_corrupt_existing_target_is_blocked_and_untouched(tmp_path):
    root = make_root(tmp_path)
    fake_apt(root)
    target = runtime(root)
    target.mkdir(parents=True)
    bad = target / "unexpected"
    bad.write_text("keep")
    before = bad.read_bytes()
    result = run_installer(root, payload())
    assert result.returncode != 0
    assert b"BLOCKED" in result.stderr
    assert bad.read_bytes() == before
    assert not wrapper(root).exists()


def test_canary_failure_rolls_back_new_target_wrapper_and_owned_dependency(tmp_path):
    root = make_root(tmp_path)
    log = fake_apt(root)
    old = wrapper(root)
    old.write_text("#!/usr/bin/env bash\necho old\n")
    old.chmod(0o755)
    unrelated = root / "opt/skeleton/esp-lab" / ("a" * 40)
    unrelated.mkdir(parents=True)
    (unrelated / "keep").write_text("keep")
    module = b"import sys\nraise SystemExit(7)\n"
    result = run_installer(root, payload(module_body=module))
    assert result.returncode != 0
    assert old.read_text() == "#!/usr/bin/env bash\necho old\n"
    assert not runtime(root).exists()
    assert (unrelated / "keep").read_text() == "keep"
    assert "remove -y esptool" in log.read_text()
    assert not (root / "usr/bin/esptool").exists()


def test_unguarded_test_root_fails_before_reading_or_mutation(tmp_path):
    root = make_root(tmp_path)
    fake_apt(root)
    env = os.environ.copy()
    env.update({"SKELETON_ESP_LAB_TEST_ROOT": str(root), "PATH": "/usr/bin:/bin"})
    result = subprocess.run(
        ["bash", str(INSTALLER)],
        input=json.dumps(payload()).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=20,
    )
    assert result.returncode != 0
    assert not runtime(root).exists()
    assert not wrapper(root).exists()
    assert not (root / "apt.log").exists()


def test_any_argument_fails_before_stdin_or_mutation(tmp_path):
    root = make_root(tmp_path)
    fake_apt(root)
    result = run_installer(root, payload(), args=["--help"])
    assert result.returncode != 0
    assert not runtime(root).exists()
    assert not wrapper(root).exists()
    assert not (root / "apt.log").exists()


def test_source_rejects_old_architecture_tokens():
    source = INSTALLER.read_text()
    lowered = source.lower()
    rejected = [
        "repo_root",
        "approved_git_head",
        "protected_installer_path",
        "install_root",
        "exec_root",
        "sudoers_path",
        "runner_user",
        "runner_service",
        "fake_root",
        "/etc/sudoers.d",
        "visudo",
        "nopasswd",
        "systemctl",
        "getent passwd",
        "git -c",
        "hash-object",
        "source_tree",
        "scripts/home_edge_esp_lab.py",
        "home_edge_esp_lab_windows_connector",
        "esp_lab_connector",
        "espconnect",
        "shared_secret",
        "bind_host",
        "allow_lan",
        "--apply",
        "--root",
        "curl",
        "wget",
        "git clone",
        "git fetch",
        "windows",
        "connector",
        "write-flash",
        "erase-flash",
        "read-flash",
    ]
    for token in rejected:
        assert token not in lowered
    assert not re.search(r"(^|[;&|\s])pip(\s|$)", lowered)
    assert "port" not in lowered

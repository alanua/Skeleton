from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_home_edge_esp_lab.sh"
APPROVED_HEAD = "5c99681f480f399b8b95eedc756734ebe789fc36"
APPROVED_BLOBS = {
    "scripts/home_edge_esp_lab.py": "d83afd466468673a68801bdf79e8e849219a338c",
    "core/home_edge/esp_lab.py": "9af234d2fe7493db4cf8c7506dd546e5a771d5cb",
    "schemas/home_edge_esp_lab_job.schema.json": "1f2daf9fcf9b553c067b3a84c494e626ccff9b75",
    "schemas/home_edge_esp_lab_observation.schema.json": "0e693f12ea71bf84175210480f4bfe89fe07e5d8",
    "schemas/home_edge_esp_lab_receipt.schema.json": "4c6a09efbb295e91740a8be54ab990ef8e4a685e",
}


def _blob_sha(path: Path) -> str:
    return subprocess.check_output(
        ["git", "hash-object", "--no-filters", "--stdin"],
        input=path.read_bytes(),
        cwd=ROOT,
        text=False,
    ).decode().strip()


def _stub_command(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _payload(tmp_path: Path, protected_installer: Path) -> dict[str, object]:
    return {
        "schema": "skeleton.home_edge.esp_lab.installer.v1",
        "operation": "install_home_edge_esp_lab_runtime_v1",
        "approved_git_head": APPROVED_HEAD,
        "repo_root": str(ROOT),
        "protected_installer_path": str(protected_installer),
        "install_root": str(tmp_path / "fake-root/usr/local/lib/skeleton/home-edge/esp-lab"),
        "exec_root": str(tmp_path / "fake-root/usr/local/libexec/skeleton/home-edge/esp-lab"),
        "sudoers_path": str(tmp_path / "fake-root/etc/sudoers.d/skeleton-home-edge-esp-lab"),
        "runner_user": "agent",
        "runner_service": "skeleton-runner-poll.service",
        "fake_root": True,
    }


def test_installer_is_static_sha_pinned_and_no_live_package_or_checkout_execution() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert "APPROVED_SCRIPT_BLOB_SHA" in text
    assert APPROVED_HEAD in text
    assert "git -C \"$REPO_ROOT\" rev-parse HEAD" in text
    assert "git -C \"$REPO_ROOT\" hash-object --no-filters --stdin" in text
    assert "/usr/bin/python3 - \"$PAYLOAD_FILE\"" in text
    assert "from core.home_edge" not in text
    assert "PYTHONPATH=\"$REPO_ROOT" not in text
    for forbidden in ("apt install", "apt-get", "pip install", "curl ", "wget ", "ssh ", "systemctl start", "systemctl restart"):
        assert forbidden not in text
    for rel, expected in APPROVED_BLOBS.items():
        assert _blob_sha(ROOT / rel) == expected


def test_payload_contract_rejects_unknown_fields_before_install(tmp_path: Path) -> None:
    protected_dir = tmp_path / "protected"
    protected_dir.mkdir()
    protected = protected_dir / INSTALLER.name
    protected.write_bytes(INSTALLER.read_bytes())
    protected.chmod(0o555)
    payload = _payload(tmp_path, protected)
    payload["unexpected"] = True

    result = subprocess.run(
        [str(protected)],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 2
    assert "BLOCKED: invalid stdin payload" in result.stderr
    assert not (tmp_path / "fake-root").exists()


def test_fake_root_integration_installs_exact_immutable_runtime(tmp_path: Path) -> None:
    protected_dir = tmp_path / "fake-root/usr/local/libexec/skeleton/home-edge/esp-lab-installer"
    protected_dir.mkdir(parents=True)
    protected = protected_dir / INSTALLER.name
    protected.write_bytes(INSTALLER.read_bytes())
    protected.chmod(0o555)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_command(bin_dir, "getent", '[[ "$1" == "passwd" && "$2" == "agent" ]]')
    _stub_command(bin_dir, "systemctl", 'printf "%s\\n" "agent"')
    _stub_command(bin_dir, "visudo", '[[ "$1" == "-cf" && -f "$2" ]]')

    payload = _payload(tmp_path, protected)
    result = subprocess.run(
        [str(protected)],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "DONE: Home Edge ESP Lab immutable runtime installed" in result.stdout
    assert f"approved_git_head={APPROVED_HEAD}" in result.stdout

    install_root = Path(str(payload["install_root"]))
    exec_root = Path(str(payload["exec_root"]))
    sudoers = Path(str(payload["sudoers_path"]))
    installed = {
        "scripts/home_edge_esp_lab.py": install_root / "scripts/home_edge_esp_lab.py",
        "core/home_edge/esp_lab.py": install_root / "core/home_edge/esp_lab.py",
        "schemas/home_edge_esp_lab_job.schema.json": install_root / "schemas/home_edge_esp_lab_job.schema.json",
        "schemas/home_edge_esp_lab_observation.schema.json": install_root / "schemas/home_edge_esp_lab_observation.schema.json",
        "schemas/home_edge_esp_lab_receipt.schema.json": install_root / "schemas/home_edge_esp_lab_receipt.schema.json",
    }
    for rel, path in installed.items():
        assert path.is_file()
        assert _blob_sha(path) == APPROVED_BLOBS[rel]
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) & 0o022 == 0
    assert stat.S_IMODE((exec_root / "home_edge_esp_lab").stat().st_mode) == 0o555
    assert stat.S_IMODE(install_root.stat().st_mode) == 0o555
    assert "PYTHONPATH=" in (exec_root / "home_edge_esp_lab").read_text(encoding="utf-8")
    assert sudoers.read_text(encoding="utf-8").strip() == f"agent ALL=(root) NOPASSWD: {exec_root}/home_edge_esp_lab *"


def test_installer_bash_syntax() -> None:
    subprocess.run(["/bin/bash", "-n", str(INSTALLER)], check=True)

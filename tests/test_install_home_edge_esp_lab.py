from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_home_edge_esp_lab.sh"


def request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "skeleton.home_edge.esp_lab.stdin_installer.v1",
        "node_id": "home-edge-esp-lab",
        "bind_host": "127.0.0.1",
        "port": 9443,
        "enable_read_only_execution": False,
        "shared_secret": "synthetic-shared-secret",
        "allowed_node_ids": ["home-edge-esp-lab"],
    }
    payload.update(overrides)
    return payload


def run_installer(args: list[str], payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )


def test_installer_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(INSTALLER)], cwd=ROOT, check=True)


def test_plan_mode_reads_stdin_and_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    result = run_installer(["--root", str(tmp_path)], request())

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["action"] == "plan_only"
    assert plan["public_safe"] is True
    assert plan["service_installed"] is False
    assert plan["default_execution"] == "plan"
    assert plan["secret_configured"] is True
    assert "synthetic-shared-secret" not in result.stdout
    assert not (tmp_path / "usr").exists()
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path / "var").exists()


def test_apply_installs_only_esp_lab_files_under_requested_root(tmp_path: Path) -> None:
    result = run_installer(["--apply", "--root", str(tmp_path)], request(enable_read_only_execution=True))

    assert result.returncode == 0, result.stderr
    install_lib = tmp_path / "usr/local/lib/skeleton-esp-lab"
    manifest = tmp_path / "etc/skeleton/esp-lab/install-manifest.json"
    secret = tmp_path / "etc/skeleton/esp-lab/shared-secret"
    assert (tmp_path / "usr/local/bin/skeleton-esp-lab").is_file()
    assert (tmp_path / "usr/local/bin/skeleton-esp-lab-windows").is_file()
    assert (install_lib / "scripts/home_edge_esp_lab.py").is_file()
    assert (install_lib / "scripts/home_edge_esp_lab_windows_connector.py").is_file()
    assert (install_lib / "core/home_edge/esp_lab.py").is_file()
    assert (install_lib / "core/home_edge/esp_lab_connector.py").is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["default_execution"] == "read_only"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert secret.read_text(encoding="utf-8") == "synthetic-shared-secret\n"


def test_apply_launcher_runs_plan_without_live_device_access(tmp_path: Path) -> None:
    result = run_installer(["--apply", "--root", str(tmp_path)], request())
    assert result.returncode == 0, result.stderr

    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "schema": "skeleton.home_edge.esp_lab.v1.job",
                "control_plane_id": "home-edge",
                "node_id": "media-pc",
                "endpoint_kind": "home_edge_local_linux",
                "adapter_kind": "linux_tty",
                "operation": "identify_chip",
                "device_ref": "/dev/ttyUSB0",
                "timeout_seconds": 5,
                "idempotency_key": "synthetic-plan",
                "execution_mode": "plan",
                "private_salt": "synthetic-private-salt",
            }
        ),
        encoding="utf-8",
    )
    launcher = tmp_path / "usr/local/bin/skeleton-esp-lab"
    completed = subprocess.run(
        [str(launcher), "plan", "--job", str(job)],
        env={"PATH": os.environ.get("PATH", "")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "commands": [["esptool", "--port", "/dev/ttyUSB0", "read-mac"]],
        "execute": False,
    }


def test_rejects_unsafe_stdin_request_before_apply(tmp_path: Path) -> None:
    result = run_installer(["--apply", "--root", str(tmp_path)], request(bind_host="0.0.0.0", allow_lan=False))

    assert result.returncode == 1
    assert "non-loopback bind requires allow_lan" in result.stderr
    assert not (tmp_path / "usr").exists()


def test_old_architecture_negative_regression_tokens_are_absent() -> None:
    source = INSTALLER.read_text(encoding="utf-8").lower()
    forbidden = (
        "sudoers",
        "/etc/sudoers.d",
        "systemctl",
        ".service",
        ".timer",
        "poller",
        "poll_github",
        "runner_poll",
        "controller",
        "--repo-root",
        "repo_root",
        "/home/agent/agent-dev/repos/skeleton",
        "home_edge_exec",
        "skeleton-home-edge-executor",
        "write-flash",
        "erase-flash",
        "read-flash",
        "dump-mem",
        "verify-flash",
        "0.0.0.0",
        "curl ",
        "wget ",
        "git clone",
    )
    for token in forbidden:
        assert token not in source

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_skeleton_local_ops.sh"


def test_installer_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(INSTALLER)], cwd=ROOT, check=True)


def test_installer_is_local_only() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "systemctl" not in text
    assert "docker" not in text
    assert "curl" not in text
    assert "wget" not in text
    assert "git clone" not in text
    assert "--system-site-packages" in text

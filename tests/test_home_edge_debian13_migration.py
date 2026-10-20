from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops/home_edge/debian13"
PROTECTED_ATA = "ata-SAMSUNG_MZ7PD128HCFV-000H1_S1MBNYAH205253"
PROTECTED_WWN = "wwn-0x5002538500000000"
SCRIPTS = [
    "inventory.sh",
    "inspect-external.sh",
    "backup-current.sh",
    "image-current.sh",
    "verify-backup.sh",
    "bootstrap.sh",
    "restore-private.sh",
    "first-boot-guard.sh",
    "acceptance.sh",
]


def run_script(name: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(OPS / name), *args],
        cwd=ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def json_out(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def test_absent_external_disk_returns_identity_pending_and_zero_side_effects() -> None:
    result = run_script("inspect-external.sh", "--json")
    assert result.returncode == 0
    payload = json_out(result)
    assert payload["status"] == "identity_pending"
    assert payload["side_effects"] == []


@pytest.mark.parametrize("target", ["/dev/sdb", "/dev/nvme1n1", "usb-stick-label"])
def test_sdx_only_and_ambiguous_targets_rejected_before_side_effects(target: str) -> None:
    result = run_script("backup-current.sh", "--plan", "--target", target)
    payload = json_out(result)
    assert result.returncode == 2
    assert payload["reason"] == "stable_by_id_required"
    assert payload["side_effects"] == []


@pytest.mark.parametrize("script", ["inspect-external.sh", "backup-current.sh", "image-current.sh", "bootstrap.sh"])
@pytest.mark.parametrize("protected_id", [PROTECTED_ATA, PROTECTED_WWN])
def test_protected_internal_disk_aliases_rejected_in_mutating_paths(script: str, protected_id: str) -> None:
    target = f"/dev/disk/by-id/{protected_id}"
    if script == "inspect-external.sh":
        result = run_script(script, "--json", env={"HE_FAKE_EXTERNAL_ID": target})
    else:
        result = run_script(script, "--plan", "--target", target)
    payload = json_out(result)
    assert result.returncode == 2
    assert payload["reason"] == "protected_internal_disk"


def test_bootstrap_legacy_bios_plan_grub_external_only_and_no_nvram() -> None:
    target = "/dev/disk/by-id/usb-Samsung_Portable_SSD_EXTSAFE0001"
    result = run_script("bootstrap.sh", "--plan", "--target", target)
    assert result.returncode == 0, result.stderr
    payload = json_out(result)
    command_text = json.dumps(payload["commands"])
    assert payload["boot"]["grub_target"] == "i386-pc"
    assert ["grub-install", "--target=i386-pc", "--boot-directory=<target-root>/boot", target] in payload["commands"]
    assert "efibootmgr" not in command_text
    assert "NVRAM" in payload["forbidden_absent"]
    assert PROTECTED_ATA not in command_text


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"HE_FAKE_INSUFFICIENT_SPACE": "1"}, "insufficient_space"),
        ({"HE_FAKE_UNEXPECTED_MOUNT_OPTIONS": "1"}, "unexpected_mount_options"),
        ({"HE_FAKE_TARGET_SYMLINK_UNSAFE": "1"}, "symlink_target"),
        ({"HE_FAKE_IDENTITY_DRIFT": "1"}, "identity_drift"),
    ],
)
def test_backup_refuses_space_mount_symlink_and_identity_drift(env: dict[str, str], reason: str) -> None:
    result = run_script("backup-current.sh", "--plan", "--target", "/dev/disk/by-id/usb-EXTSAFE0001", env=env)
    payload = json_out(result)
    assert result.returncode == 2
    assert payload["reason"] == reason


def test_backup_refuses_root_target() -> None:
    result = run_script("backup-current.sh", "--plan", "--target", "/")
    assert json_out(result)["reason"] == "stable_by_id_required"


def test_manifest_hash_and_read_only_image_verification_success_and_corruption(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    image = tmp_path / "rollback.img"
    manifest.write_text('{"schema":"fixture"}', encoding="utf-8")
    image.write_bytes(b"rollback-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    ok = run_script("verify-backup.sh", "--manifest", str(manifest), "--image", str(image), env={"HE_EXPECT_IMAGE_SHA256": digest})
    assert json_out(ok)["status"] == "verified"
    bad = run_script("verify-backup.sh", "--manifest", str(manifest), "--image", str(image), env={"HE_EXPECT_IMAGE_SHA256": "0" * 64})
    payload = json_out(bad)
    assert bad.returncode == 2
    assert payload["reason"] == "image_sha256_mismatch"


def test_restore_dry_run_and_staged_target_never_live_root_rsync(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    live = run_script("restore-private.sh", "--dry-run", "--target-root", "/", "--manifest", str(manifest))
    assert json_out(live)["reason"] == "live_root_restore_forbidden"
    stage = tmp_path / "home-edge-stage"
    stage.mkdir()
    ok = run_script(
        "restore-private.sh",
        "--dry-run",
        "--target-root",
        str(stage),
        "--manifest",
        str(manifest),
        env={"HE_FAKE_ALLOWED_STAGE_ROOT": str(stage)},
    )
    payload = json_out(ok)
    assert payload["live_root_rsync"] is False
    assert {"hashes", "permissions", "numeric_ids", "acls", "xattrs"} <= set(payload["verifies"])


@pytest.mark.parametrize(
    ("script", "args", "reason"),
    [
        ("backup-current.sh", ["--apply", "--target", "/dev/disk/by-id/usb-EXTSAFE0001"], "backup_write_approval_required"),
        ("image-current.sh", ["--apply", "--target", "/dev/disk/by-id/usb-EXTSAFE0001"], "image_write_approval_required"),
        ("bootstrap.sh", ["--apply", "--target", "/dev/disk/by-id/usb-EXTSAFE0001"], "external_repartition_approval_required"),
        ("first-boot-guard.sh", ["--arm", "--target", "/dev/disk/by-id/usb-EXTSAFE0001"], "reboot_test_boot_approval_required"),
    ],
)
def test_explicit_approval_required_independently(script: str, args: list[str], reason: str) -> None:
    result = run_script(script, *args)
    payload = json_out(result)
    assert result.returncode == 2
    assert payload["reason"] == reason


def test_first_boot_timeout_rollback_and_acceptance_commit_paths(tmp_path: Path) -> None:
    marker = tmp_path / "acceptance.commit"
    missing = run_script("first-boot-guard.sh", env={"HE_ACCEPTANCE_COMMIT_MARKER": str(marker)})
    assert missing.returncode == 3
    assert json_out(missing)["status"] == "rollback_required"
    marker.write_text("accepted\n", encoding="utf-8")
    accepted = run_script("first-boot-guard.sh", env={"HE_ACCEPTANCE_COMMIT_MARKER": str(marker)})
    assert json_out(accepted)["status"] == "accepted"


def test_acceptance_matrix_typed_results_physical_not_auto_verified_and_media_invariants() -> None:
    result = run_script("acceptance.sh", env={"HE_ACCEPTANCE_ALL_SOFTWARE_PASS": "1"})
    assert result.returncode == 0
    payload = json_out(result)
    by_id = {item["id"]: item for item in payload["items"]}
    assert len(by_id) >= 14
    assert by_id["pipewire_creative"]["physical_required"] is True
    assert by_id["pipewire_creative"]["physically_verified"] is False
    assert by_id["pipewire_creative"]["status"] == "physical_pending"
    assert payload["invariants"]["pipewire_sink"] == "alsa_output.pci-0000_00_1f.3.analog-stereo"
    assert payload["invariants"]["pipewire_device"] == "HDA Intel PCH"
    assert payload["invariants"]["pipewire_codec"] == "ALC671 Analog"
    assert payload["invariants"]["hdmi_default_allowed"] is False
    assert payload["invariants"]["samsung_contract"] == "external-tablet-kiosk-current"
    assert payload["invariants"]["obsolete_local_display_workload"] is False
    assert payload["invariants"]["obsolete_local_display_port"] is False
    assert payload["invariants"]["youtube_vaapi_requires_live_decode"] is True


def test_architecture_and_operations_contracts_are_machine_readable() -> None:
    architecture = json.loads((OPS / "architecture.json").read_text(encoding="utf-8"))
    operations = yaml.safe_load((OPS / "operations.yaml").read_text(encoding="utf-8"))
    assert architecture["external_identity"]["required_prefix"] == "/dev/disk/by-id/"
    assert architecture["boot"]["grub_target"] == "i386-pc"
    assert "internal_cutover" in architecture["approval_gates"]
    for operation in operations["operations"]:
        for field in ("argv", "node", "run_as", "timeout_seconds", "risk", "approvals", "preconditions", "independent_verification", "retry", "rollback"):
            assert field in operation
        flat = json.dumps(operation)
        forbidden = ["playlist", "cookie", "tailscale identity", "password", "secret"]
        assert not any(word in flat.lower() for word in forbidden)


def test_scripts_are_bash_syntax_clean() -> None:
    for script in SCRIPTS:
        result = subprocess.run(["bash", "-n", str(OPS / script)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_public_files_do_not_contain_private_values_or_obsolete_samsung_contract() -> None:
    checked = [
        ROOT / "docs/home_edge/HOME_EDGE_DEBIAN_MEDIA_PLATFORM_V2.md",
        *[OPS / name for name in ["README.md", "architecture.json", "package-manifest.yaml", "operations.yaml"]],
        *[OPS / name for name in SCRIPTS],
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in checked).lower()
    assert "photo-frame" not in combined
    assert "8099" not in combined
    assert "playlist url" not in combined
    assert "browser cookie" not in combined
    assert "password" not in combined
    assert "secret=" not in combined

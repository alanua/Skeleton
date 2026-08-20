from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .executor import (
    DEFAULT_NODE_ID,
    ExecutionLane,
    ExecutionUser,
    HomeEdgeExecError,
    HomeEdgeExecReceipt,
    HomeEdgeExecRequest,
    sign_request,
)
from .executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request


REQUEST_SCHEMA = "skeleton.home_edge.remote_windows_audit.request.v1"
RECEIPT_SCHEMA = "skeleton.home_edge.remote_windows_audit.receipt.v1"
ENROLLMENT_SCHEMA = "skeleton.home_edge.remote_audit_enrollment.v1"
OPERATION = "read_only_system_audit_v1"
TARGET_NODE = DEFAULT_NODE_ID
IDEMPOTENCY_KEY = "remote-windows-audit-read-only-system-audit-v1"
AUDIT_USER = "skeleton-audit"
DEFAULT_TIMEOUT_SECONDS = 180
PUBLIC_RECEIPT_FIELDS = (
    "schema",
    "operation",
    "node_id",
    "machine_identity_hash",
    "status",
    "verdict",
    "confidence",
    "reasons",
    "evidence",
    "collected_at",
    "executor_receipt_hash",
    "private_evidence_sha256",
    "privacy_boundary",
)
PRIVATE_KEY_RE = re.compile(r"(private|secret|token|password|credential|history|photo|message|browser)", re.IGNORECASE)


READ_ONLY_SYSTEM_AUDIT_SCRIPT = r"""
$ErrorActionPreference = "Stop"

function Section($Name, $Value) {
  [PSCustomObject]@{ name = $Name; value = $Value }
}

$identityRoot = Join-Path $env:ProgramData "Skeleton\RemoteAudit\identity"
$machinePublicKeyPath = Join-Path $identityRoot "machine_identity_ed25519.pub"
$machineFingerprintPath = Join-Path $identityRoot "machine_identity_ed25519.fingerprint.txt"
$machineFingerprint = $null
if (Test-Path -LiteralPath $machineFingerprintPath) {
  $machineFingerprint = (Get-Content -LiteralPath $machineFingerprintPath -Raw).Trim()
}

$os = Get-CimInstance -ClassName Win32_OperatingSystem
$computer = Get-CimInstance -ClassName Win32_ComputerSystem
$bios = Get-CimInstance -ClassName Win32_BIOS
$cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
$disks = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" |
  Select-Object DeviceID, Size, FreeSpace
$tpm = $null
try { $tpm = Get-Tpm } catch { $tpm = $null }
$secureBoot = $null
try { $secureBoot = Confirm-SecureBootUEFI } catch { $secureBoot = $null }
$defender = $null
try { $defender = Get-MpComputerStatus } catch { $defender = $null }
$openssh = Get-Service -Name sshd -ErrorAction SilentlyContinue
$tailscale = Get-Service -Name Tailscale -ErrorAction SilentlyContinue
$tailscaleStatus = $null
try {
  $tailscaleStatus = & "$env:ProgramFiles\Tailscale\tailscale.exe" status --json 2>$null | ConvertFrom-Json
} catch { $tailscaleStatus = $null }

$payload = [ordered]@{
  schema = "skeleton.home_edge.remote_windows_audit.private_evidence.v1"
  operation = "read_only_system_audit_v1"
  collected_at = (Get-Date).ToUniversalTime().ToString("o")
  machine_identity_fingerprint = $machineFingerprint
  os = [ordered]@{
    caption = $os.Caption
    version = $os.Version
    build_number = $os.BuildNumber
    architecture = $os.OSArchitecture
    install_date = $os.InstallDate
    last_boot_up_time = $os.LastBootUpTime
  }
  hardware = [ordered]@{
    manufacturer = $computer.Manufacturer
    model = $computer.Model
    total_physical_memory = [Int64]$computer.TotalPhysicalMemory
    processor = $cpu.Name
    cores = [Int32]$cpu.NumberOfCores
    logical_processors = [Int32]$cpu.NumberOfLogicalProcessors
    bios_serial_sha256 = if ($bios.SerialNumber) {
      $bytes = [Text.Encoding]::UTF8.GetBytes($bios.SerialNumber)
      ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } else { $null }
  }
  storage = @($disks | ForEach-Object {
    [ordered]@{ drive = $_.DeviceID; size = [Int64]$_.Size; free = [Int64]$_.FreeSpace }
  })
  security = [ordered]@{
    secure_boot = $secureBoot
    tpm_present = if ($tpm -ne $null) { [bool]$tpm.TpmPresent } else { $null }
    tpm_ready = if ($tpm -ne $null) { [bool]$tpm.TpmReady } else { $null }
    defender_enabled = if ($defender -ne $null) { [bool]$defender.AntivirusEnabled } else { $null }
    defender_realtime = if ($defender -ne $null) { [bool]$defender.RealTimeProtectionEnabled } else { $null }
    defender_signature_age_days = if ($defender -ne $null -and $defender.AntivirusSignatureLastUpdated) {
      [Int32]((Get-Date) - $defender.AntivirusSignatureLastUpdated).TotalDays
    } else { $null }
  }
  remote_access = [ordered]@{
    openssh_status = if ($openssh) { $openssh.Status.ToString() } else { "Absent" }
    tailscale_status = if ($tailscale) { $tailscale.Status.ToString() } else { "Absent" }
    tailscale_backend_state = if ($tailscaleStatus) { $tailscaleStatus.BackendState } else { $null }
    tailscale_self_online = if ($tailscaleStatus -and $tailscaleStatus.Self) { [bool]$tailscaleStatus.Self.Online } else { $null }
  }
  collection_scope = @(
    "Win32_OperatingSystem",
    "Win32_ComputerSystem",
    "Win32_BIOS_serial_hash_only",
    "Win32_Processor",
    "Win32_LogicalDisk_fixed_only",
    "Get-Tpm",
    "Confirm-SecureBootUEFI",
    "Get-MpComputerStatus",
    "Tailscale_status_json"
  )
  excluded_scope = @("personal_files", "messages", "browser_history", "photos", "document_content")
}
$payload | ConvertTo-Json -Depth 8 -Compress
"""


class RemoteWindowsAuditTransport(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> HomeEdgeExecReceipt: ...


@dataclass(frozen=True)
class RemoteAuditEnrollment:
    node_id: str
    machine_identity_public_key: str
    machine_identity_fingerprint: str
    audit_user: str = AUDIT_USER
    transport: str = "tailscale_openssh"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RemoteAuditEnrollment":
        if not isinstance(data, Mapping):
            raise ValueError("enrollment must be an object")
        node_id = _required_string(data.get("node_id"), "node_id")
        if node_id != TARGET_NODE:
            raise ValueError("node_id must be bound to canonical Home Edge control fabric")
        audit_user = _optional_string(data.get("audit_user")) or AUDIT_USER
        if audit_user != AUDIT_USER:
            raise ValueError("audit_user must be the dedicated Skeleton audit account")
        transport = _optional_string(data.get("transport")) or "tailscale_openssh"
        if transport != "tailscale_openssh":
            raise ValueError("transport must be tailscale_openssh")
        return cls(
            node_id=node_id,
            machine_identity_public_key=_required_public_key(data.get("machine_identity_public_key")),
            machine_identity_fingerprint=_required_fingerprint(data.get("machine_identity_fingerprint")),
            audit_user=audit_user,
            transport=transport,
        )


def build_read_only_system_audit_request(
    enrollment: RemoteAuditEnrollment,
    *,
    request_id: str | None = None,
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    secret = hmac_secret or os.environ.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        raise HomeEdgeExecError("node HMAC secret is not configured")
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": request_id or f"remote-windows-audit-{uuid4()}",
            "node_id": enrollment.node_id,
            "execution_lane": ExecutionLane.READ_ONLY.value,
            "run_as": ExecutionUser.DESKTOP_USER.value,
            "argv": [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                READ_ONLY_SYSTEM_AUDIT_SCRIPT,
            ],
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "idempotency_key": IDEMPOTENCY_KEY,
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"remote-windows-audit-{uuid4()}",
            "max_output_bytes": 128_000,
        }
    )
    return {**request.to_mapping(), "signature": sign_request(request, secret)}


def run_read_only_system_audit_v1(
    enrollment: RemoteAuditEnrollment | Mapping[str, Any],
    *,
    private_evidence_path: str | Path | None = None,
    transport: RemoteWindowsAuditTransport | None = None,
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    parsed = enrollment if isinstance(enrollment, RemoteAuditEnrollment) else RemoteAuditEnrollment.from_mapping(enrollment)
    request = build_read_only_system_audit_request(parsed, hmac_secret=hmac_secret)
    executor = transport or execute_home_edge_request
    receipt = executor(request)
    evidence = _decode_private_evidence(receipt)
    _validate_evidence_boundary(evidence)
    if evidence.get("machine_identity_fingerprint") != parsed.machine_identity_fingerprint:
        raise HomeEdgeExecError("machine identity fingerprint mismatch")
    verdict = evaluate_windows_audit_evidence(evidence)
    private_sha = _hash_json(evidence)
    if private_evidence_path is not None:
        target = Path(private_evidence_path)
        _reject_repo_path(target)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    public = {
        "schema": RECEIPT_SCHEMA,
        "operation": OPERATION,
        "node_id": parsed.node_id,
        "machine_identity_hash": _hash_text(parsed.machine_identity_public_key),
        "status": receipt.status,
        "verdict": verdict["verdict"],
        "confidence": verdict["confidence"],
        "reasons": verdict["reasons"],
        "evidence": verdict["public_evidence"],
        "collected_at": evidence.get("collected_at"),
        "executor_receipt_hash": receipt.receipt_hash,
        "private_evidence_sha256": private_sha,
        "privacy_boundary": "PUBLIC_SAFE_BOOTSTRAP_CODE / PRIVATE_NODE_IDENTITY_AND_EVIDENCE_ONLY",
    }
    return {key: public[key] for key in PUBLIC_RECEIPT_FIELDS}


def evaluate_windows_audit_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    os_info = _mapping(evidence.get("os"))
    hardware = _mapping(evidence.get("hardware"))
    security = _mapping(evidence.get("security"))
    remote = _mapping(evidence.get("remote_access"))
    storage = evidence.get("storage") if isinstance(evidence.get("storage"), list) else []
    reasons: list[str] = []
    score = 0

    if "64" not in str(os_info.get("architecture", "")):
        return _verdict("RETIRE", 0.92, ["unsupported_non_x64_windows"], evidence)
    caption = str(os_info.get("caption", ""))
    build = _int(os_info.get("build_number"))
    if "Windows 10" not in caption and "Windows 11" not in caption:
        return _verdict("RETIRE", 0.9, ["unsupported_legacy_windows"], evidence)
    if build is not None and build < 19041:
        return _verdict("RETIRE", 0.88, ["windows_build_before_supported_floor"], evidence)

    memory_gb = (_int(hardware.get("total_physical_memory")) or 0) / (1024**3)
    if memory_gb < 8:
        reasons.append("memory_below_8gb")
        score += 2
    elif memory_gb < 16:
        reasons.append("memory_below_preferred_16gb")
        score += 1

    free_values = [_int(item.get("free")) for item in storage if isinstance(item, Mapping)]
    if free_values and max(v or 0 for v in free_values) < 32 * 1024**3:
        reasons.append("free_disk_below_32gb")
        score += 2

    if security.get("secure_boot") is False:
        reasons.append("secure_boot_disabled")
        score += 1
    if security.get("tpm_present") is False:
        reasons.append("tpm_missing")
        score += 1
    if security.get("defender_enabled") is False or security.get("defender_realtime") is False:
        reasons.append("defender_not_active")
        score += 2
    signature_age = _int(security.get("defender_signature_age_days"))
    if signature_age is not None and signature_age > 14:
        reasons.append("defender_signatures_stale")
        score += 1

    if remote.get("openssh_status") != "Running":
        reasons.append("openssh_not_running")
        score += 1
    if remote.get("tailscale_status") != "Running" or remote.get("tailscale_backend_state") not in {None, "Running"}:
        reasons.append("tailscale_not_ready")
        score += 1

    if not reasons:
        return _verdict("KEEP", 0.86, ["supported_windows_security_and_capacity_ok"], evidence)
    if score >= 5:
        verdict = "REINSTALL"
        confidence = 0.82
    elif any(reason in reasons for reason in ("memory_below_8gb", "free_disk_below_32gb")):
        verdict = "UPGRADE"
        confidence = 0.78
    else:
        verdict = "REPAIR"
        confidence = 0.74
    return _verdict(verdict, confidence, reasons, evidence)


def _verdict(verdict: str, confidence: float, reasons: list[str], evidence: Mapping[str, Any]) -> dict[str, Any]:
    os_info = _mapping(evidence.get("os"))
    hardware = _mapping(evidence.get("hardware"))
    security = _mapping(evidence.get("security"))
    remote = _mapping(evidence.get("remote_access"))
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasons": reasons,
        "public_evidence": {
            "windows_family": _public_windows_family(str(os_info.get("caption", ""))),
            "build_number": os_info.get("build_number"),
            "architecture": os_info.get("architecture"),
            "memory_gb": round((_int(hardware.get("total_physical_memory")) or 0) / (1024**3), 1),
            "secure_boot": security.get("secure_boot"),
            "tpm_present": security.get("tpm_present"),
            "defender_enabled": security.get("defender_enabled"),
            "openssh_status": remote.get("openssh_status"),
            "tailscale_status": remote.get("tailscale_status"),
            "collection_scope": evidence.get("collection_scope", []),
            "excluded_scope": evidence.get("excluded_scope", []),
        },
    }


def _decode_private_evidence(receipt: HomeEdgeExecReceipt) -> dict[str, Any]:
    if receipt.status != "ok" or receipt.exit_code != 0:
        raise HomeEdgeExecError("remote read-only audit did not complete")
    decoded = json.loads(receipt.stdout)
    if not isinstance(decoded, dict):
        raise HomeEdgeExecError("remote read-only audit returned non-object evidence")
    if decoded.get("operation") != OPERATION:
        raise HomeEdgeExecError("remote evidence operation mismatch")
    return decoded


def _validate_evidence_boundary(evidence: Mapping[str, Any]) -> None:
    text = json.dumps(evidence, sort_keys=True)
    for key in evidence:
        if PRIVATE_KEY_RE.search(str(key)) and key not in {"excluded_scope"}:
            raise HomeEdgeExecError("private evidence contains disallowed personal scope")
    forbidden = ("browser_history", "photos", "messages", "personal_files")
    if any(value in text for value in forbidden) and "excluded_scope" not in evidence:
        raise HomeEdgeExecError("private evidence boundary is ambiguous")


def _reject_repo_path(path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return
    raise HomeEdgeExecError("private evidence path must stay outside repository")


def _required_string(value: object, field: str) -> str:
    text = _optional_string(value)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    return value.strip()


def _required_public_key(value: object) -> str:
    text = _required_string(value, "machine_identity_public_key")
    if not text.startswith("ssh-ed25519 ") or len(text.split()) < 2:
        raise ValueError("machine_identity_public_key must be an ssh-ed25519 public key")
    return text


def _required_fingerprint(value: object) -> str:
    text = _required_string(value, "machine_identity_fingerprint")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/=]{20,80}", text):
        raise ValueError("machine_identity_fingerprint must be an OpenSSH SHA256 fingerprint")
    return text


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _public_windows_family(caption: str) -> str:
    if "Windows 11" in caption:
        return "Windows 11"
    if "Windows 10" in caption:
        return "Windows 10"
    return "unsupported_or_unknown"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Mapping[str, Any]) -> str:
    return _hash_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the strict read-only Windows remote audit.")
    parser.add_argument("--enrollment", required=True, help="Private enrollment JSON path on the controller.")
    parser.add_argument("--private-evidence-out", help="Private evidence output path outside the repository.")
    args = parser.parse_args(argv)
    try:
        enrollment = json.loads(Path(args.enrollment).read_text(encoding="utf-8"))
        receipt = run_read_only_system_audit_v1(
            enrollment,
            private_evidence_path=args.private_evidence_out,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns structured failure.
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

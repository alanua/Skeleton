from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol


SCHEMA_VERSION = "skeleton.home_edge.esp_lab.v1"
PUBLIC_NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
LINUX_DEVICE_RE = re.compile(r"^/dev/tty(?:USB|ACM)[0-9]+$")
COM_RE = re.compile(r"^(?:\\\\\.\\)?COM([1-9][0-9]{0,2})$", re.IGNORECASE)
SAFE_PRODUCT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:+/-]{0,127}$")
SUPPORTED_FAMILIES = (
    "esp32",
    "esp32-c3",
    "esp32-c5",
    "esp32-c6",
    "esp32-h2",
    "esp32-p4",
    "esp32-s2",
    "esp32-s3",
    "esp8266-limited",
)
SUPPORTED_OPERATIONS = (
    "discover_serial_candidates",
    "identify_chip",
    "inspect_flash_identity",
    "observe_serial_bounded",
)
ENDPOINT_KINDS = ("home_edge_local_linux", "windows_workstation_connector")
ADAPTER_KINDS = ("linux_tty", "windows_com")
READ_ONLY_COMMANDS = {
    "identify_chip": ("read-mac",),
    "inspect_flash_identity": ("flash-id",),
}
FORBIDDEN_TOKENS = {
    "write-flash",
    "erase-flash",
    "erase-region",
    "write-flash-status",
    "write-mem",
    "load-ram",
    "read-flash",
    "dump-mem",
    "verify-flash",
    "merge-bin",
    "--force",
    "--encrypt",
    "--erase-all",
    "backup",
    "restore",
}
FORBIDDEN_EXE_NAMES = {"cmd.exe", "powershell.exe", "pwsh.exe", "wmi.exe", "wmic.exe"}
ALLOWED_BAUDS = (9600, 115200, 460800, 921600)
MAX_TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 4096
DEFAULT_SERIAL_MAX_BYTES = 4096

MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")
BARE_MAC_RE = re.compile(r"\b[0-9a-fA-F]{12}\b")
IP_RE = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
VID_PID_PAIR_RE = re.compile(r"\b(?:vid|pid)[:=]?[0-9a-fA-F]{4}.*?(?:vid|pid)[:=]?[0-9a-fA-F]{4}\b", re.IGNORECASE)


class EspLabError(ValueError):
    """Raised for rejected ESP Lab jobs and unsafe execution attempts."""


@dataclass(frozen=True)
class CommandResult:
    status: str
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int | None = None
    duration_ms: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class SerialObservationResult:
    status: str
    data: bytes = b""
    duration_ms: int = 0
    terminal_status: str = "completed"
    reason: str | None = None


class EspToolAdapter(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        env: dict[str, str],
        max_output_bytes: int,
    ) -> CommandResult:
        ...


class SerialObservationAdapter(Protocol):
    def observe(
        self,
        device_path: str,
        *,
        baud: int,
        timeout_seconds: int,
        max_bytes: int,
    ) -> SerialObservationResult:
        ...


class RegistryAdapter(Protocol):
    def serial_comm_values(self) -> dict[str, str]:
        ...


class SubprocessEspToolAdapter:
    adapter_name = "subprocess.esptool"
    adapter_version = "v1"

    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        env: dict[str, str],
        max_output_bytes: int,
    ) -> CommandResult:
        started = datetime.now(UTC)
        try:
            completed = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
                shell=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                status="timeout",
                stdout=_bound_bytes(exc.stdout or b"", max_output_bytes),
                stderr=_bound_bytes(exc.stderr or b"", max_output_bytes),
                duration_ms=_elapsed_ms(started),
                reason="timeout",
            )
        return CommandResult(
            status="observed" if completed.returncode == 0 else "failed",
            stdout=_bound_bytes(completed.stdout, max_output_bytes),
            stderr=_bound_bytes(completed.stderr, max_output_bytes),
            exit_code=completed.returncode,
            duration_ms=_elapsed_ms(started),
            reason=None if completed.returncode == 0 else "nonzero_exit",
        )


class WinRegSerialCommAdapter:
    def serial_comm_values(self) -> dict[str, str]:
        import winreg

        values: dict[str, str] = {}
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if isinstance(value, str):
                    values[str(name)] = value
                index += 1
        return values


def discover_serial_candidates(sysfs_root: str | Path = "/sys/class/tty") -> list[dict[str, Any]]:
    root = Path(sysfs_root)
    candidates: list[dict[str, Any]] = []
    if not root.exists():
        return candidates
    for entry in sorted(root.iterdir(), key=lambda value: value.name):
        device_path = f"/dev/{entry.name}"
        if not LINUX_DEVICE_RE.fullmatch(device_path):
            continue
        metadata = _read_usb_metadata(entry)
        candidates.append(
            {
                "device_ref": device_path,
                "endpoint_kind": "home_edge_local_linux",
                "adapter_kind": "linux_tty",
                "driver": metadata.get("driver"),
                "product": metadata.get("product"),
                "vid": metadata.get("vid"),
                "pid": metadata.get("pid"),
            }
        )
    return candidates


def discover_windows_serial_candidates(registry: RegistryAdapter | None = None) -> list[dict[str, Any]]:
    reg = registry or WinRegSerialCommAdapter()
    candidates: list[dict[str, Any]] = []
    for name, value in sorted(reg.serial_comm_values().items()):
        try:
            com = canonicalize_windows_com(value)
        except EspLabError:
            continue
        candidates.append(
            {
                "device_ref": com,
                "endpoint_kind": "windows_workstation_connector",
                "adapter_kind": "windows_com",
                "registry_value_name_hash": hashlib.sha256(name.encode("utf-8")).hexdigest()[:16],
            }
        )
    return candidates


def validate_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema",
        "control_plane_id",
        "node_id",
        "endpoint_kind",
        "adapter_kind",
        "operation",
        "device_ref",
        "device_path",
        "timeout_seconds",
        "idempotency_key",
        "execution_mode",
        "private_salt",
        "baud",
        "max_bytes",
        "expected_family",
    }
    unknown = set(job) - allowed
    if unknown:
        raise EspLabError(f"unknown job field: {sorted(unknown)[0]}")
    if job.get("schema") != f"{SCHEMA_VERSION}.job":
        raise EspLabError("invalid job schema")
    safe = dict(job)
    safe.setdefault("control_plane_id", "home-edge")
    safe.setdefault("endpoint_kind", "home_edge_local_linux")
    safe.setdefault("adapter_kind", "linux_tty")
    safe.setdefault("execution_mode", "plan")
    safe.setdefault("idempotency_key", "local-plan")
    if "device_ref" not in safe and "device_path" in safe:
        safe["device_ref"] = safe["device_path"]
    _validate_public_id(safe.get("control_plane_id"), "invalid control plane id")
    _validate_public_id(safe.get("node_id"), "invalid public node id")
    _validate_public_id(safe.get("idempotency_key"), "invalid idempotency key")
    if safe.get("endpoint_kind") not in ENDPOINT_KINDS:
        raise EspLabError("unsupported endpoint kind")
    if safe.get("adapter_kind") not in ADAPTER_KINDS:
        raise EspLabError("unsupported adapter kind")
    if safe["endpoint_kind"] == "home_edge_local_linux" and safe["adapter_kind"] != "linux_tty":
        raise EspLabError("endpoint adapter mismatch")
    if safe["endpoint_kind"] == "windows_workstation_connector" and safe["adapter_kind"] != "windows_com":
        raise EspLabError("endpoint adapter mismatch")
    if safe.get("operation") not in SUPPORTED_OPERATIONS:
        raise EspLabError("unsupported operation")
    safe["device_ref"] = validate_device_ref(safe.get("device_ref"), adapter_kind=safe["adapter_kind"])
    if "device_path" in safe:
        safe["device_path"] = safe["device_ref"]
    timeout = safe.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise EspLabError("invalid timeout")
    salt = safe.get("private_salt")
    if not isinstance(salt, str) or len(salt) < 8 or len(salt) > 256:
        raise EspLabError("invalid private salt")
    if safe.get("execution_mode") not in {"plan", "read_only"}:
        raise EspLabError("invalid execution mode")
    expected_family = safe.get("expected_family")
    if expected_family is not None and expected_family not in SUPPORTED_FAMILIES:
        raise EspLabError("unsupported expected family")
    if safe["operation"] == "observe_serial_bounded":
        baud = safe.get("baud", 115200)
        if baud not in ALLOWED_BAUDS:
            raise EspLabError("unsupported baud")
        max_bytes = safe.get("max_bytes", DEFAULT_SERIAL_MAX_BYTES)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 16384:
            raise EspLabError("invalid max bytes")
    elif "baud" in safe or "max_bytes" in safe:
        raise EspLabError("baud and max_bytes are only valid for serial observation")
    return safe


def validate_device_path(device_path: object) -> str:
    return validate_device_ref(device_path, adapter_kind="linux_tty")


def validate_device_ref(device_ref: object, *, adapter_kind: str) -> str:
    if adapter_kind == "linux_tty":
        if not isinstance(device_ref, str) or not LINUX_DEVICE_RE.fullmatch(device_ref):
            raise EspLabError("invalid serial device path")
        if Path(device_ref).is_symlink():
            raise EspLabError("invalid serial device path")
        return device_ref
    if adapter_kind == "windows_com":
        return canonicalize_windows_com(device_ref)
    raise EspLabError("unsupported adapter kind")


def canonicalize_windows_com(device_ref: object) -> str:
    if not isinstance(device_ref, str) or device_ref != device_ref.strip():
        raise EspLabError("invalid serial device path")
    if any(ch in device_ref for ch in " \t\r\n/;&|<>`$"):
        raise EspLabError("invalid serial device path")
    if device_ref.startswith("\\\\") and not device_ref.startswith("\\\\.\\"):
        raise EspLabError("invalid serial device path")
    match = COM_RE.fullmatch(device_ref)
    if not match:
        raise EspLabError("invalid serial device path")
    number = int(match.group(1))
    if not 1 <= number <= 256:
        raise EspLabError("invalid serial device path")
    return f"COM{number}"


def build_read_only_commands(job: dict[str, Any], *, esptool: str | list[str] = "esptool") -> list[list[str]]:
    safe = validate_job(job)
    commands: list[list[str]] = []
    for command in READ_ONLY_COMMANDS.get(safe["operation"], ()):
        base = [esptool] if isinstance(esptool, str) else list(esptool)
        argv = [*base, "--port", safe["device_ref"], command]
        validate_command(argv, adapter_kind=safe["adapter_kind"])
        commands.append(argv)
    return commands


def validate_command(argv: list[str], *, adapter_kind: str = "linux_tty") -> list[str]:
    if not isinstance(argv, list) or not argv:
        raise EspLabError("invalid command")
    if any(not isinstance(part, str) or not part for part in argv):
        raise EspLabError("invalid command")
    for part in argv:
        lowered = _normal_command_token(part)
        if part.startswith("@") or lowered.startswith("@"):
            raise EspLabError("argument files are forbidden")
        if lowered in FORBIDDEN_TOKENS:
            raise EspLabError("forbidden read/write operation rejected")
        if any(token in lowered for token in FORBIDDEN_TOKENS):
            raise EspLabError("forbidden read/write operation rejected")
    exe_name = PureWindowsPath(argv[0]).name.lower() if "\\" in argv[0] else Path(argv[0]).name.lower()
    if exe_name in FORBIDDEN_EXE_NAMES:
        raise EspLabError("unsupported executable")
    if exe_name in {"esptool", "esptool.exe"}:
        port_index = 1
    elif exe_name in {"python", "python.exe"} and len(argv) >= 3 and argv[1:3] == ["-m", "esptool"]:
        port_index = 3
    else:
        raise EspLabError("unsupported executable")
    if len(argv) != port_index + 3 or argv[port_index] != "--port":
        raise EspLabError("unsupported esptool command shape")
    validate_device_ref(argv[port_index + 1], adapter_kind=adapter_kind)
    if argv[port_index + 2] not in {"read-mac", "flash-id"}:
        raise EspLabError("unsupported esptool command")
    return argv


def inspect_job(
    job: dict[str, Any],
    *,
    execute_read_only: bool = False,
    adapter: EspToolAdapter | None = None,
    serial_adapter: SerialObservationAdapter | None = None,
    executable_finder: Any = shutil.which,
    esptool_command: str | list[str] | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    safe = validate_job(job)
    timestamp = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    request_execute = safe["execution_mode"] == "read_only"
    may_execute = bool(execute_read_only and request_execute)
    probes: list[dict[str, Any]] = []
    raw_evidence: list[dict[str, Any]] = []
    adapter_state = "planned_not_executed"
    esptool = resolve_esptool_command(esptool_command=esptool_command, executable_finder=executable_finder)

    if safe["operation"] in READ_ONLY_COMMANDS and may_execute and esptool is None:
        adapter_state = "unsupported_dependency"
        probes.append(_probe_record("esptool", "unsupported_dependency", reason="esptool_missing"))
    elif not may_execute:
        probes.extend(_probe_record(argv[-1], "planned_not_executed", argv=argv) for argv in build_read_only_commands(safe))
        if safe["operation"] == "observe_serial_bounded":
            probes.append(_probe_record("serial_observation", "planned_not_executed"))
    elif safe["operation"] in READ_ONLY_COMMANDS:
        active_adapter = adapter or SubprocessEspToolAdapter()
        adapter_state = getattr(active_adapter, "adapter_name", "injected_esptool_adapter")
        for argv in build_read_only_commands(safe, esptool=esptool or "esptool"):
            result = active_adapter.run(
                argv,
                timeout_seconds=safe["timeout_seconds"],
                env=sanitized_child_env(),
                max_output_bytes=MAX_OUTPUT_BYTES,
            )
            probes.append(_command_probe(argv[-1], result))
            raw_evidence.append(_command_evidence(argv[-1], result))
    elif safe["operation"] == "observe_serial_bounded":
        if serial_adapter is None:
            adapter_state = "unsupported_dependency"
            probes.append(_probe_record("serial_observation", "unsupported_dependency", reason="serial_adapter_missing"))
        else:
            adapter_state = getattr(serial_adapter, "adapter_name", "injected_serial_adapter")
            result = serial_adapter.observe(
                safe["device_ref"],
                baud=safe.get("baud", 115200),
                timeout_seconds=safe["timeout_seconds"],
                max_bytes=safe.get("max_bytes", DEFAULT_SERIAL_MAX_BYTES),
            )
            probes.append(_serial_probe(result))
            raw_evidence.append(_serial_evidence(result, safe.get("max_bytes", DEFAULT_SERIAL_MAX_BYTES)))

    detected = _parse_detected_values(raw_evidence)
    fingerprint = device_fingerprint(
        private_salt=safe["private_salt"],
        evidence={
            "node_id": safe["node_id"],
            "adapter_kind": safe["adapter_kind"],
            "device_ref": safe["device_ref"],
            "family": detected.get("family"),
            "flash_manufacturer_id": detected.get("flash_manufacturer_id"),
            "flash_device_id": detected.get("flash_device_id"),
            "operation": safe["operation"],
            "mac_observed": detected.get("mac_observed", False),
        },
    )
    observation = {
        "schema": f"{SCHEMA_VERSION}.observation",
        "generated_at": timestamp,
        "control_plane_id": safe["control_plane_id"],
        "node_id": safe["node_id"],
        "endpoint_kind": safe["endpoint_kind"],
        "adapter_kind": safe["adapter_kind"],
        "operation": safe["operation"],
        "execution_mode": safe["execution_mode"],
        "adapter": adapter_state,
        "adapter_version": _adapter_version(adapter, serial_adapter),
        "detected": {
            "family": normalize_family(detected.get("family")),
            "revision": detected.get("revision"),
            "flash_manufacturer_id": detected.get("flash_manufacturer_id"),
            "flash_device_id": detected.get("flash_device_id"),
            "flash_size": detected.get("flash_size"),
            "mac_observed": bool(detected.get("mac_observed")),
        },
        "private_device_metadata": {"device_ref": safe["device_ref"]},
        "salted_device_fingerprint": fingerprint,
        "raw_bounded_evidence": raw_evidence,
        "probes": probes,
        "capability_matrix": capability_matrix(endpoint_kind=safe["endpoint_kind"], adapter_kind=safe["adapter_kind"]),
    }
    receipt = build_public_receipt(observation)
    return observation, receipt


def build_public_receipt(observation: dict[str, Any]) -> dict[str, Any]:
    detected = observation.get("detected", {})
    probes = observation.get("probes", [])
    statuses = [probe.get("status") for probe in probes]
    if any(status in {"failed", "timeout", "rejected"} for status in statuses):
        aggregate = "FAIL"
    elif any(status in {"unsupported_dependency", "planned_not_executed"} for status in statuses):
        aggregate = "CAUTION"
    else:
        aggregate = "PASS"
    counts: dict[str, int] = {}
    for item in observation.get("capability_matrix", []):
        state = str(item.get("state"))
        counts[state] = counts.get(state, 0) + 1
    risk_flags = sorted(
        {
            str(probe.get("reason"))
            for probe in probes
            if probe.get("reason") and probe.get("status") != "observed"
        }
    )
    return {
        "aggregate": aggregate,
        "capability_state_counts": dict(sorted(counts.items())),
        "detected_family": detected.get("family"),
        "endpoint_kind": observation.get("endpoint_kind"),
        "flash_size_class": _flash_size_class(detected.get("flash_size")),
        "node_id": _public_node_id(observation.get("node_id")),
        "operation": observation.get("operation"),
        "risk_flags": risk_flags,
        "schema": f"{SCHEMA_VERSION}.receipt",
    }


def resolve_esptool_command(*, esptool_command: str | list[str] | None = None, executable_finder: Any = shutil.which) -> list[str] | str | None:
    if isinstance(esptool_command, list):
        if esptool_command[:2] == ["python.exe", "-m"] or esptool_command[:2] == ["python", "-m"]:
            validate_command([*esptool_command, "--port", "COM1", "read-mac"], adapter_kind="windows_com")
        else:
            validate_command([*esptool_command, "--port", "/dev/ttyUSB0", "read-mac"])
        return list(esptool_command)
    if isinstance(esptool_command, str):
        name = PureWindowsPath(esptool_command).name.lower() if "\\" in esptool_command else Path(esptool_command).name.lower()
        if name not in {"esptool", "esptool.exe", "python", "python.exe"}:
            raise EspLabError("unsupported executable")
        return [esptool_command, "-m", "esptool"] if name in {"python", "python.exe"} else esptool_command
    found = executable_finder("esptool")
    return found


def validate_output_target(path: str | Path, *, allowed_roots: list[str | Path]) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = Path.cwd() / target
    resolved_roots = [Path(root).resolve() for root in allowed_roots]
    parent = target.parent.resolve()
    if not any(parent == root or parent.is_relative_to(root) for root in resolved_roots):
        raise EspLabError("output path outside allowed roots")
    if any(part == ".." for part in target.parts):
        raise EspLabError("unsafe output path")
    if target.is_symlink():
        raise EspLabError("output target is not a regular private file")
    if target.exists():
        st = target.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise EspLabError("output target is not a regular private file")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise EspLabError("output target is not private-mode")
    if target.parent.exists() and target.parent.is_symlink():
        raise EspLabError("output parent is a symlink")
    return target


def write_json_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def sanitized_child_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "")}


def normalize_family(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
    if normalized in {"esp8266", "esp8266ex"}:
        return "esp8266-limited"
    if normalized in SUPPORTED_FAMILIES:
        return normalized
    for family in SUPPORTED_FAMILIES:
        if family != "esp8266-limited" and family in normalized:
            return family
    return None


def device_fingerprint(*, private_salt: str, evidence: dict[str, Any]) -> str:
    normalized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{private_salt}\0{normalized}".encode("utf-8")).hexdigest()


def capability_matrix(*, endpoint_kind: str = "home_edge_local_linux", adapter_kind: str = "linux_tty") -> list[dict[str, str]]:
    return [
        {"capability": "discover_serial_candidates", "state": "supported"},
        {"capability": "identify_chip", "state": "supported"},
        {"capability": "inspect_flash_identity", "state": "supported"},
        {"capability": "observe_serial_bounded", "state": "supported"},
        {"capability": endpoint_kind, "state": "supported"},
        {"capability": adapter_kind, "state": "supported"},
        {"capability": "esp8266", "state": "limited"},
        {"capability": "partition_table_reads", "state": "deferred"},
        {"capability": "ota_metadata", "state": "deferred"},
        {"capability": "nvs_inspection", "state": "deferred"},
        {"capability": "filesystem_extraction", "state": "deferred"},
        {"capability": "flash_backup", "state": "unavailable"},
        {"capability": "firmware_flashing", "state": "unavailable"},
        {"capability": "automatic_endpoint_failover", "state": "unavailable"},
    ]


def load_job(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EspLabError("job must be a JSON object")
    return data


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Home Edge ESP Lab read-only helper")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--sysfs-root", required=True)
    validate = sub.add_parser("validate-job")
    validate.add_argument("--job", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--job", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--job", required=True)
    inspect.add_argument("--private-out", required=True)
    inspect.add_argument("--receipt-out", required=True)
    inspect.add_argument("--allowed-root", action="append", default=None)
    inspect.add_argument("--execute-read-only", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "discover":
        print(json.dumps(discover_serial_candidates(args.sysfs_root), sort_keys=True))
        return 0
    if args.command == "validate-job":
        print(json.dumps(validate_job(load_job(args.job)), sort_keys=True))
        return 0
    if args.command == "plan":
        job = validate_job(load_job(args.job))
        print(json.dumps({"commands": build_read_only_commands(job), "execute": False}, sort_keys=True))
        return 0
    job = load_job(args.job)
    roots = [Path(root) for root in (args.allowed_root or [Path(args.private_out).parent, Path(args.receipt_out).parent])]
    private_out = validate_output_target(args.private_out, allowed_roots=roots)
    receipt_out = validate_output_target(args.receipt_out, allowed_roots=roots)
    observation, receipt = inspect_job(job, execute_read_only=args.execute_read_only)
    write_json_private(private_out, observation)
    write_json_private(receipt_out, receipt)
    print(json.dumps({"private_out": str(private_out), "receipt_out": str(receipt_out), "status": receipt["aggregate"]}, sort_keys=True))
    return 0


def _read_usb_metadata(entry: Path) -> dict[str, str | None]:
    values: dict[str, str | None] = {"driver": None, "product": None, "vid": None, "pid": None}
    for parent in (entry, *entry.parents):
        if parent == parent.parent:
            break
        for key, filename in (("product", "product"), ("vid", "idVendor"), ("pid", "idProduct")):
            value = _safe_read(parent / filename)
            if value and (key != "product" or SAFE_PRODUCT_RE.fullmatch(value)):
                values[key] = value
        driver = parent / "driver"
        if driver.exists():
            values["driver"] = driver.name if not driver.is_symlink() else driver.resolve().name
        if all(values.values()):
            break
    return values


def _safe_read(path: Path) -> str | None:
    try:
        if path.is_file() and not path.is_symlink():
            return path.read_text(encoding="utf-8", errors="replace").strip()[:128]
    except OSError:
        return None
    return None


def _validate_public_id(value: object, message: str) -> None:
    if not isinstance(value, str) or not PUBLIC_NODE_RE.fullmatch(value):
        raise EspLabError(message)


def _normal_command_token(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _bound_bytes(value: bytes, limit: int) -> bytes:
    return value[:limit]


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


def _probe_record(name: str, status: str, *, argv: list[str] | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "argv": _public_command_shape(argv) if argv else None,
        "duration_ms": 0,
        "exit_code": None,
        "name": name,
        "reason": reason,
        "status": status,
    }


def _command_probe(name: str, result: CommandResult) -> dict[str, Any]:
    return {
        "duration_ms": result.duration_ms,
        "exit_code": result.exit_code,
        "name": name,
        "reason": result.reason,
        "status": result.status,
    }


def _serial_probe(result: SerialObservationResult) -> dict[str, Any]:
    data = _bound_bytes(result.data, DEFAULT_SERIAL_MAX_BYTES)
    text = data.decode("utf-8", errors="replace")
    return {
        "byte_count": len(data),
        "decoder_status": "lossy" if "\ufffd" in text else "ok",
        "duration_ms": result.duration_ms,
        "exit_code": None,
        "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        "name": "serial_observation",
        "reason": result.reason,
        "status": result.status,
        "terminal_status": result.terminal_status,
        "truncated": len(result.data) > len(data),
    }


def _command_evidence(name: str, result: CommandResult) -> dict[str, Any]:
    bounded_stdout = _bound_bytes(result.stdout, MAX_OUTPUT_BYTES)
    bounded_stderr = _bound_bytes(result.stderr, MAX_OUTPUT_BYTES)
    stdout = bounded_stdout.decode("utf-8", errors="replace")
    stderr = bounded_stderr.decode("utf-8", errors="replace")
    return {
        "mac_observed": bool(MAC_RE.search(stdout) or BARE_MAC_RE.search(stdout)),
        "probe": name,
        "stderr": _redact_sensitive(stderr),
        "stderr_truncated": len(result.stderr) > MAX_OUTPUT_BYTES,
        "stdout": _redact_sensitive(stdout),
        "stdout_truncated": len(result.stdout) > MAX_OUTPUT_BYTES,
    }


def _serial_evidence(result: SerialObservationResult, max_bytes: int) -> dict[str, Any]:
    data = _bound_bytes(result.data, max_bytes)
    text = data.decode("utf-8", errors="replace")
    return {
        "byte_count": len(data),
        "data": _redact_sensitive(text),
        "decoder_status": "lossy" if "\ufffd" in text else "ok",
        "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        "probe": "serial_observation",
        "terminal_status": result.terminal_status,
        "truncated": len(result.data) > len(data),
    }


def _redact_sensitive(text: str) -> str:
    text = MAC_RE.sub("[REDACTED_MAC]", text)
    text = BARE_MAC_RE.sub("[REDACTED_MAC]", text)
    text = IP_RE.sub("[REDACTED_IP]", text)
    text = VID_PID_PAIR_RE.sub("[REDACTED_USB_IDS]", text)
    return text


def _parse_detected_values(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    combined = "\n".join(str(item.get("stdout", "")) for item in evidence)
    detected: dict[str, Any] = {}
    chip_match = re.search(r"Chip is ([A-Za-z0-9 -]+)(?: .*?revision ([0-9.]+))?", combined, re.IGNORECASE)
    if chip_match:
        detected["family"] = chip_match.group(1)
        detected["revision"] = chip_match.group(2)
    flash_match = re.search(r"Manufacturer:\s*([0-9a-fA-Fx]+).*?Device:\s*([0-9a-fA-Fx]+)", combined, re.IGNORECASE | re.DOTALL)
    if flash_match:
        detected["flash_manufacturer_id"] = flash_match.group(1).lower()
        detected["flash_device_id"] = flash_match.group(2).lower()
    size_match = re.search(r"Detected flash size:\s*([0-9]+\s*(?:KB|MB))", combined, re.IGNORECASE)
    if size_match:
        detected["flash_size"] = size_match.group(1).upper().replace(" ", "")
    detected["mac_observed"] = any(item.get("mac_observed") is True for item in evidence)
    return detected


def _adapter_version(adapter: EspToolAdapter | None, serial_adapter: SerialObservationAdapter | None) -> str | None:
    active = adapter or serial_adapter
    if active is None:
        return None
    version = getattr(active, "adapter_version", None)
    return str(version) if version else None


def _public_command_shape(argv: list[str] | None) -> list[str] | None:
    if not argv:
        return None
    return [PureWindowsPath(argv[0]).name if "\\" in argv[0] else Path(argv[0]).name, "--port", "[REDACTED_DEVICE]", argv[-1]]


def _flash_size_class(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.upper()
    if normalized.endswith("KB"):
        return "sub_1mb"
    match = re.match(r"([0-9]+)MB", normalized)
    if not match:
        return None
    size = int(match.group(1))
    if size <= 4:
        return "up_to_4mb"
    if size <= 16:
        return "up_to_16mb"
    return "over_16mb"


def _public_node_id(value: object) -> str:
    if not isinstance(value, str):
        return "unknown-node"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

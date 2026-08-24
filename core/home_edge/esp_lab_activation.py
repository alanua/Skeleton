from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from core.home_edge.esp_lab import (
    EspLabError,
    build_public_receipt,
    inspect_job,
    validate_job,
    validate_output_target,
    write_json_private,
)
from core.home_edge.esp_lab_connector import controller_dispatch, load_secret_file


ACTIVATION_SCHEMA = "skeleton.home_edge.esp_lab.activation.v2"
TASK_ID = "home_edge_esp_lab_stage1_activation_v2"
DEFAULT_JOB_SCHEMA = "skeleton.home_edge.esp_lab.v1.job"
DEFAULT_TIMEOUT_SECONDS = 5
PUBLIC_SAFE_LINE_RE = re.compile(r"^[A-Za-z0-9_.:-]+=[A-Za-z0-9_.:,+/-]+$")
PRIVATE_LINE_MARKERS = (
    "/dev/",
    "COM",
    "secret",
    "private_salt",
    "raw_bounded_evidence",
    "connector_url",
    "connector_secret_file",
)


class EspLabActivationError(ValueError):
    """Raised for invalid Stage 1 activation packets."""


def task_packet_from_issue_body(body: str) -> dict[str, Any]:
    fenced = _first_fenced_json(body)
    if fenced is not None:
        return validate_activation_packet(fenced)
    fields = _parse_issue_fields(body)
    packet = {
        "schema": ACTIVATION_SCHEMA,
        "operation_id": fields.get("Maintenance Task ID", TASK_ID),
        "node_id": fields.get("Node ID"),
        "endpoint_kind": fields.get("Endpoint Kind", "home_edge_local_linux"),
        "adapter_kind": fields.get("Adapter Kind", "linux_tty"),
        "operation": fields.get("Operation", "identify_chip"),
        "device_ref": fields.get("Device Ref") or fields.get("Device Path"),
        "execution_mode": fields.get("Execution Mode", "plan"),
        "timeout_seconds": int(fields.get("Timeout Seconds", str(DEFAULT_TIMEOUT_SECONDS))),
        "idempotency_key": fields.get("Idempotency Key"),
        "private_salt": fields.get("Private Salt"),
    }
    for source, target in (
        ("Connector URL", "connector_url"),
        ("Connector Secret File", "connector_secret_file"),
        ("Connector CA Cert", "connector_ca_cert"),
        ("Connector Pinned Cert SHA256", "connector_pinned_cert_sha256"),
        ("Expected Family", "expected_family"),
    ):
        if source in fields:
            packet[target] = fields[source]
    return validate_activation_packet(packet)


def validate_activation_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema",
        "operation_id",
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
        "connector_url",
        "connector_secret_file",
        "connector_ca_cert",
        "connector_pinned_cert_sha256",
        "private_out",
        "receipt_out",
    }
    unknown = set(packet) - allowed
    if unknown:
        raise EspLabActivationError(f"unknown_activation_field:{sorted(unknown)[0]}")
    if packet.get("schema") != ACTIVATION_SCHEMA:
        raise EspLabActivationError("invalid_activation_schema")
    if packet.get("operation_id") != TASK_ID:
        raise EspLabActivationError("invalid_operation_id")
    job = _job_from_packet(packet)
    safe_job = validate_job(job)
    safe = dict(packet)
    safe.update(
        {
            "operation_id": TASK_ID,
            "control_plane_id": safe_job["control_plane_id"],
            "node_id": safe_job["node_id"],
            "endpoint_kind": safe_job["endpoint_kind"],
            "adapter_kind": safe_job["adapter_kind"],
            "operation": safe_job["operation"],
            "device_ref": safe_job["device_ref"],
            "timeout_seconds": safe_job["timeout_seconds"],
            "idempotency_key": safe_job["idempotency_key"],
            "execution_mode": safe_job["execution_mode"],
            "private_salt": safe_job["private_salt"],
        }
    )
    if "device_path" in safe:
        safe["device_path"] = safe_job["device_ref"]
    if safe_job["endpoint_kind"] == "windows_workstation_connector":
        if bool(safe.get("connector_ca_cert")) == bool(safe.get("connector_pinned_cert_sha256")):
            raise EspLabActivationError("exactly_one_tls_verification_method_required")
        for key in ("connector_url", "connector_secret_file"):
            if not isinstance(safe.get(key), str) or not str(safe[key]).strip():
                raise EspLabActivationError(f"{key}_required")
    return safe


def build_stage1_job(packet: Mapping[str, Any]) -> dict[str, Any]:
    return _job_from_packet(validate_activation_packet(packet))


def execute_activation_packet(
    packet: Mapping[str, Any],
    *,
    execute_read_only: bool = True,
    dispatch: Callable[..., dict[str, Any]] = controller_dispatch,
    secret_loader: Callable[[str | Path], bytes] = load_secret_file,
    inspect: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = inspect_job,
    generated_at: str | None = None,
) -> dict[str, Any]:
    safe = validate_activation_packet(packet)
    job = build_stage1_job(safe)
    if job["endpoint_kind"] == "windows_workstation_connector":
        result = dispatch(
            url=str(safe["connector_url"]),
            ca_cert=safe.get("connector_ca_cert"),
            pinned_cert_sha256=safe.get("connector_pinned_cert_sha256"),
            secret=secret_loader(str(safe["connector_secret_file"])),
            job=_connector_job(job),
            timeout_seconds=job["timeout_seconds"],
        )
        observation = result["observation"]
        receipt = result["receipt"]
        dispatch_proof = "signed_windows_connector"
    else:
        observation, receipt = inspect(
            job,
            execute_read_only=execute_read_only,
            generated_at=generated_at,
        )
        dispatch_proof = "local_controller"
    public_receipt = build_public_receipt(observation)
    if public_receipt != receipt:
        raise EspLabActivationError("receipt_mismatch")
    return {
        "schema": f"{ACTIVATION_SCHEMA}.result",
        "operation_id": TASK_ID,
        "generated_at_epoch": int(time.time()),
        "dispatch_proof": dispatch_proof,
        "observation": observation,
        "receipt": public_receipt,
    }


def receipt_status_lines(result: Mapping[str, Any]) -> list[str]:
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise EspLabActivationError("receipt_missing")
    counts = receipt.get("capability_state_counts")
    state_counts = counts if isinstance(counts, Mapping) else {}
    lines = [
        f"operation_id={TASK_ID}",
        "controller_schema=esp_lab_activation_v2",
        f"dispatch_proof={result.get('dispatch_proof', 'unknown')}",
        f"aggregate={receipt.get('aggregate', 'UNKNOWN')}",
        f"endpoint_kind={receipt.get('endpoint_kind', 'unknown')}",
        f"operation={receipt.get('operation', 'unknown')}",
        f"detected_family={receipt.get('detected_family') or 'unknown'}",
        f"supported_capability_count={int(state_counts.get('supported', 0))}",
        f"limited_capability_count={int(state_counts.get('limited', 0))}",
        f"deferred_capability_count={int(state_counts.get('deferred', 0))}",
        f"unavailable_capability_count={int(state_counts.get('unavailable', 0))}",
        f"risk_flags={_public_join(receipt.get('risk_flags', []))}",
        "private_device_evidence=private_runtime_artifact_only",
        "destructive_esp_operation=not_permitted",
    ]
    _assert_public_safe_lines(lines)
    return lines


def success_criteria_met(result: Mapping[str, Any]) -> bool:
    receipt = result.get("receipt")
    return isinstance(receipt, Mapping) and receipt.get("aggregate") in {"PASS", "CAUTION"}


def execute_stage1_activation_task(
    body: str,
    *,
    maintenance_report: Callable[[str, str, list[str], str], str],
    execute_read_only: bool = True,
) -> str:
    try:
        packet = task_packet_from_issue_body(body)
        result = execute_activation_packet(packet, execute_read_only=execute_read_only)
        success = success_criteria_met(result)
        return maintenance_report(
            "DONE" if success else "BLOCKED",
            TASK_ID,
            receipt_status_lines(result),
            "met" if success else "not_met",
        )
    except Exception as exc:  # noqa: BLE001 - runner public reports must fail closed.
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [f"reason={_public_reason(exc)}", "esp_lab_activation_status=blocked"],
            "not_met",
        )


def write_result_artifacts(
    result: Mapping[str, Any],
    *,
    private_out: str | Path,
    receipt_out: str | Path,
    allowed_roots: list[str | Path],
) -> None:
    observation = result.get("observation")
    receipt = result.get("receipt")
    if not isinstance(observation, dict) or not isinstance(receipt, dict):
        raise EspLabActivationError("invalid_result")
    write_json_private(validate_output_target(private_out, allowed_roots=allowed_roots), observation)
    write_json_private(validate_output_target(receipt_out, allowed_roots=allowed_roots), receipt)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Home Edge ESP Lab Stage 1 activation controller")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-packet")
    validate.add_argument("--packet", required=True)
    run = sub.add_parser("run")
    run.add_argument("--packet", required=True)
    run.add_argument("--private-out", required=True)
    run.add_argument("--receipt-out", required=True)
    run.add_argument("--allowed-root", action="append", default=None)
    run.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    if args.command == "validate-packet":
        print(json.dumps(validate_activation_packet(packet), sort_keys=True))
        return 0
    result = execute_activation_packet(packet, execute_read_only=not args.plan_only)
    roots = [Path(root) for root in (args.allowed_root or [Path(args.private_out).parent, Path(args.receipt_out).parent])]
    write_result_artifacts(result, private_out=args.private_out, receipt_out=args.receipt_out, allowed_roots=roots)
    print(json.dumps({"status": result["receipt"]["aggregate"], "dispatch_proof": result["dispatch_proof"]}, sort_keys=True))
    return 0


def _job_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    job = {
        "schema": DEFAULT_JOB_SCHEMA,
        "control_plane_id": packet.get("control_plane_id", "home-edge"),
        "node_id": packet.get("node_id"),
        "endpoint_kind": packet.get("endpoint_kind", "home_edge_local_linux"),
        "adapter_kind": packet.get("adapter_kind", "linux_tty"),
        "operation": packet.get("operation", "identify_chip"),
        "device_ref": packet.get("device_ref", packet.get("device_path")),
        "timeout_seconds": packet.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        "idempotency_key": packet.get("idempotency_key"),
        "execution_mode": packet.get("execution_mode", "plan"),
        "private_salt": packet.get("private_salt"),
    }
    for key in ("baud", "max_bytes", "expected_family"):
        if key in packet:
            job[key] = packet[key]
    return job


def _connector_job(job: Mapping[str, Any]) -> dict[str, Any]:
    connector_job = dict(job)
    connector_job["schema"] = "skeleton.home_edge.esp_lab.connector.v1.job"
    return connector_job


def _first_fenced_json(body: str) -> dict[str, Any] | None:
    match = re.search(r"```(?:json|task)?\s*(\{.*?\})\s*```", body, re.DOTALL)
    if not match:
        return None
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise EspLabActivationError("task_packet_must_be_object")
    return data


def _parse_issue_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def _public_join(value: object) -> str:
    if not isinstance(value, list):
        return "none"
    safe = [str(item).replace("=", "_").replace(",", "_") for item in value if str(item)]
    return ",".join(sorted(safe)) or "none"


def _assert_public_safe_lines(lines: list[str]) -> None:
    text = "\n".join(lines)
    if any(marker in text for marker in PRIVATE_LINE_MARKERS):
        raise EspLabActivationError("private_marker_in_public_status")
    for line in lines:
        if not PUBLIC_SAFE_LINE_RE.fullmatch(line):
            raise EspLabActivationError("unsafe_public_status_line")


def _public_reason(exc: BaseException) -> str:
    if isinstance(exc, (EspLabActivationError, EspLabError)):
        return str(exc).split(":", 1)[0].replace(" ", "_")[:80] or type(exc).__name__
    return type(exc).__name__


if __name__ == "__main__":
    raise SystemExit(cli())

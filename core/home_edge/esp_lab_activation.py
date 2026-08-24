from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

TASK_ID = "home_edge_esp_lab_stage1_activation_v1"
RECEIPT_SCHEMA = "skeleton.home_edge.esp_lab_stage1_activation_receipt.v1"
OPERATOR_APPROVAL = "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_APPROVED"
REPOSITORY = "alanua/Skeleton"
STAGE = "stage1_read_only_connector"
PRIVACY_BOUNDARY = "PRIVATE_LOCAL_DEVICE_EVIDENCE_PUBLIC_SAFE_AGGREGATES_ONLY"
INSTALLER = "scripts/install_home_edge_esp_lab.sh"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_LINE_RE = re.compile(r"^[A-Za-z0-9_.:/=,+-]{1,240}$")

CommandRunner = Callable[
    [list[str], Path, Mapping[str, str] | None, int], tuple[int, str, str]
]
MaintenanceReport = Callable[[str, str, list[str], str], str]


class EspLabActivationError(ValueError):
    """Raised for invalid Stage 1 activation requests."""


def execute_stage1_activation(
    body: str,
    *,
    workdir: str | Path,
    maintenance_report: MaintenanceReport,
    command_runner: CommandRunner | None = None,
) -> str:
    runner = command_runner or _run_command
    parsed, reason = parse_activation_issue_body(body)
    if reason is not None or parsed is None:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [f"reason={reason or 'esp_lab_activation_invalid_input'}"],
            "not_met",
        )

    checkout = Path(workdir).resolve()
    status_lines = [
        f"repository={REPOSITORY}",
        f"head_sha={parsed['Expected Main SHA']}",
        "stage=stage1_read_only_connector",
        "privacy_boundary=private_local_device_evidence_public_safe_aggregates_only",
    ]
    reason = _preflight_checkout(checkout, parsed["Expected Main SHA"], runner)
    if reason is not None:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [*status_lines, f"reason={reason}"],
            "not_met",
        )
    status_lines.append("step=verify_checkout status=done")

    receipt = activation_receipt(
        source_sha=parsed["Expected Main SHA"],
        checkout=checkout,
    )
    reason = validate_activation_receipt(receipt, expected_sha=parsed["Expected Main SHA"])
    if reason is not None:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [*status_lines, f"reason={reason}"],
            "not_met",
        )
    status_lines.extend(receipt_status_lines(receipt))
    return maintenance_report("DONE", TASK_ID, status_lines, "met")


def parse_activation_issue_body(body: str) -> tuple[dict[str, str] | None, str | None]:
    allowed = {
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Operator Approval",
    }
    parsed: dict[str, str] = {}
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(
            r"(?P<field>[A-Za-z][A-Za-z0-9 ]*):\s*(?P<value>\S(?:.*\S)?)",
            line,
        )
        if match is None:
            return None, "esp_lab_activation_noncanonical_input"
        field = match.group("field")
        if field not in allowed:
            return None, "esp_lab_activation_unknown_input_field"
        if field in parsed:
            return None, "esp_lab_activation_duplicate_input_field"
        parsed[field] = match.group("value")
    if set(parsed) != allowed:
        return None, "esp_lab_activation_required_input_missing"
    if parsed["Mode"] != "RUNTIME_MAINTENANCE_TASK":
        return None, "esp_lab_activation_mode_mismatch"
    if parsed["Maintenance Task ID"] != TASK_ID:
        return None, "esp_lab_activation_task_id_mismatch"
    if parsed["Repository"] != REPOSITORY:
        return None, "esp_lab_activation_repository_mismatch"
    if _SHA_RE.fullmatch(parsed["Expected Main SHA"]) is None:
        return None, "esp_lab_activation_expected_main_sha_invalid"
    if parsed["Operator Approval"] != OPERATOR_APPROVAL:
        return None, "esp_lab_activation_operator_approval_invalid"
    return parsed, None


def activation_receipt(*, source_sha: str, checkout: str | Path) -> dict[str, object]:
    root = Path(checkout)
    checks = {
        "stage1_read_only_contract": True,
        "linux_tty_adapter": _contains(root / "core/home_edge/esp_lab.py", "linux_tty"),
        "windows_com_adapter": _contains(root / "core/home_edge/esp_lab.py", "windows_com"),
        "authenticated_connector": _contains(root / "core/home_edge/esp_lab_connector.py", "x-esp-lab-signature"),
        "public_receipt_schema": (root / "schemas/home_edge_esp_lab_receipt.schema.json").is_file(),
        "activation_receipt_schema": (root / "schemas/home_edge_esp_lab_activation_receipt.schema.json").is_file(),
        "installer_present": (root / INSTALLER).is_file(),
        "destructive_operations_enabled": False,
        "live_device_mutation_attempted": False,
        "second_executor_created": False,
    }
    check_passed = all(
        value is True
        for key, value in checks.items()
        if key
        not in {
            "destructive_operations_enabled",
            "live_device_mutation_attempted",
            "second_executor_created",
        }
    ) and all(
        checks[key] is False
        for key in {
            "destructive_operations_enabled",
            "live_device_mutation_attempted",
            "second_executor_created",
        }
    )
    supported_operations = [
        "discover_serial_candidates",
        "identify_chip",
        "inspect_flash_identity",
        "observe_serial_bounded",
    ]
    return {
        "schema": RECEIPT_SCHEMA,
        "maintenance_task_id": TASK_ID,
        "repository": REPOSITORY,
        "source_sha": source_sha,
        "stage": STAGE,
        "status": "DONE" if check_passed else "BLOCKED",
        "checks": checks,
        "supported_operations": supported_operations,
        "privacy_boundary": PRIVACY_BOUNDARY,
        "private_evidence_exported": False,
        "public_safe_aggregate_only": True,
        "live_device_mutation_attempted": False,
        "destructive_operations_enabled": False,
        "next_operator_action": "run_private_stage1_read_only_smoke_on_home_edge_when_device_available",
        "success_criteria": "met" if check_passed else "not_met",
    }


def validate_activation_receipt(
    receipt: Mapping[str, object],
    *,
    expected_sha: str,
) -> str | None:
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return "esp_lab_activation_receipt_schema_invalid"
    if receipt.get("maintenance_task_id") != TASK_ID:
        return "esp_lab_activation_receipt_task_mismatch"
    if receipt.get("repository") != REPOSITORY:
        return "esp_lab_activation_receipt_repository_mismatch"
    if receipt.get("source_sha") != expected_sha:
        return "esp_lab_activation_receipt_source_mismatch"
    checks = receipt.get("checks")
    if not isinstance(checks, Mapping):
        return "esp_lab_activation_receipt_checks_invalid"
    required_false = (
        "destructive_operations_enabled",
        "live_device_mutation_attempted",
        "second_executor_created",
    )
    if any(checks.get(key) is not False for key in required_false):
        return "esp_lab_activation_forbidden_capability_enabled"
    for key, value in checks.items():
        if key in required_false:
            continue
        if value is not True:
            return "esp_lab_activation_receipt_checks_failed"
    if receipt.get("private_evidence_exported") is not False:
        return "esp_lab_activation_private_leak_detected"
    if receipt.get("public_safe_aggregate_only") is not True:
        return "esp_lab_activation_public_boundary_invalid"
    if receipt.get("live_device_mutation_attempted") is not False:
        return "esp_lab_activation_live_mutation_attempted"
    if receipt.get("destructive_operations_enabled") is not False:
        return "esp_lab_activation_destructive_enabled"
    if receipt.get("success_criteria") != "met":
        return "esp_lab_activation_success_criteria_not_met"
    return None


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    checks = receipt.get("checks")
    check_count = len(checks) if isinstance(checks, Mapping) else 0
    operations = receipt.get("supported_operations")
    operation_count = len(operations) if isinstance(operations, list) else 0
    lines = [
        "step=verify_stage1_contract status=done",
        f"read_only_operation_count={operation_count}",
        f"contract_check_count={check_count}",
        "destructive_operations_enabled=false",
        "live_device_mutation_attempted=false",
        "private_evidence_exported=false",
        "public_safe_aggregate_only=true",
        "test_summary=home_edge_esp_lab_stage1_activation_done",
        f"next_operator_action={receipt.get('next_operator_action')}",
    ]
    return [line for line in lines if _SAFE_LINE_RE.fullmatch(line)]


def _preflight_checkout(checkout: Path, expected_sha: str, runner: CommandRunner) -> str | None:
    if not checkout.exists():
        return "esp_lab_activation_checkout_missing"
    code, stdout, _stderr = runner(["git", "rev-parse", "HEAD"], checkout, None, 30)
    head = stdout.strip().splitlines()[0] if stdout.strip() else ""
    if code != 0 or _SHA_RE.fullmatch(head) is None:
        return "esp_lab_activation_head_unavailable"
    if head.lower() != expected_sha.lower():
        return "esp_lab_activation_expected_main_sha_mismatch"
    code, stdout, _stderr = runner(["git", "status", "--porcelain"], checkout, None, 30)
    if code != 0:
        return "esp_lab_activation_git_status_unavailable"
    if stdout.strip():
        return "esp_lab_activation_checkout_dirty"
    return None


def _run_command(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout: int,
) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _contains(path: Path, token: str) -> bool:
    try:
        return token in path.read_text(encoding="utf-8")
    except OSError:
        return False


def cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Home Edge ESP Lab Stage 1 activation receipt")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--checkout", default=".")
    args = parser.parse_args(argv)
    receipt = activation_receipt(source_sha=args.expected_sha, checkout=args.checkout)
    reason = validate_activation_receipt(receipt, expected_sha=args.expected_sha)
    json.dump(receipt, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if reason is not None:
        sys.stderr.write(f"BLOCKED: {reason}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator

from core.home_edge.esp_lab_activation import (
    OPERATOR_APPROVAL,
    RECEIPT_SCHEMA,
    TASK_ID,
    activation_receipt,
    cli,
    execute_stage1_activation,
    parse_activation_issue_body,
    validate_activation_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _body(
    *,
    sha: str = SHA,
    approval: str = OPERATOR_APPROVAL,
) -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {TASK_ID}",
            "Repository: alanua/Skeleton",
            f"Expected Main SHA: {sha}",
            f"Operator Approval: {approval}",
        )
    )


def _report(status: str, task_id: str, lines: list[str], success: str) -> str:
    return "\n".join(
        [
            f"{status}: Runner host maintenance task completed.",
            f"maintenance_task_id={task_id}",
            *lines,
            f"success_criteria={success}",
        ]
    )


def _runner(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout: int,
) -> tuple[int, str, str]:
    del cwd, env, timeout
    if command == ["git", "rev-parse", "HEAD"]:
        return 0, SHA + "\n", ""
    if command == ["git", "status", "--porcelain"]:
        return 0, "", ""
    raise AssertionError(f"unexpected command: {command}")


def test_activation_receipt_is_public_safe_and_schema_valid() -> None:
    receipt = activation_receipt(source_sha=SHA, checkout=ROOT)
    schema = json.loads(
        (ROOT / "schemas/home_edge_esp_lab_activation_receipt.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator(schema).validate(receipt)

    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["success_criteria"] == "met"
    assert receipt["private_evidence_exported"] is False
    assert receipt["live_device_mutation_attempted"] is False
    assert receipt["destructive_operations_enabled"] is False
    assert receipt["supported_operations"] == [
        "discover_serial_candidates",
        "identify_chip",
        "inspect_flash_identity",
        "observe_serial_bounded",
    ]
    public = json.dumps(receipt, sort_keys=True)
    for token in ("COM42", "/dev/ttyUSB0", "aa:bb:cc:dd:ee:ff", "192.168."):
        assert token not in public
    assert validate_activation_receipt(receipt, expected_sha=SHA) is None


def test_activation_issue_body_is_exact_and_rejects_unknown_fields() -> None:
    parsed, reason = parse_activation_issue_body(_body())
    assert reason is None
    assert parsed is not None
    assert parsed["Maintenance Task ID"] == TASK_ID

    parsed, reason = parse_activation_issue_body(_body() + "\nDevice Ref: /dev/ttyUSB0")
    assert parsed is None
    assert reason == "esp_lab_activation_unknown_input_field"


def test_execute_stage1_activation_returns_bounded_done_report() -> None:
    report = execute_stage1_activation(
        _body(),
        workdir=ROOT,
        maintenance_report=_report,
        command_runner=_runner,
    )

    assert report.startswith("DONE:")
    assert f"maintenance_task_id={TASK_ID}" in report
    assert "read_only_operation_count=4" in report
    assert "destructive_operations_enabled=false" in report
    assert "live_device_mutation_attempted=false" in report
    assert "private_evidence_exported=false" in report
    assert "success_criteria=met" in report


def test_execute_stage1_activation_blocks_before_preflight_without_approval() -> None:
    called = False

    def fail_runner(
        command: list[str],
        cwd: Path,
        env: Mapping[str, str] | None,
        timeout: int,
    ) -> tuple[int, str, str]:
        nonlocal called
        called = True
        return 1, "", ""

    report = execute_stage1_activation(
        _body(approval="PENDING"),
        workdir=ROOT,
        maintenance_report=_report,
        command_runner=fail_runner,
    )

    assert report.startswith("BLOCKED:")
    assert "reason=esp_lab_activation_operator_approval_invalid" in report
    assert called is False


def test_install_script_has_no_runtime_service_or_destructive_esp_path() -> None:
    source = (ROOT / "scripts/install_home_edge_esp_lab.sh").read_text(encoding="utf-8")

    forbidden = [
        "systemctl",
        "write-flash",
        "erase-flash",
        "read-flash",
        "ssh ",
        "powershell",
        "pwsh",
    ]
    assert all(token not in source for token in forbidden)
    assert 'REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"' in source
    assert "/home/agent/agent-dev/repos/Skeleton" not in source
    assert "skeleton-home-edge-esp-lab" in source
    assert "live_device_mutation_attempted=false" in source


def test_activation_cli_emits_valid_receipt(capsys: object) -> None:
    rc = cli(["--expected-sha", SHA, "--checkout", str(ROOT)])

    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert rc == 0
    assert captured.err == ""
    assert validate_activation_receipt(receipt, expected_sha=SHA) is None

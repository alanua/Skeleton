from __future__ import annotations

import json

import pytest

from core.home_edge import family_document_production as production
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV


SHA = "6" * 40


def body(**overrides: str) -> str:
    values = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": production.TASK_ID,
        "Repository": production.REPOSITORY,
        "Expected Main SHA": SHA,
        "Operator Approval": production.OPERATOR_APPROVAL,
        "Target": production.TARGET_NODE,
    }
    values.update(overrides)
    return "\n".join(f"{key}: {value}" for key, value in values.items())


def test_runtime_input_is_exact_and_unknown_behavior_field_rejected() -> None:
    parsed = production.parse_runtime_input(body())
    assert parsed.expected_main_sha == SHA
    with pytest.raises(ValueError, match="unknown_runtime_input_field"):
        production.parse_runtime_input(body() + "\nCommand: rm -rf /")
    with pytest.raises(ValueError, match="target_mismatch"):
        production.parse_runtime_input(body(Target="other-node"))


def test_main_sha_must_match_registered_and_github() -> None:
    production.validate_main_sha(SHA, registered_clean_main_sha=SHA, github_main_sha=SHA)
    with pytest.raises(ValueError, match="registered_clean_main_sha_mismatch"):
        production.validate_main_sha(SHA, registered_clean_main_sha="7" * 40, github_main_sha=SHA)
    with pytest.raises(ValueError, match="github_main_sha_mismatch"):
        production.validate_main_sha(SHA, registered_clean_main_sha=SHA, github_main_sha="7" * 40)


def test_request_is_fixed_signed_privileged_home_edge_script() -> None:
    request = production.build_activation_request(
        SHA,
        environment={EXEC_HMAC_SECRET_ENV: "synthetic-hmac-secret-value"},
    )
    value = request.to_mapping()
    assert value["node_id"] == "home-edge-01"
    assert value["execution_lane"] == "privileged_mutation"
    assert value["run_as"] == "root"
    assert value["idempotency_key"] == production.IDEMPOTENCY_KEY
    assert value["operator_approval_ref"] == production.OPERATOR_APPROVAL
    assert value["signature"]
    script = value["script"]
    assert SHA in script
    assert "https://github.com/alanua/Skeleton.git" in script
    assert "/opt/skeleton/family-document/current" in script
    assert "/home/agent/agent-dev/Skeleton" not in script
    assert "pdftotext" in script and "ocrmypdf" in script and "tesseract" in script
    assert "SKELETON_TG_BOT" in script and "SKELETON_TG_CHAT" in script


def test_public_receipt_accepts_awaiting_scan_without_fake_success() -> None:
    receipt = {
        "maintenance_task_id": production.TASK_ID,
        "deployment_status": "healthy",
        "config_ready": True,
        "dependencies_ready": True,
        "exact_sha_verified": True,
        "service_active": True,
        "single_worker": True,
        "canary_state": "awaiting_physical_scan",
        "live_canary_success": False,
        "accepted_delta": 0,
        "work_done_delta": 0,
        "report_done_delta": 0,
        "archive_readback": False,
        "memorygateway_readback": False,
        "telegram_report_done": False,
        "report_is_rich": False,
        "duplicate_replay_zero": False,
        "retrying_count": 0,
        "review_count": 0,
        "stable_reason": "awaiting_physical_scan",
        "success_criteria": "not_met",
    }
    sanitized = production.sanitize_public_receipt(receipt)
    assert sanitized["service_active"] is True
    assert production.success_criteria_met(sanitized) is False


def test_public_receipt_requires_all_live_canary_evidence_for_success() -> None:
    receipt = {
        "maintenance_task_id": production.TASK_ID,
        "deployment_status": "healthy",
        "config_ready": True,
        "dependencies_ready": True,
        "exact_sha_verified": True,
        "service_active": True,
        "single_worker": True,
        "canary_state": "passed",
        "live_canary_success": True,
        "accepted_delta": 1,
        "work_done_delta": 1,
        "report_done_delta": 1,
        "archive_readback": True,
        "memorygateway_readback": True,
        "telegram_report_done": True,
        "report_is_rich": True,
        "duplicate_replay_zero": True,
        "retrying_count": 0,
        "review_count": 0,
        "stable_reason": "canary_passed",
        "success_criteria": "met",
    }
    assert production.success_criteria_met(production.sanitize_public_receipt(receipt)) is True
    receipt["duplicate_replay_zero"] = False
    assert production.success_criteria_met(production.sanitize_public_receipt(receipt)) is False


def test_public_receipt_rejects_private_path_or_extra_field() -> None:
    receipt = production.blocked_receipt("safe_reason")
    receipt["stable_reason"] = "/private/path"
    with pytest.raises(ValueError, match="receipt_field_not_public_safe"):
        production.sanitize_public_receipt(receipt)
    receipt = production.blocked_receipt("safe_reason")
    receipt["private"] = "value"
    with pytest.raises(ValueError, match="receipt_field_set_mismatch"):
        production.sanitize_public_receipt(receipt)


def test_executor_stdout_parser_returns_only_public_receipt() -> None:
    receipt = production.blocked_receipt("config_missing")
    parsed = production.public_receipt_from_executor_stdout(
        {"stdout": json.dumps(receipt), "status": "failed", "exit_code": 20}
    )
    assert parsed == receipt

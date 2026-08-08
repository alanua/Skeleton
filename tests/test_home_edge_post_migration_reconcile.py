from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.home_edge import post_migration_reconcile as reconcile
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "a" * 40
SECRET = "test-home-edge-secret"
ALLOWED_FILES = (
    "core/home_edge/post_migration_reconcile.py",
    "scripts/runner_poll_github_tasks.py",
    "schemas/home_edge_post_migration_reconcile_receipt.schema.json",
    "tests/test_home_edge_post_migration_reconcile.py",
    "tests/test_runner_poll_github_tasks.py",
    "docs/home_edge/HOME_EDGE_POST_MIGRATION_RECONCILE.md",
)


def issue_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": reconcile.TASK_ID,
        "Repository": reconcile.REPOSITORY,
        "Expected Main SHA": SHA,
        "Operator Approval": reconcile.OPERATOR_APPROVAL,
        "Target": reconcile.TARGET_NODE,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def public_receipt(**updates: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": reconcile.TASK_ID,
        "os_identity_status": "verified",
        "node_identity_status": "verified",
        "registry_doctor_status": "healthy",
        "gallery_pre_count": 47,
        "gallery_post_count": 47,
        "gallery_root_cause_class": "unavailable_upstream_asset",
        "gallery_status": "healthy",
        "brother_specialized_status": "healthy",
        "aggregate_verifier_status": "healthy",
        "stale_home_path_matches_before": 2,
        "stale_home_path_matches_after": 0,
        "cast_status": "healthy",
        "pointer_status": "healthy",
        "media_watchdog_status": "healthy",
        "failed_units_count": 0,
        "reboot_performed": False,
        "rollback_ready": True,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "f" * 64,
        "final_postcheck_receipt_hash": "e" * 64,
        "audit_receipt_hash": "0" * 64,
        "stable_reason": "completed",
        "success_criteria": "met",
    }
    receipt.update(updates)
    return receipt


def executor_receipt(stdout: str, *, request_id: str = "req") -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id=request_id,
        node_id=reconcile.TARGET_NODE,
        execution_lane="privileged_mutation",
        exit_code=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def test_exact_runtime_input_accepted_and_sha_checked() -> None:
    parsed = reconcile.parse_runtime_input(issue_body())

    assert parsed.repository == reconcile.REPOSITORY
    assert parsed.expected_main_sha == SHA
    assert parsed.operator_approval == reconcile.OPERATOR_APPROVAL
    assert parsed.target == reconcile.TARGET_NODE
    reconcile.validate_main_sha(
        SHA,
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )


@pytest.mark.parametrize(
    "body,reason",
    [
        (issue_body(**{"Expected Main SHA": "A" * 40}), "expected_main_sha_malformed"),
        (issue_body() + "\nExpected Main SHA: " + SHA, "duplicate_runtime_input_field"),
        (issue_body() + "\nCommand: rm -rf /", "unknown_runtime_input_field"),
        (issue_body() + "\nPath: /tmp/evil", "unknown_runtime_input_field"),
        (issue_body() + "\nArgv: /bin/sh", "unknown_runtime_input_field"),
        (issue_body() + "\nService: ssh.service", "unknown_runtime_input_field"),
        (issue_body() + "\nHost: other", "unknown_runtime_input_field"),
        (issue_body() + "\nScript: $(id)", "unknown_runtime_input_field"),
        (issue_body() + "\nSecret: token", "unknown_runtime_input_field"),
        (issue_body() + "\nTimeout: 1", "unknown_runtime_input_field"),
    ],
)
def test_issue_text_cannot_inject_behavior_changing_fields(body: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        reconcile.parse_runtime_input(body)


def test_expected_sha_must_equal_registered_and_github_main() -> None:
    with pytest.raises(ValueError, match="registered_clean_main_sha_mismatch"):
        reconcile.validate_main_sha(SHA, registered_clean_main_sha="b" * 40, github_main_sha=SHA)
    with pytest.raises(ValueError, match="github_main_sha_mismatch"):
        reconcile.validate_main_sha(SHA, registered_clean_main_sha=SHA, github_main_sha="b" * 40)


def test_request_is_signed_fixed_and_dispatched_only_through_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Mapping[str, Any]] = []
    monkeypatch.setenv(reconcile.EXEC_HMAC_SECRET_ENV, SECRET)

    def fake_execute(request: Mapping[str, Any]):
        calls.append(request)
        if len(calls) == 1:
            return executor_receipt(json.dumps(public_receipt()))
        return executor_receipt("", request_id="post")

    monkeypatch.setattr(reconcile, "execute_home_edge_request", fake_execute)

    receipt = reconcile.execute_post_migration_reconcile_task(
        issue_body() + "\n\n```task\n{\"ignored\":\"/tmp/injected\"}\n```",
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["success_criteria"] == "met"
    assert len(calls) == 2
    first = HomeEdgeExecRequest.from_mapping(calls[0])
    assert first.signature == sign_request(first, SECRET)
    assert first.node_id == reconcile.TARGET_NODE
    assert first.run_as.value == "root"
    assert first.execution_lane.value == "privileged_mutation"
    assert first.timeout_seconds == 900
    assert first.idempotency_key == reconcile.IDEMPOTENCY_KEY
    assert first.operator_approval_ref == reconcile.OPERATOR_APPROVAL
    assert first.script == reconcile.RECONCILE_SCRIPT
    assert "/tmp/injected" not in first.script
    assert "subprocess" not in reconcile.__dict__
    assert "OpenSSHExecTransport" not in reconcile.__dict__

    postcheck = HomeEdgeExecRequest.from_mapping(calls[1])
    assert postcheck.signature == sign_request(postcheck, SECRET)
    assert postcheck.argv == ("/usr/bin/true",)
    assert postcheck.execution_lane.value == "read_only"
    assert receipt["mutation_executor_receipt_hash"] == "f" * 64
    assert receipt["final_postcheck_receipt_hash"] == "f" * 64
    assert receipt["audit_receipt_hash"] == reconcile._audit_hash(receipt)


@pytest.mark.parametrize(
    "marker",
    [
        "os_not_debian",
        "os_version_not_13",
        "hostname_mismatch",
        "user_missing",
        "uid_mismatch",
        "home_mismatch",
        "boot_identity_unavailable",
        "signed_gateway_path_missing",
        "registry_doctor_unhealthy",
        "missing_root_execution",
    ],
)
def test_preflight_blocks_before_mutation(marker: str) -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert f"block {marker}" in script
    mutation_index = min(
        script.index("\nrepair_gallery\n"),
        script.index("\npatch_aggregate_verifier\n"),
        script.index("\nreplace_stale_paths\n"),
    )
    assert script.index(f"block {marker}") < mutation_index


def test_gallery_repair_uses_canonical_refresh_and_verifier_without_threshold_weakening() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert "/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh" in script
    assert "/home/oleksii/.local/bin/home-edge-screensaver-verify-v9" in script
    assert "gallery_pre_count" in script
    assert "gallery_post_count" in script
    assert "unavailable_upstream_asset" in script
    assert "stale_cache_state" in script
    assert "duplicate_identity" in script
    assert "broken_asset_metadata" in script
    assert "verifier_assumption" in script
    assert "acceptance_threshold" not in script
    assert "qualification_threshold" not in script


def test_aggregate_verifier_transformation_keeps_brother_v4_check_fail_closed() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert "/home/oleksii/.local/bin/home-edge-brother-scankey-verify-v4" in script
    assert "home-edge-brother-scankey-verify-v4" in script
    assert "aggregate_brother_check_missing" in script
    assert "aggregate_patch_weakened" in script
    assert "BROTHER_CHECK_DISABLED" in script
    assert "bypass.*Brother" in script
    assert "run_quiet brother_v4" in script
    assert "run_quiet aggregate_after" in script


def test_stale_home_path_scope_is_exact_and_private_areas_are_excluded() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert "STALENESS_ROOTS=(/home/oleksii/.config/skeleton" in script
    assert "/home/valertos08" in script
    assert "/home/oleksii${old#/home/valertos08}" in script
    assert '[ -e "$new" ] || ok=false' in script
    for excluded in (
        "*/.ssh/*",
        "*/credentials/*",
        "*/secrets/*",
        "*/browser/*",
        "*/Documents/*",
        "*/production-archives/*",
    ):
        assert excluded in script


def test_rollback_manifest_restores_every_touched_file_after_postcheck_failure() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert 'STATE_ROOT="/var/lib/skeleton/home-edge-01/post-migration-reconcile-v1"' in script
    assert 'install -d -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT" "$ROLLBACK_DIR"' in script
    assert 'chmod 0600 "$MANIFEST"' in script
    assert "backup_file" in script
    assert "restore_files" in script
    assert "fail_after_mutation postcheck_failed" in script
    assert "rollback_applied=true" in script
    assert 'rm -rf "$ROLLBACK_DIR"' not in script


def test_postchecks_cover_required_health_and_no_boot_change_or_power_command() -> None:
    script = reconcile.RECONCILE_SCRIPT
    lowered = script.lower()
    for token in (
        "systemctl reboot",
        "/sbin/reboot",
        "shutdown ",
        "poweroff",
        "mkfs",
        "parted",
        "iptables",
        " ufw ",
        " tailscale ",
        "ssh -",
    ):
        assert token not in lowered
    assert "boot_id_before" in script
    assert "boot_id_after" in script
    assert '"reboot_performed":false' in script
    assert "skeleton-devices doctor" in script
    assert "skeleton-cast.service" in script
    assert "home-edge-pointer-broker.service" in script
    assert "/run/home-edge-pointer-broker.sock" in script
    assert "/dev/uinput" in script
    assert "home-edge-media-watchdog-status --zero-critical --zero-warnings" in script
    assert "systemctl --failed --no-legend --plain" in script


def test_public_receipt_rejects_private_values_and_success_requires_all_postchecks() -> None:
    receipt = public_receipt(stable_reason="/home/oleksii/private")
    with pytest.raises(ValueError, match="receipt_field_not_public_safe"):
        reconcile.sanitize_public_receipt(receipt)

    sanitized = reconcile.sanitize_public_receipt(public_receipt())
    assert reconcile.success_criteria_met(sanitized) is True
    sanitized["stale_home_path_matches_after"] = 1
    assert reconcile.success_criteria_met(sanitized) is False
    lines = "\n".join(reconcile.receipt_status_lines(public_receipt()))
    assert "/home" not in lines
    assert "secret" not in lines.lower()
    assert "oleksii" not in lines


def test_allowed_text_files_have_exactly_one_newline_and_no_trailing_space() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in ALLOWED_FILES:
        data = (root / relative).read_bytes()
        assert data.endswith(b"\n"), relative
        assert not data.endswith(b"\n\n"), relative
        for line in data.splitlines():
            assert line.rstrip(b" \t") == line, relative

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge import post_migration_reconcile as reconcile
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "a" * 40
SECRET = "test-home-edge-reconcile-secret"


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
        "boot_id_unchanged": True,
        "registry_pre_status": "healthy",
        "screensaver_status": "already_healthy",
        "screensaver_refresh_count": 0,
        "gallery_pre_count": 48,
        "gallery_post_count": 0,
        "brother_verify_status": "healthy",
        "aggregate_verify_status": "healthy",
        "aggregate_source_repaired": False,
        "stale_operational_matches_before": 0,
        "stale_operational_matches_after": 0,
        "stale_files_changed_count": 0,
        "system_failed_units_count": 0,
        "user_failed_units_count": 0,
        "cast_status": "healthy",
        "pointer_status": "healthy",
        "watchdog_status": "healthy",
        "watchdog_critical_count": 0,
        "watchdog_warning_count": 0,
        "rollback_ready": True,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "f" * 64,
        "audit_receipt_hash": "0" * 64,
        "stable_reason": "already_healthy",
        "success_criteria": "met",
        "canonical_memory_post_step": "home_edge_audit_persist_v1",
    }
    receipt.update(updates)
    return receipt


def executor_receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id="req",
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


def test_exact_runtime_input_accepted_and_main_sha_checked() -> None:
    parsed = reconcile.parse_runtime_input(issue_body())

    assert parsed.repository == reconcile.REPOSITORY
    assert parsed.expected_main_sha == SHA
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
        (issue_body() + "\nCommand: /bin/true", "unknown_runtime_input_field"),
        (issue_body() + "\nPath: /home/oleksii/.local/bin/other", "unknown_runtime_input_field"),
        (issue_body() + "\nUnit: evil.service", "unknown_runtime_input_field"),
        (issue_body() + "\nRun As: desktop-user", "unknown_runtime_input_field"),
        (issue_body() + "\nTimeout: 1", "unknown_runtime_input_field"),
        (issue_body(**{"Operator Approval": "wrong"}), "operator_approval_mismatch"),
        (issue_body(**{"Repository": "other/repo"}), "repository_mismatch"),
        (issue_body(**{"Target": "other"}), "target_mismatch"),
    ],
)
def test_malformed_duplicate_and_behavior_changing_fields_rejected(
    body: str, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        reconcile.parse_runtime_input(body)


def test_request_is_signed_fixed_and_dispatched_only_through_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Mapping[str, Any]] = []
    monkeypatch.setenv(reconcile.EXEC_HMAC_SECRET_ENV, SECRET)

    def fake_execute(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        calls.append(request)
        return executor_receipt(json.dumps(public_receipt()))

    monkeypatch.setattr(reconcile, "execute_home_edge_request", fake_execute)

    receipt = reconcile.execute_post_migration_reconcile_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["success_criteria"] == "met"
    assert len(calls) == 1
    request = HomeEdgeExecRequest.from_mapping(calls[0])
    assert request.signature == sign_request(request, SECRET)
    assert request.node_id == reconcile.TARGET_NODE
    assert request.run_as.value == "root"
    assert request.execution_lane.value == "privileged_mutation"
    assert request.timeout_seconds == 900
    assert request.idempotency_key == reconcile.IDEMPOTENCY_KEY
    assert request.operator_approval_ref == reconcile.OPERATOR_APPROVAL
    assert request.script == reconcile.RECONCILE_SCRIPT
    assert receipt["mutation_executor_receipt_hash"] == "f" * 64
    assert receipt["audit_receipt_hash"] == reconcile._audit_hash(receipt)


def test_exact_verified_live_entrypoints_and_rejected_invented_names() -> None:
    script = reconcile.RECONCILE_SCRIPT
    for path in reconcile.VERIFIED_ENTRYPOINTS.values():
        assert path in script
    assert '"$GALLERY_VERIFY"' in script
    assert '"$GALLERY_VERIFY" --json' not in script
    for invented in reconcile.INVENTED_ENTRYPOINTS:
        assert invented not in script
    assert "/home/oleksii/.local/bin/skeleton-cast-control" in script
    assert '"$CAST_STATUS" status' in script
    assert '"$WATCHDOG_STATUS" status' in script


def test_user_helpers_use_fixed_uid_1000_session_environment_not_root_user_systemctl() -> None:
    script = reconcile.RECONCILE_SCRIPT
    expected = (
        "runuser -u oleksii -- env HOME=/home/oleksii "
        "XDG_RUNTIME_DIR=/run/user/1000 "
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
    )
    assert expected in script
    assert "desktop_run systemctl --user --failed" in script
    assert "systemctl --user -m" not in script
    assert "systemctl status skeleton-cast" not in script


def test_gallery_repair_contract_is_default_verify_refresh_once_and_fail_closed() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert 'if desktop_run "$GALLERY_VERIFY" >"$pre_gallery_log" 2>&1; then' in script
    assert 'screensaver_status="already_healthy"' in script
    assert 'run_private gallery_refresh desktop_run "$GALLERY_REFRESH"' in script
    assert "screensaver_refresh_count=1" in script
    assert "block screensaver_postcheck_degraded" in script
    assert "--json" not in script
    assert re.search(r"(>=|>|=)\s*[\"']?4[78][\"']?", script) is None
    assert "gallery cache" not in script.lower()
    assert "qualified_items" in script


def test_aggregate_source_repair_is_exact_literal_guarded_and_rollback_verified() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert 'OLD_BROTHER_UNIT="brother_guard_v2.service"' in script
    assert 'NEW_BROTHER_UNIT="brother-guard.service"' in script
    assert 'grep -Fao "$OLD_BROTHER_UNIT" -- "$AGGREGATE_VERIFY"' in script
    assert '[ "$matches" = "1" ] || block aggregate_old_unit_literal_count_mismatch' in script
    assert 'desktop_run systemctl --user is-active --quiet "$NEW_BROTHER_UNIT"' in script
    assert "brother_guard_target_unit_unverified" in script
    assert 'text.count(old) != 1' in script
    assert "text.replace(old, new)" in script
    assert "restore_owned_files || block rollback_failed" in script
    assert "aggregate_repair_rollback_unverified" in script
    assert "sed -i" not in script
    assert "home-edge-platform-verify" not in script


def test_stale_path_scope_privacy_exclusions_nul_safety_and_atomic_replacement() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert "find /home/oleksii/.local/bin -maxdepth 1 -type f" in script
    assert "-name 'home-edge-*'" in script
    assert "-name 'skeleton-*'" in script
    assert "find /home/oleksii/.config/systemd/user -maxdepth 1 -type f" in script
    assert "find /etc/systemd/system -maxdepth 1 -type f" in script
    assert "find /etc/skeleton -maxdepth 2 -type f" in script
    assert "-print0" in script
    assert "read -r -d '' path" in script
    for excluded in (
        "memory-gate",
        "device-registry",
        "phone-ssh",
        "github-app",
        "gmail",
        "secret",
        "credential",
        "token",
        "password",
        "known_hosts",
        "archive",
        "backup",
    ):
        assert excluded in script
    assert "pathlib.Path(target).exists()" in script
    assert "mktemp" in script
    assert "mv -f --" in script
    assert "systemctl daemon-reload" in script
    assert "desktop_run systemctl --user daemon-reload" in script
    assert "restart" not in script


def test_postchecks_include_both_failed_unit_counts_and_runtime_helpers() -> None:
    script = reconcile.RECONCILE_SCRIPT
    assert 'run_private registry_post desktop_run "$REGISTRY_CLI" doctor' in script
    assert 'run_private brother_final desktop_run "$BROTHER_VERIFY"' in script
    assert 'run_private aggregate_final desktop_run "$AGGREGATE_VERIFY"' in script
    assert 'run_private cast_status desktop_run "$CAST_STATUS" status' in script
    assert 'run_private pointer_status desktop_run "$POINTER_STATUS"' in script
    assert 'run_private watchdog_status desktop_run "$WATCHDOG_STATUS" status' in script
    assert 'systemctl --failed --no-legend' in script
    assert 'desktop_run systemctl --user --failed --no-legend' in script
    assert 'boot_id_before="$(cat /proc/sys/kernel/random/boot_id' in script
    assert "reboot" not in script.lower()
    assert "shutdown" not in script.lower()


def test_receipt_is_sanitized_for_later_audit_persist_not_memorygate_persistence() -> None:
    receipt = public_receipt()
    sanitized = reconcile.sanitize_public_receipt(receipt)

    assert sanitized["canonical_memory_post_step"] == "home_edge_audit_persist_v1"
    assert reconcile.success_criteria_met(sanitized)
    assert "MemoryGate" not in reconcile.RECONCILE_SCRIPT
    assert "/var/lib/skeleton" in reconcile.RECONCILE_SCRIPT
    assert "memory_gateway" not in reconcile.RECONCILE_SCRIPT
    with pytest.raises(ValueError, match="receipt_field_not_public_safe"):
        reconcile.sanitize_public_receipt(public_receipt(stable_reason="/home/oleksii/private"))

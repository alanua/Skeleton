from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge import post_migration_reconcile as reconcile
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "a" * 40
SECRET = "test-home-edge-secret"


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
        "registry_status": "healthy",
        "gallery_status": "already_healthy",
        "brother_status": "healthy",
        "aggregate_status": "healthy",
        "cast_status": "healthy",
        "pointer_status": "healthy",
        "watchdog_status": "healthy",
        "watchdog_critical_count": 0,
        "watchdog_warning_count": 0,
        "refresh_count": 0,
        "aggregate_source_repaired": False,
        "stale_before_count": 1,
        "stale_after_count": 0,
        "changed_file_count": 1,
        "system_failed_unit_count": 0,
        "user_failed_unit_count": 0,
        "current_brother_service_status": "active",
        "current_brother_guard_timer_status": "active",
        "rollback_ready": True,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "f" * 64,
        "audit_receipt_hash": "0" * 64,
        "stable_reason": "completed",
        "success_criteria": "met",
        "canonical_memory_post_step": reconcile.CANONICAL_MEMORY_POST_STEP,
    }
    receipt.update(updates)
    return receipt


def executor_receipt(stdout: str, *, exit_code: int = 0, status: str = "ok") -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status=status,
        request_id="req",
        node_id=reconcile.TARGET_NODE,
        execution_lane="privileged_mutation",
        exit_code=exit_code,
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
        (issue_body() + "\nCommand: /bin/sh", "unknown_runtime_input_field"),
        (issue_body() + "\nPath: /tmp/anything", "unknown_runtime_input_field"),
        (issue_body() + "\nUnit: brother-guard.service", "unknown_runtime_input_field"),
        (issue_body() + "\nTimeout: 1", "unknown_runtime_input_field"),
        (issue_body() + "\nLane: destructive", "unknown_runtime_input_field"),
        (issue_body() + "\nRun As: desktop-user", "unknown_runtime_input_field"),
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

    def fake_execute(request: Mapping[str, Any]):
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


def test_runtime_script_uses_exact_paths_and_boundaries() -> None:
    script = reconcile.RECONCILE_SCRIPT

    assert "/home/valertos08" in script
    assert "/home/oleksii" in script
    assert "/home/jeeves" not in script
    assert reconcile.GALLERY_VERIFY in script
    assert reconcile.GALLERY_REFRESH in script
    assert reconcile.BROTHER_VERIFY in script
    assert reconcile.AGGREGATE_VERIFY in script
    assert reconcile.REGISTRY_CLI in script
    assert reconcile.CURRENT_BROTHER_SERVICE in script
    assert reconcile.CURRENT_BROTHER_GUARD_TIMER in script
    assert "brother-guard.service" not in script
    assert "brother_guard_v2.service" in script
    assert "runuser -u oleksii -- env HOME=/home/oleksii XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in script
    assert 'run_user "$GALLERY_VERIFY" >"$gallery_pre_log" 2>&1' in script
    assert "--json" not in script
    assert "home_edge_audit_persist_v1" in script


def test_runtime_script_excludes_forbidden_mutations_and_broad_private_scans() -> None:
    lowered = reconcile.RECONCILE_SCRIPT.lower()

    forbidden = (
        "apt-get",
        "dpkg ",
        "systemctl reboot",
        "/sbin/reboot",
        "shutdown ",
        "poweroff",
        " tailscale ",
        " ufw ",
        "iptables",
        "parted",
        "mkfs",
        "ssh -",
        "known_hosts",
        "password",
        "token",
        "memorygate",
    )
    for token in forbidden:
        assert token not in lowered
    assert "find /home/oleksii/.local/bin -maxdepth 1" in reconcile.RECONCILE_SCRIPT
    assert "find /home/oleksii/.config/systemd/user -maxdepth 1" in reconcile.RECONCILE_SCRIPT
    assert "find /etc/systemd/system -maxdepth 1" in reconcile.RECONCILE_SCRIPT


def test_brother_parser_requires_healthy_and_true_boolean_checks() -> None:
    assert reconcile.brother_json_is_healthy({"status": "healthy"})
    assert reconcile.brother_json_is_healthy({"status": "healthy", "checks": {"svc": True}})
    assert not reconcile.brother_json_is_healthy({"status": "ok", "checks": {"svc": True}})
    assert not reconcile.brother_json_is_healthy({"status": "healthy", "checks": {"svc": False}})
    assert not reconcile.brother_json_is_healthy({"status": "healthy", "checks": {"svc": "true"}})


def test_cast_pointer_watchdog_payloads_must_be_authoritatively_healthy() -> None:
    assert reconcile.cast_json_is_healthy({"status": "ok", "service": "skeleton-cast"})
    assert not reconcile.cast_json_is_healthy({"status": "ok", "service": "other"})
    assert reconcile.pointer_json_is_healthy(
        {
            "service_active": True,
            "socket_exists": True,
            "uinput_device_registered": True,
            "broker_response": "ok",
            "api_backend": "uinput_pointer_broker",
        }
    )
    assert not reconcile.pointer_json_is_healthy(
        {
            "service_active": True,
            "socket_exists": True,
            "uinput_device_registered": True,
            "broker_response": "ok",
            "api_backend": "fallback",
        }
    )
    ok, critical, warnings = reconcile.watchdog_json_status(
        {
            "last": {
                "overall": "healthy",
                "healthy": True,
                "summary": {"critical": 0, "warnings": 0},
            }
        }
    )
    assert (ok, critical, warnings) == (True, 0, 0)
    assert reconcile.watchdog_json_status(
        {
            "last": {
                "overall": "healthy",
                "healthy": True,
                "summary": {"critical": 2, "warnings": 3},
            }
        }
    ) == (False, 2, 3)


def test_watchdog_missing_summary_counts_rejects_without_synthesized_zero() -> None:
    assert reconcile.watchdog_json_status(
        {"last": {"overall": "healthy", "healthy": True, "summary": {}}}
    ) == (False, 0, 0)


@pytest.mark.parametrize(
    "critical,warnings",
    [
        (False, 0),
        (0, True),
        ("0", 0),
        (0, "0"),
        (-1, 0),
        (0, -1),
        (0.0, 0),
        (0, 0.0),
    ],
)
def test_watchdog_counts_must_be_real_nonnegative_integers(
    critical: object, warnings: object
) -> None:
    ok, _, _ = reconcile.watchdog_json_status(
        {
            "last": {
                "overall": "healthy",
                "healthy": True,
                "summary": {"critical": critical, "warnings": warnings},
            }
        }
    )

    assert not ok


def test_success_criteria_requires_all_final_contracts() -> None:
    assert reconcile.success_criteria_met(public_receipt())
    assert not reconcile.success_criteria_met(public_receipt(watchdog_warning_count=1))
    assert not reconcile.success_criteria_met(public_receipt(system_failed_unit_count=1))
    assert not reconcile.success_criteria_met(public_receipt(stale_after_count=1))
    assert not reconcile.success_criteria_met(
        public_receipt(canonical_memory_post_step="other")
    )


def test_nonzero_executor_exit_preserves_valid_embedded_blocked_receipt() -> None:
    embedded = public_receipt(
        success_criteria="not_met",
        stable_reason="rollback_failed",
        rollback_ready=True,
        rollback_applied=False,
    )

    parsed = reconcile.public_receipt_from_executor_stdout(
        executor_receipt(
            "diagnostic preface\n" + json.dumps(embedded) + "\n",
            exit_code=60,
        ).to_mapping()
    )

    assert parsed["success_criteria"] == "not_met"
    assert parsed["stable_reason"] == "rollback_failed"
    assert parsed["rollback_ready"] is True
    assert parsed["rollback_applied"] is False


@pytest.mark.parametrize("stdout", ["", "not-json", '{"stable_reason":'])
def test_nonzero_executor_exit_without_valid_receipt_falls_back_generic(
    stdout: str,
) -> None:
    parsed = reconcile.public_receipt_from_executor_stdout(
        executor_receipt(stdout, exit_code=10).to_mapping()
    )

    assert parsed["success_criteria"] == "not_met"
    assert parsed["stable_reason"] == "executor_receipt_not_ok"


def test_transport_failure_falls_back_generic_even_with_stdout() -> None:
    parsed = reconcile.public_receipt_from_executor_stdout(
        executor_receipt(json.dumps(public_receipt()), status="blocked").to_mapping()
    )

    assert parsed["stable_reason"] == "executor_receipt_not_ok"


def test_runtime_script_watchdog_and_daemon_reload_fail_closed() -> None:
    script = reconcile.RECONCILE_SCRIPT

    assert "last.summary.critical 2>/dev/null || echo 0" not in script
    assert "last.summary.warnings 2>/dev/null || echo 0" not in script
    assert "critical < 0" in script
    assert "warnings < 0" in script
    assert "fail_after_mutation system_daemon_reload_failed" in script
    assert "fail_after_mutation user_daemon_reload_failed" in script
    assert 'stable_reason="rollback_failed"' in script
    assert 'rollback_applied=true' in script


def test_runtime_script_identity_status_is_tracked_not_predeclared_verified() -> None:
    script = reconcile.RECONCILE_SCRIPT

    assert 'os_identity_status="unverified"' in script
    assert 'node_identity_status="unverified"' in script
    assert 'printf \'"os_identity_status":"%s",\' "$os_identity_status"' in script
    assert 'printf \'"node_identity_status":"%s",\' "$node_identity_status"' in script
    assert 'printf \'"os_identity_status":"verified",\'' not in script
    assert 'printf \'"node_identity_status":"verified",\'' not in script
    assert script.index('case "${VERSION_ID:-}"') < script.index('os_identity_status="verified"')
    assert script.index('os_identity_status="verified"') < script.index('[ "$(hostname)" = "home-edge-01" ]')
    assert script.index('[ -S /run/user/1000/bus ]') < script.index('node_identity_status="verified"')

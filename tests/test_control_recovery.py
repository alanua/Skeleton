from __future__ import annotations

from pathlib import Path

from core.control_recovery import (
    CONTROL_RECOVERY_SCHEMA,
    FailureClass,
    RecoveryStatus,
    RecoveryStore,
    build_recovery_plan,
    execute_recovery_packet,
)


def _done(action_id: str) -> str:
    return (
        "DONE: Runner host maintenance task completed.\n"
        f"action={action_id}\n"
        "success_criteria=met"
    )


def _packet(**updates: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": CONTROL_RECOVERY_SCHEMA,
        "failure_class": FailureClass.CODEGEN_RUNTIME_UNHEALTHY.value,
        "failure_key": "control:codegen-runtime",
        "backoff_seconds": 10,
        "max_attempts": 3,
    }
    packet.update(updates)
    return packet


def test_codegen_failure_uses_fixed_non_codegen_action_then_canary_and_queue(tmp_path: Path) -> None:
    calls: list[str] = []

    def action(action_id: str) -> str:
        calls.append(action_id)
        assert action_id != "codex"
        return _done(action_id)

    receipt = execute_recovery_packet(
        _packet(),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=action,
        canary_executor=lambda canary: canary == "codegen_read_only_canary",
    )

    assert receipt["status"] == RecoveryStatus.RECOVERED.value
    assert receipt["actions_executed"] == ["executor_service_preflight", "queue_reactivate"]
    assert receipt["canaries_executed"] == ["codegen_read_only_canary"]
    assert calls == ["executor_service_preflight", "queue_reactivate"]


def test_missing_canary_executor_fails_closed(tmp_path: Path) -> None:
    receipt = execute_recovery_packet(
        _packet(failure_key="control:missing-canary"),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=_done,
        canary_executor=None,
    )

    assert receipt["status"] == RecoveryStatus.WAITING_RECOVERY.value
    assert receipt["reason"] == "CANARY_EXECUTOR_REQUIRED"
    assert receipt["canaries_executed"] == []


def test_registered_checkout_recovery_syncs_then_canaries_then_reactivates_queue(tmp_path: Path) -> None:
    calls: list[str] = []
    receipt = execute_recovery_packet(
        _packet(
            failure_class=FailureClass.REGISTERED_CHECKOUT_STALE_OR_DIRTY.value,
            failure_key="control:checkout-stale",
        ),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda canary: canary == "registered_checkout_freshness_canary",
    )
    assert receipt["status"] == RecoveryStatus.RECOVERED.value
    assert calls == ["registered_checkout_recover", "queue_reactivate"]
    assert receipt["canaries_executed"] == ["registered_checkout_freshness_canary"]


def test_stale_poller_uses_registered_reload_then_resumes(tmp_path: Path) -> None:
    calls: list[str] = []
    receipt = execute_recovery_packet(
        _packet(failure_class=FailureClass.LONG_LIVED_POLLER_STALE.value, failure_key="control:poller-stale"),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda _canary: True,
    )
    assert receipt["status"] == RecoveryStatus.RECOVERED.value
    assert calls == ["long_lived_poller_reload", "queue_reactivate"]


def test_github_actions_lane_failure_does_not_stall_healthy_issue_runner(tmp_path: Path) -> None:
    calls: list[str] = []
    receipt = execute_recovery_packet(
        _packet(
            failure_class=FailureClass.GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY.value,
            failure_key="control:gha-lane",
        ),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda _canary: True,
    )
    assert receipt["status"] == RecoveryStatus.RECOVERED.value
    assert calls == ["issue_runner_continue", "queue_reactivate"]


def test_duplicate_restart_tick_does_not_repeat_recovered_action(tmp_path: Path) -> None:
    calls: list[str] = []
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    packet = _packet(failure_key="control:duplicate")
    first = execute_recovery_packet(packet, store=store, now=100, action_executor=lambda action: calls.append(action) or _done(action), canary_executor=lambda _canary: True)
    second = execute_recovery_packet(packet, store=store, now=101, action_executor=lambda action: calls.append(action) or _done(action), canary_executor=lambda _canary: True)
    assert first["status"] == RecoveryStatus.RECOVERED.value
    assert second["reason"] == "RECOVERY_ALREADY_DONE"
    assert calls == ["executor_service_preflight", "queue_reactivate"]


def test_failed_recovery_retries_with_backoff_then_exactly_one_operator_notice(tmp_path: Path) -> None:
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    packet = _packet(failure_key="control:exhaust", max_attempts=2, backoff_seconds=5)
    first = execute_recovery_packet(packet, store=store, now=100, action_executor=lambda _action: "BLOCKED: no\nsuccess_criteria=not_met")
    duplicate = execute_recovery_packet(packet, store=store, now=101, action_executor=lambda _action: _done("unexpected"))
    second = execute_recovery_packet(packet, store=store, now=105, action_executor=lambda _action: "BLOCKED: no\nsuccess_criteria=not_met")
    third = execute_recovery_packet(packet, store=store, now=110, action_executor=lambda _action: _done("unexpected"))
    assert first["status"] == RecoveryStatus.WAITING_RECOVERY.value
    assert first["next_retry_at"] == 105
    assert duplicate["reason"] == "RECOVERY_BACKOFF_ACTIVE"
    assert second["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert second["needs_operator_notification"] is True
    assert third["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert third["needs_operator_notification"] is False


def test_unknown_or_issue_supplied_authority_fails_closed(tmp_path: Path) -> None:
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    unknown = execute_recovery_packet(_packet(failure_class="NEW_THING"), store=store, now=100, action_executor=_done)
    unknown_again = execute_recovery_packet(_packet(failure_class="NEW_THING"), store=store, now=101, action_executor=_done)
    broadened = execute_recovery_packet(_packet(command="pip install arbitrary", package="badpkg", model="codex-new"), store=store, now=100, action_executor=_done)
    assert unknown["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert unknown["reason"] == "UNKNOWN_UNSAFE_RECOVERY"
    assert unknown["needs_operator_notification"] is True
    assert unknown_again["needs_operator_notification"] is False
    assert broadened["reason"] == "UNREGISTERED_RECOVERY_AUTHORITY"


def test_plans_never_require_codegen() -> None:
    for failure_class in FailureClass:
        plan = build_recovery_plan({"schema": CONTROL_RECOVERY_SCHEMA, "failure_class": failure_class.value, "failure_key": f"control:{failure_class.value}"})
        assert plan is not None
        assert plan.requires_codegen is False

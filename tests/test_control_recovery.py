from __future__ import annotations

from pathlib import Path

from core.control_recovery import (
    CODEGEN_UNKNOWN_VARIANT_MAX_MESSAGE,
    CONTROL_RECOVERY_SCHEMA,
    FailureClass,
    RecoveryStatus,
    RecoveryStore,
    SafeResponseClass,
    build_recovery_plan,
    derive_failure_fingerprint,
    execute_recovery_packet,
    is_codegen_unknown_variant_max_failure,
    production_control_recovery_db_path,
    _validated_agent_state_db_path,
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
    assert receipt["actions_executed"] == ["codegen_runtime_recover", "queue_reactivate"]
    assert receipt["canaries_executed"] == ["codegen_read_only_canary"]
    assert calls == ["codegen_runtime_recover", "queue_reactivate"]


def test_bounded_action_reason_propagates_without_raw_output(tmp_path: Path) -> None:
    report = "\n".join(
        (
            "BLOCKED: Runner host maintenance task completed.",
            "maintenance_task_id=codegen_runtime_recover",
            "reason=CODEX_RUNTIME_RECOVERY_NPM_RUNTIME_BINARY_MISSING",
            "success_criteria=not_met",
            "private=/must/not/propagate",
        )
    )
    receipt = execute_recovery_packet(
        _packet(failure_key="control:reason-propagation"),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=lambda _action: report,
    )
    assert receipt["status"] == RecoveryStatus.WAITING_RECOVERY.value
    assert receipt["reason"] == (
        "RECOVERY_ACTION_FAILED_CODEX_RUNTIME_RECOVERY_NPM_RUNTIME_BINARY_MISSING"
    )
    assert "/must/not/propagate" not in str(receipt)


def test_untrusted_action_reason_format_is_not_propagated(tmp_path: Path) -> None:
    report = "\n".join(
        (
            "BLOCKED: no",
            "reason=bad/path/value",
            "success_criteria=not_met",
        )
    )
    receipt = execute_recovery_packet(
        _packet(failure_key="control:unsafe-reason"),
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=lambda _action: report,
    )
    assert receipt["reason"] == "RECOVERY_ACTION_FAILED"
    assert "bad/path/value" not in str(receipt)


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
        _packet(
            failure_class=FailureClass.LONG_LIVED_POLLER_STALE.value,
            failure_key="control:poller-stale",
        ),
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
    first = execute_recovery_packet(
        packet,
        store=store,
        now=100,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda _canary: True,
    )
    second = execute_recovery_packet(
        packet,
        store=store,
        now=101,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda _canary: True,
    )
    assert first["status"] == RecoveryStatus.RECOVERED.value
    assert second["reason"] == "RECOVERY_ALREADY_DONE"
    assert calls == ["codegen_runtime_recover", "queue_reactivate"]


def test_failed_recovery_retries_with_backoff_then_exactly_one_operator_notice(tmp_path: Path) -> None:
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    packet = _packet(failure_key="control:exhaust", max_attempts=2, backoff_seconds=5)
    first = execute_recovery_packet(
        packet,
        store=store,
        now=100,
        action_executor=lambda _action: "BLOCKED: no\nsuccess_criteria=not_met",
    )
    duplicate = execute_recovery_packet(
        packet,
        store=store,
        now=101,
        action_executor=lambda _action: _done("unexpected"),
    )
    second = execute_recovery_packet(
        packet,
        store=store,
        now=105,
        action_executor=lambda _action: "BLOCKED: no\nsuccess_criteria=not_met",
    )
    third = execute_recovery_packet(
        packet,
        store=store,
        now=110,
        action_executor=lambda _action: _done("unexpected"),
    )
    assert first["status"] == RecoveryStatus.WAITING_RECOVERY.value
    assert first["next_retry_at"] == 105
    assert duplicate["reason"] == "RECOVERY_BACKOFF_ACTIVE"
    assert second["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert second["needs_operator_notification"] is True
    assert third["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert third["needs_operator_notification"] is False


def test_unknown_or_issue_supplied_authority_fails_closed(tmp_path: Path) -> None:
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    unknown = execute_recovery_packet(
        _packet(failure_class="NEW_THING"), store=store, now=100, action_executor=_done
    )
    unknown_again = execute_recovery_packet(
        _packet(failure_class="NEW_THING"), store=store, now=101, action_executor=_done
    )
    broadened = execute_recovery_packet(
        _packet(command="pip install arbitrary", package="badpkg", model="codex-new"),
        store=store,
        now=100,
        action_executor=_done,
    )
    assert unknown["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert unknown["reason"] == "UNKNOWN_UNSAFE_RECOVERY"
    assert unknown["needs_operator_notification"] is True
    assert unknown_again["needs_operator_notification"] is False
    assert broadened["reason"] == "UNREGISTERED_RECOVERY_AUTHORITY"


def test_only_exact_live_unknown_variant_max_autoclassifies_codegen() -> None:
    noisy = f"prefix {CODEGEN_UNKNOWN_VARIANT_MAX_MESSAGE} suffix OpenAI Codex v0.999"
    assert is_codegen_unknown_variant_max_failure(noisy, 1) is True
    plan = build_recovery_plan(
        {
            "schema": CONTROL_RECOVERY_SCHEMA,
            "failure_key": "control:live-max",
            "output": noisy,
        }
    )
    assert plan is not None
    assert plan.failure_class is FailureClass.CODEGEN_RUNTIME_UNHEALTHY

    non_matches = (
        "sqlite3.OperationalError: attempt to write a readonly database",
        "permission denied while opening worktree",
        "quota exceeded",
        "provider outage",
        "prompt task failed validation",
        "generic codex failed",
        "failed to decode models response: unknown variant `high`",
    )
    for text in non_matches:
        assert is_codegen_unknown_variant_max_failure(text, 1) is False
        assert build_recovery_plan({"failure_key": "control:x", "output": text}) is None
    assert is_codegen_unknown_variant_max_failure(noisy, 0) is False


def test_production_control_recovery_db_is_fixed_agent_local() -> None:
    assert production_control_recovery_db_path() == Path(
        "/home/agent/.local/state/skeleton-runner/control-recovery/control_recovery.sqlite3"
    )
    rendered = str(production_control_recovery_db_path())
    assert "/var/lib" not in rendered
    assert "/tmp" not in rendered
    assert "/.codex/" not in rendered


def test_agent_state_path_hardening_fails_closed_for_symlink_and_private_modes(
    tmp_path: Path,
) -> None:
    loose_parent = tmp_path / "loose"
    loose_parent.mkdir(mode=0o755)
    db = loose_parent / "control_recovery.sqlite3"
    db.write_text("", encoding="utf-8")
    try:
        _validated_agent_state_db_path(db)
        raise AssertionError("loose state parent must fail closed")
    except ValueError as exc:
        assert str(exc) == "STATE_PATH_PRIVATE_MODE_REQUIRED"

    private_parent = tmp_path / "private"
    private_parent.mkdir(mode=0o700)
    target = private_parent / "target.sqlite3"
    target.write_text("", encoding="utf-8")
    symlink = private_parent / "link.sqlite3"
    symlink.symlink_to(target)
    try:
        _validated_agent_state_db_path(symlink)
        raise AssertionError("state DB symlink must fail closed")
    except ValueError as exc:
        assert str(exc) == "STATE_DB_NOT_REGULAR_FILE"


def test_plans_never_require_codegen() -> None:
    for failure_class in FailureClass:
        plan = build_recovery_plan(
            {
                "schema": CONTROL_RECOVERY_SCHEMA,
                "failure_class": failure_class.value,
                "failure_key": f"control:{failure_class.value}",
            }
        )
        assert plan is not None
        assert plan.requires_codegen is False


def test_verified_lesson_changes_repeated_incident_to_learned_safe_route(tmp_path: Path) -> None:
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    packet = _packet(
        failure_class=FailureClass.STALE_BASE.value,
        failure_key="control:stale-base:a:b",
        reason_class="STALE_BASE",
        task_kind="code_generation",
        phase="preflight",
    )
    calls: list[str] = []

    first = execute_recovery_packet(
        packet,
        store=store,
        now=100,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda _canary: True,
    )
    repeated = execute_recovery_packet(
        {**packet, "failure_key": "control:stale-base:duplicate"},
        store=store,
        now=130,
        action_executor=lambda action: calls.append(action) or _done(action),
        canary_executor=lambda _canary: True,
    )

    assert first["status"] == RecoveryStatus.RECOVERED.value
    assert first["response_class"] == SafeResponseClass.CURRENT_MAIN_REBUILD.value
    assert repeated["status"] == RecoveryStatus.RECOVERED.value
    assert repeated["response_class"] == SafeResponseClass.CURRENT_MAIN_REBUILD.value
    assert calls == [
        "current_main_rebuild",
        "queue_reactivate",
        "current_main_rebuild",
        "queue_reactivate",
    ]
    assert store.learning_metrics()["repeats_prevented"] == 1


def test_failed_adaptation_advances_to_second_registered_route_and_prefers_it(
    tmp_path: Path,
) -> None:
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    packet = _packet(
        failure_class=FailureClass.OPAQUE_CI_FAILURE.value,
        failure_key="control:opaque-ci",
        reason_class="OPAQUE_CI_FAILURE",
        task_kind="code_generation",
        phase="validation",
        max_attempts=3,
        backoff_seconds=1,
    )
    calls: list[str] = []

    def action(action_id: str) -> str:
        calls.append(action_id)
        if action_id == "current_main_rebuild":
            return "BLOCKED: no\nreason=CI_STILL_FAILING\nsuccess_criteria=not_met"
        return _done(action_id)

    first = execute_recovery_packet(
        packet,
        store=store,
        now=100,
        action_executor=action,
        canary_executor=lambda _canary: True,
    )
    second = execute_recovery_packet(
        packet,
        store=store,
        now=101,
        action_executor=action,
        canary_executor=lambda _canary: True,
    )
    recurrence = execute_recovery_packet(
        {**packet, "failure_key": "control:opaque-ci-next"},
        store=store,
        now=130,
        action_executor=action,
        canary_executor=lambda _canary: True,
    )

    assert first["status"] == RecoveryStatus.WAITING_RECOVERY.value
    assert second["status"] == RecoveryStatus.RECOVERED.value
    assert second["response_class"] == SafeResponseClass.CURRENT_MAIN_REPLACEMENT.value
    assert recurrence["response_class"] == SafeResponseClass.CURRENT_MAIN_REPLACEMENT.value
    assert calls == [
        "current_main_rebuild",
        "current_main_replacement",
        "queue_reactivate",
        "current_main_replacement",
        "queue_reactivate",
    ]


def test_restart_preserves_fingerprint_attempt_and_duplicate_lesson(tmp_path: Path) -> None:
    db = tmp_path / "recovery.sqlite3"
    packet = _packet(
        failure_class=FailureClass.OPAQUE_CI_FAILURE.value,
        failure_key="control:restart-ci",
        reason_class="OPAQUE_CI_FAILURE",
        phase="validation",
        max_attempts=3,
        backoff_seconds=1,
    )
    first = execute_recovery_packet(
        packet,
        store=RecoveryStore(db),
        now=100,
        action_executor=lambda _action: "BLOCKED: no\nreason=CI_STILL_FAILING\nsuccess_criteria=not_met",
        canary_executor=lambda _canary: True,
    )
    after_restart = execute_recovery_packet(
        packet,
        store=RecoveryStore(db),
        now=101,
        action_executor=_done,
        canary_executor=lambda _canary: True,
    )
    metrics = RecoveryStore(db).learning_metrics()

    assert first["next_retry_at"] == 101
    assert after_restart["response_class"] == SafeResponseClass.CURRENT_MAIN_REPLACEMENT.value
    assert metrics["incident_classes_seen"] == 1
    assert metrics["lessons_verified"] == 1


def test_fingerprint_excludes_raw_private_output() -> None:
    safe = {
        "failure_class": FailureClass.STALE_BASE.value,
        "reason_class": "STALE_BASE",
        "task_kind": "code_generation",
        "phase": "preflight",
        "output": "/private/path/token@example.com",
    }
    fingerprint = derive_failure_fingerprint(safe)
    assert fingerprint.startswith("failure-fp:")
    assert "/private" not in fingerprint
    assert "token" not in fingerprint


def test_missing_tool_with_no_approved_lane_becomes_bounded_needs_operator(
    tmp_path: Path,
) -> None:
    packet = _packet(
        failure_class=FailureClass.TOOLCHAIN_CAPABILITY_MISMATCH.value,
        failure_key="control:toolchain:unknown",
        reason_class="TOOLCHAIN_CAPABILITY_MISMATCH",
        capability="unknown:tool",
    )
    receipt = execute_recovery_packet(
        packet,
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=_done,
    )
    assert receipt["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert receipt["reason"] == "REPAIR_TASK_REQUIRED"
    assert receipt["needs_operator_notification"] is True


def test_ambiguous_mutating_result_never_becomes_auto_retry_lesson(tmp_path: Path) -> None:
    packet = _packet(
        failure_class=FailureClass.AMBIGUOUS_MUTATING_RESULT.value,
        failure_key="control:ambiguous-mutating",
        reason_class="AMBIGUOUS_MUTATING_RESULT",
    )
    first = execute_recovery_packet(
        packet,
        store=RecoveryStore(tmp_path / "recovery.sqlite3"),
        now=100,
        action_executor=_done,
    )
    assert first["status"] == RecoveryStatus.NEEDS_OPERATOR.value
    assert first["response_class"] is None

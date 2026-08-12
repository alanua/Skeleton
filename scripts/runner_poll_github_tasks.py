from __future__ import annotations

# The implementation blob is byte-for-byte the previously reviewed Runner module.
# Execute it in this canonical module namespace so existing imports/monkeypatches,
# authority gates, maintenance handlers, and process_issue globals remain unchanged.
import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath

_IMPLEMENTATION_PATH = _BootstrapPath(__file__).with_name("runner_poll_github_tasks_impl.py")
_original_module_name = __name__
_exec_module_name = (
    _original_module_name
    if _original_module_name != "__main__"
    else "scripts.runner_poll_github_tasks"
)
_bootstrap_module = _bootstrap_sys.modules.get(_original_module_name)
if _bootstrap_module is not None:
    _bootstrap_sys.modules.setdefault(_exec_module_name, _bootstrap_module)
globals()["__name__"] = _exec_module_name
try:
    exec(
        compile(
            _IMPLEMENTATION_PATH.read_text(encoding="utf-8"),
            str(_IMPLEMENTATION_PATH),
            "exec",
        ),
        globals(),
        globals(),
    )
finally:
    globals()["__name__"] = _original_module_name


def _autonomous_queue_eligible_snapshot() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Read queue state and apply the existing public-safe selection gates without mutation."""
    ready_issues = get_ready_issues()
    if ready_issues:
        return ready_issues, [], []
    candidate_issues = get_queue_replenisher_candidate_issues()
    eligible = select_runner_queue_replenishment_targets(
        ready_issues,
        candidate_issues,
    )
    return ready_issues, candidate_issues, eligible


def _autonomous_queue_occurrence_key(eligible: list[dict[str, Any]]) -> str:
    numbers = sorted(
        number
        for issue in eligible
        if (number := _queue_replenisher_issue_number(issue)) is not None
    )
    payload = ",".join(str(number) for number in numbers).encode("ascii")
    digest = hashlib.sha256(payload).hexdigest()[:20]
    return f"control:queue-idle:{digest}"


def _autonomous_queue_packet(eligible: list[dict[str, Any]]) -> dict[str, object]:
    packet = public_safe_failure_packet(
        failure_class=FailureClass.QUEUE_IDLE_WITH_ELIGIBLE_WORK,
        failure_key=_autonomous_queue_occurrence_key(eligible),
        reason_class="QUEUE_IDLE_WITH_ELIGIBLE_WORK",
        task_kind="runner_poll",
        phase="queue_intake",
        capability="queue:label",
        operation="replenish_runner_queue",
    )
    packet["route_id"] = "runner_queue"
    # The failure key is occurrence-specific, while the fingerprint remains a
    # stable public-safe incident class so verified lessons can be reused.
    packet["fingerprint"] = derive_failure_fingerprint(
        packet,
        failure_class=FailureClass.QUEUE_IDLE_WITH_ELIGIBLE_WORK,
    )
    return packet


def _autonomous_queue_blocked_report(reason: str) -> str:
    return _maintenance_report(
        "BLOCKED",
        REPLENISH_RUNNER_QUEUE,
        [f"reason={reason}", "telegram_notifications=0"],
        "not_met",
    )


def _autonomous_queue_replenish_action(expected_key: str) -> str:
    # Recheck all current gates immediately before the only queue mutation.
    ready_before, _candidates, eligible = _autonomous_queue_eligible_snapshot()
    if ready_before:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_READY_WORK_PRESENT")
    if not eligible:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_NO_ELIGIBLE_WORK")
    if _autonomous_queue_occurrence_key(eligible) != expected_key:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_CANDIDATES_CHANGED")

    report = replenish_runner_queue("")
    ready_after = get_ready_issues()
    if len(ready_after) <= len(ready_before):
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_NO_PROGRESS")
    if not maintenance_report_is_done(report):
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_REPORT_NOT_DONE")
    return report


def maybe_recover_idle_runner_queue() -> bool:
    """Recover an idle queue through the existing durable RecoveryStore only."""
    try:
        ready_before, _candidates, eligible = _autonomous_queue_eligible_snapshot()
        if ready_before or not eligible:
            return False
        packet = _autonomous_queue_packet(eligible)
        expected_key = str(packet["failure_key"])

        def run_action(action_id: str) -> str:
            if action_id != "queue_reactivate":
                return _autonomous_queue_blocked_report(
                    "QUEUE_RECOVERY_ACTION_NOT_ALLOWLISTED"
                )
            return _autonomous_queue_replenish_action(expected_key)

        receipt = execute_recovery_packet(
            packet,
            store=RecoveryStore(control_recovery_db_path()),
            now=int(time.time()),
            action_executor=run_action,
            canary_executor=None,
        )
        if str(receipt.get("status") or "") != "RECOVERED":
            return False
        return bool(get_ready_issues())
    except Exception:
        # Query/control failures fail closed. RecoveryStore itself owns retry
        # and backoff once a bounded incident has been recorded.
        return False


def self_heal_run_now_queue_intake() -> int:
    """Compatibility entrypoint bound to the single learned queue recovery path."""
    return 1 if maybe_recover_idle_runner_queue() else 0


def maybe_replenish_runner_queue_after_completion() -> bool:
    """Completion hook uses the same durable queue-idle path, never direct labels."""
    return maybe_recover_idle_runner_queue()


def poll_once(workdir: str | None = None) -> int:
    try:
        reconcile_scheduler_on_poll()
    except Exception:
        pass

    # A fresh systemd oneshot cannot overlap itself; Scheduler reconciliation
    # above resolves durable leases/ambiguous receipts. Standard queue selection
    # still treats any residual runner:running issue as occupied work for file
    # overlap, so unrelated safe work can continue without replaying that task.
    maybe_recover_idle_runner_queue()
    issues = get_ready_issues()
    for issue in issues:
        process_issue(issue, workdir=workdir)
    return len(issues)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll GitHub runner task issues.")
    parser.add_argument("--loop", action="store_true", help="poll continuously")
    parser.add_argument("--workdir", default=None, help="repository workdir")
    args = parser.parse_args()

    if args.loop:
        while True:
            poll_once(workdir=args.workdir)
            time.sleep(POLL_INTERVAL)
    else:
        poll_once(workdir=args.workdir)


if _original_module_name == "__main__":
    main()

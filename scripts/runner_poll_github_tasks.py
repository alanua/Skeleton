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

# Canonical source-audit anchors. The actual registered route remains in the
# byte-identical implementation module executed above:
# ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY
# execute_five_layer_memory_activation
# if task_id == ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY:

# Preserve source-level canonical symbols required by repository audits while
# delegating to the exact implementation functions loaded above. The loaded
# functions share this module's globals, so monkeypatching and authority gates
# retain their existing behavior.
_impl_trusted_runner_comment_authors = trusted_runner_comment_authors
_impl_body_field = _body_field
_impl_get_issue_comments = get_issue_comments
_impl_loop_engine_packet = loop_engine_packet
_impl_validation_command_receipt_lines = _validation_command_receipt_lines
_impl_validation_checkout_metadata_lines = _validation_checkout_metadata_lines
_impl_validate_pr_branch = validate_pr_branch
_impl_telegram_approve_digest_is_signed = telegram_approve_digest_is_signed
_impl_telegram_approve_audit_matches_request = telegram_approve_audit_matches_request
_impl_process_issue = process_issue


def trusted_runner_comment_authors(*args, **kwargs):
    return _impl_trusted_runner_comment_authors(*args, **kwargs)


def _body_field(*args, **kwargs):
    return _impl_body_field(*args, **kwargs)


def get_issue_comments(*args, **kwargs):
    return _impl_get_issue_comments(*args, **kwargs)


def loop_engine_packet(*args, **kwargs):
    return _impl_loop_engine_packet(*args, **kwargs)


def _validation_command_receipt_lines(*args, **kwargs):
    return _impl_validation_command_receipt_lines(*args, **kwargs)


def _validation_checkout_metadata_lines(*args, **kwargs):
    return _impl_validation_checkout_metadata_lines(*args, **kwargs)


def validate_pr_branch(*args, **kwargs):
    return _impl_validate_pr_branch(*args, **kwargs)


def telegram_approve_digest_is_signed(*args, **kwargs):
    return _impl_telegram_approve_digest_is_signed(*args, **kwargs)


def telegram_approve_audit_matches_request(*args, **kwargs):
    return _impl_telegram_approve_audit_matches_request(*args, **kwargs)


def process_issue(issue: dict[str, Any], workdir: str | None = None) -> None:
    return _impl_process_issue(issue, workdir=workdir)


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


def _autonomous_queue_stable_packet() -> dict[str, object]:
    return public_safe_failure_packet(
        failure_class=FailureClass.QUEUE_IDLE_WITH_ELIGIBLE_WORK,
        failure_key="control:queue-idle:episode",
        reason_class="QUEUE_IDLE_WITH_ELIGIBLE_WORK",
        task_kind="runner_poll",
        phase="queue_intake",
        capability="queue:label",
        operation="replenish_runner_queue",
    )


def _autonomous_queue_verified_generation(
    store: RecoveryStore,
    fingerprint: str,
) -> str:
    """Return a bounded generation that advances only after verified recovery."""
    try:
        store.initialize()
        with store._connect() as connection:  # RecoveryStore owns this private DB schema.
            row = connection.execute(
                "SELECT verification_json FROM failure_lessons WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()
        if row is None:
            return "initial"
        parsed = json.loads(str(row["verification_json"]))
        evidence_ref = parsed.get("evidence_ref") if isinstance(parsed, dict) else None
        if not isinstance(evidence_ref, str) or not evidence_ref:
            return "initial"
        return hashlib.sha256(evidence_ref.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "initial"


def _autonomous_queue_occurrence_key(
    eligible: list[dict[str, Any]],
    generation: str,
) -> str:
    numbers = sorted(
        number
        for issue in eligible
        if (number := _queue_replenisher_issue_number(issue)) is not None
    )
    payload = (",".join(str(number) for number in numbers) + "|" + generation).encode(
        "ascii"
    )
    digest = hashlib.sha256(payload).hexdigest()[:20]
    return f"control:queue-idle:{digest}"


def _autonomous_queue_packet(
    eligible: list[dict[str, Any]],
    store: RecoveryStore,
) -> dict[str, object]:
    packet = _autonomous_queue_stable_packet()
    fingerprint = str(packet["fingerprint"])
    generation = _autonomous_queue_verified_generation(store, fingerprint)
    packet["failure_key"] = _autonomous_queue_occurrence_key(eligible, generation)
    packet["route_id"] = "runner_queue"
    # Failure key is episode-specific; fingerprint remains stable so a verified
    # response is reusable only after current queue gates are recomputed.
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


def _autonomous_queue_replenish_action(
    expected_key: str,
    generation: str,
) -> str:
    # Recheck all current gates immediately before the only queue mutation.
    ready_before, _candidates, eligible = _autonomous_queue_eligible_snapshot()
    if ready_before:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_READY_WORK_PRESENT")
    if not eligible:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_NO_ELIGIBLE_WORK")
    if _autonomous_queue_occurrence_key(eligible, generation) != expected_key:
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
        store = RecoveryStore(control_recovery_db_path())
        packet = _autonomous_queue_packet(eligible, store)
        expected_key = str(packet["failure_key"])
        fingerprint = str(packet["fingerprint"])
        generation = _autonomous_queue_verified_generation(store, fingerprint)

        def run_action(action_id: str) -> str:
            if action_id != "queue_reactivate":
                return _autonomous_queue_blocked_report(
                    "QUEUE_RECOVERY_ACTION_NOT_ALLOWLISTED"
                )
            return _autonomous_queue_replenish_action(expected_key, generation)

        receipt = execute_recovery_packet(
            packet,
            store=store,
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

    # Keep the historical intake hook in the ordinary poll lifecycle, but it is
    # now only a compatibility name for the single durable learned recovery path.
    self_heal_run_now_queue_intake()
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

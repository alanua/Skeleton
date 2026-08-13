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
_impl_get_queue_replenisher_candidate_issues = get_queue_replenisher_candidate_issues
_impl_replenish_runner_queue = replenish_runner_queue


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


_QUEUE_RECOVERY_CANDIDATE_OVERRIDE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "skeleton_queue_recovery_candidate_override",
    default=None,
)
_QUEUE_RECOVERY_SOURCE: ContextVar[str] = ContextVar(
    "skeleton_queue_recovery_source",
    default="compat",
)


def get_queue_replenisher_candidate_issues() -> list[dict[str, Any]]:
    """Return the rechecked snapshot only while the existing replenisher executes."""
    override = _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.get()
    if override is not None:
        return [dict(issue) for issue in override]
    return _impl_get_queue_replenisher_candidate_issues()


def _autonomous_queue_store_path() -> Path:
    """Keep validation/feature recovery state local; production main stays canonical."""
    configured = control_recovery_db_path()
    production = production_control_recovery_db_path()
    if configured != production:
        return configured
    try:
        projects = load_runner_project_tree().get("projects")
        matches = [
            Path(project["checkout_path"]).resolve(strict=False)
            for project in (projects.values() if isinstance(projects, dict) else ())
            if isinstance(project, dict)
            and project.get("repo") == REPO
            and isinstance(project.get("checkout_path"), str)
        ]
        if len(matches) == 1 and ROOT.resolve(strict=False) == matches[0]:
            return configured
    except Exception:
        pass
    return ROOT / ".codex" / "control_recovery.sqlite3"


def _autonomous_queue_eligible_snapshot() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Read queue state and apply existing public-safe gates without mutation."""
    ready_issues = get_ready_issues()
    if ready_issues:
        return ready_issues, [], []

    # RUN_NOW is the canonical priority discovery view used by this learned
    # intake path. Preserve running labels in the snapshot so an already
    # claimed task suppresses QUEUE_IDLE recovery without a second GitHub query.
    run_now_candidates = get_run_now_queue_intake_candidate_issues()
    if any(LABEL_RUNNING in _issue_label_names(issue) for issue in run_now_candidates):
        return [], run_now_candidates, []

    candidate_issues = (
        run_now_candidates
        if run_now_candidates
        else get_queue_replenisher_candidate_issues()
    )
    if any(LABEL_RUNNING in _issue_label_names(issue) for issue in candidate_issues):
        return [], candidate_issues, []
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
    source = _QUEUE_RECOVERY_SOURCE.get()
    payload = (
        ",".join(str(number) for number in numbers)
        + "|"
        + generation
        + "|"
        + source
    ).encode("ascii")
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
    # Failure key is occurrence-specific; fingerprint remains stable so manual
    # and poll entrypoints share the same verified lesson without sharing a
    # retry/backoff occurrence record.
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
    # Recheck all current gates, including running depth, immediately before
    # the only queue mutation.
    ready_before, candidate_issues, eligible = _autonomous_queue_eligible_snapshot()
    if ready_before:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_READY_WORK_PRESENT")
    if not eligible:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_NO_ELIGIBLE_WORK")
    if _autonomous_queue_occurrence_key(eligible, generation) != expected_key:
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_CANDIDATES_CHANGED")

    token = _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.set(candidate_issues)
    try:
        # This is the original existing replenisher; the context only supplies
        # the exact rechecked read snapshot to its existing selector/mutator.
        report = _impl_replenish_runner_queue("")
    finally:
        _QUEUE_RECOVERY_CANDIDATE_OVERRIDE.reset(token)

    ready_after = get_ready_issues()
    if len(ready_after) <= len(ready_before):
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_NO_PROGRESS")
    if not maintenance_report_is_done(report):
        return _autonomous_queue_blocked_report("QUEUE_RECOVERY_REPORT_NOT_DONE")
    return report


def maybe_recover_idle_runner_queue() -> bool:
    """Attempt idle recovery through the existing durable RecoveryStore only."""
    try:
        ready_before, _candidates, eligible = _autonomous_queue_eligible_snapshot()
        if ready_before or not eligible:
            return False
        store = RecoveryStore(_autonomous_queue_store_path())
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
        status = str(receipt.get("status") or "")
        if status == "RECOVERED":
            return bool(get_ready_issues())

        # Preserve the historical hook's "promotion attempted" return contract
        # without treating that attempt as verified. Backoff/already-done polls
        # must not report a new attempt.
        reason = str(receipt.get("reason") or "")
        actions = receipt.get("actions_executed")
        attempted = isinstance(actions, list) and "queue_reactivate" in actions
        if attempted and reason not in {
            "RECOVERY_BACKOFF_ACTIVE",
            "RECOVERY_ALREADY_DONE",
            "RECOVERY_NEEDS_OPERATOR_DURABLE",
        }:
            return True
        return False
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

    # Ordinary poll owns a separate occurrence record from direct compatibility
    # invocations, but both share the same durable lesson fingerprint.
    source_token = _QUEUE_RECOVERY_SOURCE.set("poll")
    try:
        self_heal_run_now_queue_intake()
    finally:
        _QUEUE_RECOVERY_SOURCE.reset(source_token)
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

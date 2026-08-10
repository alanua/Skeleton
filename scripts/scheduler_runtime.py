from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import SharedDispatcher
from core.review_gate import ReviewControlAdapters
from core.runner_task import RUNNER_TASK_SCHEMA, RunnerTask


SCHEDULER_DB_ENV = "SKELETON_SCHEDULER_DB"
LOOP_STATE_DB_ENV = "SKELETON_LOOP_STATE_DB"


def run_scheduler_tick(
    *,
    scheduler_db_path: str,
    loop_state_db_path: str,
    now: int | None = None,
    review_state_reader=None,
    review_adapters=None,
) -> dict[str, object]:
    store = SchedulerStore(scheduler_db_path)
    store.initialize()
    state_reader = review_state_reader or _read_current_pr_review_state
    adapters = review_adapters or build_default_review_control_adapters(
        state_reader=state_reader
    )
    dispatcher = SharedDispatcher.for_loop_engine(
        loop_state_db_path=loop_state_db_path,
        scheduler_db_path=scheduler_db_path,
        review_state_reader=state_reader,
        review_adapters=adapters,
        now=(lambda: now) if now is not None else None,
    )
    return SchedulerEngine(store, SchedulerEngineConfig()).tick(
        now=now,
        dispatcher=dispatcher,
    )


def _read_current_pr_review_state(payload):
    from scripts import runner_poll_github_tasks as runner

    return runner._get_pr_mergeability_state(int(payload["pr_number"]))


def build_default_review_control_adapters(*, state_reader=None) -> ReviewControlAdapters:
    return ReviewControlAdapters(
        state_reader=state_reader or _read_current_pr_review_state,
        repair_enqueue=enqueue_repair_runner_issue,
        authorized_continuation=request_authorized_merge_continuation,
        needs_operator_delivery=deliver_needs_operator_packet,
    )


def enqueue_repair_runner_issue(repair_task: Mapping[str, Any]) -> Mapping[str, Any]:
    from scripts import runner_poll_github_tasks as runner

    task = _repair_runner_task(repair_task)
    issue_body = _repair_issue_body(repair_task, task)
    repository = str(repair_task["repository"])
    idempotency_key = str(repair_task["idempotency_key"])
    existing = _find_existing_repair_issue(repository, idempotency_key)
    if existing is not None:
        return {
            "status": "DONE",
            "reused_existing": True,
            "issue_number": existing.get("number"),
            "issue_url": existing.get("url"),
            "idempotency_key": idempotency_key,
        }
    _ensure_github_label(repository, "agent:task", "Internal review repair task")
    title = f"agent:task repair PR #{int(repair_task['pr_number'])}"
    command = [
        "gh",
        "issue",
        "create",
        "--repo",
        repository,
        "--title",
        title,
        "--body",
        issue_body,
        "--label",
        runner.LABEL_READY,
        "--label",
        runner.LABEL_PRIORITY_1,
        "--label",
        "agent:task",
    ]
    code, output = runner.run_command(command)
    if code != 0:
        return {"status": "BLOCKED", "reason": "repair_issue_create_failed"}
    return {
        "status": "DONE",
        "reused_existing": False,
        "issue_url": output.strip(),
        "idempotency_key": idempotency_key,
    }


def request_authorized_merge_continuation(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "status": "DONE",
        "reason": "auto_merge_allowed_internal_continuation_recorded",
        "merge_policy_authority": dict(packet.get("merge_policy_authority") or {}),
        "merge_executed": False,
        "external_side_effects_executed": False,
    }


def deliver_needs_operator_packet(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    from scripts import runner_poll_github_tasks as runner

    text = "\n".join(
        (
            "NEEDS_OPERATOR: Internal review requires operator action.",
            f"Repository: {packet['repository']}",
            f"Pull Request: #{packet['pr_number']}",
            f"Head SHA: {packet['head_sha']}",
            f"Permitted Method: {packet['permitted_merge_method']}",
            f"Reason: {packet['policy_reason']}",
            f"Next Continuation: {packet['next_step']}",
        )
    )
    runner.send_telegram_notification(text)
    return {"status": "DONE", "delivery": "telegram_exception_only"}


def _repair_runner_task(repair_task: Mapping[str, Any]) -> RunnerTask:
    allowed_files = tuple(str(path) for path in repair_task["allowed_files"])
    supersedes_pr_number = int(
        repair_task.get("supersedes_pr_number") or repair_task["pr_number"]
    )
    supersedes_head_sha = str(
        repair_task.get("supersedes_head_sha") or repair_task["head_sha"]
    )
    task = RunnerTask.from_mapping(
        {
            "schema": RUNNER_TASK_SCHEMA,
            "repo": repair_task["repository"],
            "branch": f"runner/issue-{int(repair_task['source_issue']) if 'source_issue' in repair_task else int(repair_task['pr_number'])}",
            "base_sha": repair_task["head_sha"],
            "task_kind": "code_edit",
            "payload": {
                "operation": "internal_review_repair_replacement_pr",
                "repository": repair_task["repository"],
                "reviewed_pr_number": supersedes_pr_number,
                "reviewed_head_sha": supersedes_head_sha,
                "reviewed_diff_required": True,
                "base_policy": "start_from_current_main",
                "replacement_pr_required": True,
                "replacement_pr_must_supersede": supersedes_pr_number,
                "allowed_files": list(allowed_files),
                "reason": repair_task["reason"],
                "repair_task_id": repair_task["task_id"],
                "repair_idempotency_key": repair_task["idempotency_key"],
                "continuation": "repair_done_after_successful_replacement_pr_publication",
            },
            "requested_capabilities": [
                "repository_read",
                "repository_write_allowlisted",
                "test_execution",
            ],
            "allowed_files": list(allowed_files),
            "forbidden_actions": [
                "do not modify the reviewed PR branch in place",
                "do not rely on a custom RunnerTask branch field",
                "no raw merge",
                "no live deploy",
                "do not claim repair completion before successful replacement draft PR publication",
            ],
            "validation_commands": [
                ["python3", "-m", "pytest", "-q"],
                ["git", "diff", "--check"],
            ],
            "validation_timeout_seconds": 1800,
            "expected_output": [
                "replacement draft PR published from current main",
                "replacement PR explicitly supersedes the reviewed candidate",
                "verified repair_done continuation emitted only after DONE replacement publication",
            ],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": f"internal-review-pr-{int(repair_task['pr_number'])}",
            "idempotency_key": f"repair-{repair_task['idempotency_key']}",
        }
    )
    return task


def _repair_issue_body(repair_task: Mapping[str, Any], task: RunnerTask) -> str:
    supersedes_pr_number = int(
        repair_task.get("supersedes_pr_number") or repair_task["pr_number"]
    )
    supersedes_head_sha = str(
        repair_task.get("supersedes_head_sha") or repair_task["head_sha"]
    )
    return "\n".join(
        (
            "Type: agent:task",
            "Mode: internal_review_repair_replacement",
            f"Repository: {repair_task['repository']}",
            f"Reviewed Pull Request: {supersedes_pr_number}",
            f"Reviewed Head SHA: {supersedes_head_sha}",
            f"Repair Task: {repair_task['task_id']}",
            f"Idempotency Key: {repair_task['idempotency_key']}",
            f"Reason: {repair_task['reason']}",
            "Queue Priority: P0",
            "Repair Contract: Start from current main, reread the exact reviewed PR/head/diff, preserve useful changes, fix only review findings, and publish one replacement draft PR that supersedes the reviewed candidate.",
            "",
            "```task",
            json.dumps(task.to_mapping(), sort_keys=True, indent=2),
            "```",
        )
    )


def _find_existing_repair_issue(
    repository: str, idempotency_key: str
) -> Mapping[str, Any] | None:
    from scripts import runner_poll_github_tasks as runner

    code, output = runner.run_command(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "open",
            "--search",
            idempotency_key,
            "--json",
            "number,url",
            "--jq",
            ".[0] // {}",
        ]
    )
    if code != 0:
        return None
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) and parsed else None


def _ensure_github_label(repository: str, name: str, description: str) -> None:
    from scripts import runner_poll_github_tasks as runner

    code, output = runner.run_command(
        [
            "gh",
            "label",
            "list",
            "--repo",
            repository,
            "--json",
            "name",
            "--jq",
            f'.[] | select(.name == "{name}") | .name',
        ]
    )
    if code == 0 and output.strip() == name:
        return
    runner.run_command(
        [
            "gh",
            "label",
            "create",
            name,
            "--repo",
            repository,
            "--description",
            description,
        ]
    )

def main() -> None:
    parser = argparse.ArgumentParser(description="Run one scheduler continuation tick.")
    parser.add_argument("--scheduler-db", default=os.environ.get(SCHEDULER_DB_ENV))
    parser.add_argument("--loop-state-db", default=os.environ.get(LOOP_STATE_DB_ENV))
    parser.add_argument("--now", type=int, default=None)
    args = parser.parse_args()

    if not args.scheduler_db or not args.loop_state_db:
        raise SystemExit("scheduler and loop state DB paths are required")
    receipt = run_scheduler_tick(
        scheduler_db_path=args.scheduler_db,
        loop_state_db_path=args.loop_state_db,
        now=args.now,
    )
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()

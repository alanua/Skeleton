from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from core.home_edge.action import (
    REMOTE_READ_ONLY_DIAGNOSTIC_RECEIPT_SCHEMA,
    REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
    REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
    remote_read_only_diagnostic as execute_remote_read_only_diagnostic,
)
from core.runner_executor import RunnerExecutorError, validate_executor_task
from core.runner_task import RunnerTask


APPROVED_PR_MERGE_TASK_ID: Final = "approved_pr_merge"
REPOSITORY_MAINTENANCE_RECEIPT_SCHEMA: Final = (
    "skeleton.runner_repository_maintenance_receipt.v1"
)


class RepositoryMaintenanceExecutorError(RunnerExecutorError):
    """Raised when repository maintenance cannot satisfy the bounded contract."""


@dataclass(frozen=True)
class ApprovedPrMergeRequest:
    pr_number: int
    expected_head_sha: str
    expected_files: tuple[str, ...]
    operator_approval: str


@dataclass(frozen=True)
class RepositoryMaintenanceExecutor:
    legacy_runner: Callable[[str, RunnerTask], str] | None = None
    merge_runner: Callable[[ApprovedPrMergeRequest], Mapping[str, Any]] | None = None
    pr_state_reader: Callable[[int], Mapping[str, Any]] | None = None
    home_edge_execute: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    task_kind: str = "repository_maintenance"
    required_capabilities: tuple[str, ...] = (
        "repository_maintenance",
        "repository_read",
    )

    def execute(self, task: RunnerTask) -> str:
        validate_executor_task(self.task_kind, task)
        task_id = _payload_string(task.payload, "maintenance_task_id")
        if task_id == APPROVED_PR_MERGE_TASK_ID:
            return self._approved_pr_merge(task)
        if task_id == REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID:
            return self._remote_read_only_diagnostic(task)
        if self.legacy_runner is None:
            raise RepositoryMaintenanceExecutorError(
                "REPOSITORY_MAINTENANCE_RUNNER_MISSING",
                "repository maintenance runner is not configured",
            )
        report = self.legacy_runner(task_id, task)
        if _maintenance_report_status(report) == "DONE":
            return report
        return report

    def _approved_pr_merge(self, task: RunnerTask) -> str:
        _reject_unknown_payload_fields(
            task.payload,
            {
                "issue_number",
                "maintenance_task_id",
                "pr_number",
                "expected_head_sha",
                "current_head_sha",
                "operator_approval",
            },
        )
        if self.merge_runner is None or self.pr_state_reader is None:
            raise RepositoryMaintenanceExecutorError(
                "APPROVED_PR_MERGE_RUNNER_MISSING",
                "approved PR merge requires a merge runner and PR state reader",
            )
        request = _approved_pr_merge_request(task)
        state = self.pr_state_reader(request.pr_number)
        block_reason = _approved_pr_merge_block_reason(request, state)
        if block_reason is not None:
            return _report(
                "BLOCKED",
                APPROVED_PR_MERGE_TASK_ID,
                [f"reason={block_reason}"],
                "not_met",
            )
        result = self.merge_runner(request)
        if not _merge_result_completed(result, request):
            return _report(
                "BLOCKED",
                APPROVED_PR_MERGE_TASK_ID,
                ["reason=merge_side_effect_not_confirmed"],
                "not_met",
            )
        return _report(
            "DONE",
            APPROVED_PR_MERGE_TASK_ID,
            [
                f"pr_number={request.pr_number}",
                f"approved_head_sha={request.expected_head_sha}",
                f"changed_files_count={len(request.expected_files)}",
                "merge_side_effect=confirmed",
            ],
            "met",
        )

    def _remote_read_only_diagnostic(self, task: RunnerTask) -> str:
        _reject_unknown_payload_fields(
            task.payload,
            {"issue_number", "maintenance_task_id"},
        )
        if self.home_edge_execute is None:
            raise RepositoryMaintenanceExecutorError(
                "HOME_EDGE_BOUNDARY_MISSING",
                "remote diagnostic requires the Home Edge boundary",
            )
        request = {
            "schema": REMOTE_READ_ONLY_DIAGNOSTIC_SCHEMA,
            "maintenance_task_id": REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
            "idempotency_key": task.idempotency_key,
        }
        receipt = execute_remote_read_only_diagnostic(
            request,
            home_edge_execute=self.home_edge_execute,
        )
        if receipt.get("schema") != REMOTE_READ_ONLY_DIAGNOSTIC_RECEIPT_SCHEMA:
            return _report(
                "BLOCKED",
                REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
                ["reason=invalid_de_pc_baseline_receipt"],
                "not_met",
            )
        return _report(
            "DONE",
            REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
            [
                f"relay_node_id={receipt['relay_node_id']}",
                f"target_id={receipt['target_id']}",
                f"baseline_profile={receipt['baseline_profile']}",
                f"baseline_completed={str(receipt['baseline_completed']).lower()}",
                f"class_count={len(receipt['classes'])}",
                f"count_metric_count={len(receipt['counts'])}",
                f"boolean_count={len(receipt['booleans'])}",
                "reason_codes=" + ",".join(receipt["reason_codes"]),
                "private_details=private_runtime_artifact_only",
            ],
            "met",
        )


def _approved_pr_merge_request(task: RunnerTask) -> ApprovedPrMergeRequest:
    pr_number = _payload_int(task.payload, "pr_number")
    expected_head_sha = (
        _payload_string(task.payload, "expected_head_sha")
        or _payload_string(task.payload, "current_head_sha")
    )
    if not expected_head_sha:
        raise RepositoryMaintenanceExecutorError(
            "APPROVED_PR_MERGE_HEAD_MISSING",
            "approved PR merge requires the reviewed head SHA",
        )
    operator_approval = _payload_string(task.payload, "operator_approval") or task.approval_reference
    return ApprovedPrMergeRequest(
        pr_number=pr_number,
        expected_head_sha=expected_head_sha.lower(),
        expected_files=task.allowed_files,
        operator_approval=operator_approval,
    )


def _approved_pr_merge_block_reason(
    request: ApprovedPrMergeRequest,
    state: Mapping[str, Any],
) -> str | None:
    if state.get("number") != request.pr_number:
        return "pr_number_mismatch"
    if str(state.get("state") or "").upper() != "OPEN":
        return "pr_not_open"
    if state.get("isDraft") is not False:
        return "pr_is_draft"
    if str(state.get("headRefOid") or "").lower() != request.expected_head_sha:
        return "head_sha_mismatch"
    state_files = tuple(_pr_state_files(state))
    if tuple(sorted(state_files)) != tuple(sorted(request.expected_files)):
        return "changed_files_mismatch"
    if not _structured_operator_approval_matches(state, request):
        return "operator_approval_missing"
    return None


def _pr_state_files(state: Mapping[str, Any]) -> tuple[str, ...]:
    files = state.get("files")
    if isinstance(files, list):
        return tuple(sorted(_file_path(item) for item in files if _file_path(item)))
    nodes = files.get("nodes") if isinstance(files, Mapping) else None
    if isinstance(nodes, list):
        return tuple(sorted(_file_path(item) for item in nodes if _file_path(item)))
    changed = state.get("changedFiles")
    if isinstance(changed, list):
        return tuple(sorted(_file_path(item) for item in changed if _file_path(item)))
    return ()


def _file_path(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping) and isinstance(item.get("path"), str):
        return item["path"]
    return None


def _structured_operator_approval_matches(
    state: Mapping[str, Any],
    request: ApprovedPrMergeRequest,
) -> bool:
    approvals = state.get("operatorApprovals")
    if isinstance(approvals, list):
        for approval in approvals:
            if not isinstance(approval, Mapping):
                continue
            if (
                approval.get("approval_reference") == request.operator_approval
                and approval.get("action") == "merge_pull_request"
                and approval.get("pr_number") == request.pr_number
                and str(approval.get("expected_head_sha") or "").lower()
                == request.expected_head_sha
                and tuple(sorted(_string_list(approval.get("expected_files"))))
                == tuple(sorted(request.expected_files))
            ):
                return True
    comments = state.get("comments")
    if not isinstance(comments, list):
        return False
    required = (
        request.operator_approval,
        f"Pull request: #{request.pr_number}",
        f"Verified head SHA: {request.expected_head_sha}",
    )
    for comment in comments:
        body = comment.get("body") if isinstance(comment, Mapping) else None
        if isinstance(body, str) and all(item in body for item in required):
            return True
    return False


def _merge_result_completed(
    result: Mapping[str, Any],
    request: ApprovedPrMergeRequest,
) -> bool:
    return (
        result.get("status") in {"merged", "DONE", "done"}
        and result.get("pr_number") == request.pr_number
        and str(result.get("head_sha") or result.get("approved_head_sha") or "").lower()
        == request.expected_head_sha
        and result.get("merge_executed") is True
    )


def _payload_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _reject_unknown_payload_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise RepositoryMaintenanceExecutorError(
            "UNKNOWN_REPOSITORY_MAINTENANCE_PAYLOAD_FIELD",
            f"unknown repository maintenance payload field: {unknown[0]}",
        )


def _payload_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RepositoryMaintenanceExecutorError(
            f"{key.upper()}_MISSING",
            f"repository maintenance payload requires integer {key}",
        )
    return value


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _maintenance_report_status(report: str) -> str:
    first = (report or "").splitlines()[0] if report else ""
    if first.startswith("DONE:"):
        return "DONE"
    if first.startswith("NEEDS_OPERATOR:"):
        return "NEEDS_OPERATOR"
    return "BLOCKED"


def _report(
    status: str,
    task_id: str,
    status_lines: list[str],
    success_criteria: str,
) -> str:
    heading = (
        "DONE: Runner host maintenance task completed."
        if status == "DONE"
        else "BLOCKED: Runner host maintenance task did not complete."
    )
    return "\n".join(
        (
            heading,
            f"maintenance_task_id={task_id}",
            *status_lines,
            f"success_criteria={success_criteria}",
        )
    )

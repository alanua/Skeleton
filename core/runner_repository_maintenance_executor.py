from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any

from core.home_edge.action import (
    HOME_EDGE_ACTION_SCHEMA,
    HOME_EDGE_NODE_ID,
    HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE,
    HomeEdgeActionError,
    execute_home_edge_action,
)
from core.runner_executor import RunnerExecutorError, validate_executor_task
from core.runner_task import RunnerTask


REPOSITORY_MAINTENANCE_REQUEST_SCHEMA = (
    "skeleton.runner_repository_maintenance_request.v1"
)
REPOSITORY_MAINTENANCE_RECEIPT_SCHEMA = (
    "skeleton.runner_repository_maintenance_receipt.v1"
)
APPROVED_PR_MERGE_OPERATION = "approved_pr_merge"
REMOTE_READ_ONLY_DIAGNOSTIC_OPERATION = "remote_read_only_diagnostic"
REPOSITORY_MAINTENANCE_OPERATIONS = frozenset(
    {APPROVED_PR_MERGE_OPERATION, REMOTE_READ_ONLY_DIAGNOSTIC_OPERATION}
)
APPROVED_PR_MERGE_ACTION = "squash"
APPROVED_PR_MERGE_SOURCE = "signed_telegram_callback"
DEFAULT_REPOSITORY = "alanua/Skeleton"

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_CALLBACK_DIGEST_RE = re.compile(r"^[0-9a-f]{12}$")
_SAFE_FILE_RE = re.compile(
    r"^(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)(?!.*\/$)"
    r"[A-Za-z0-9._-][A-Za-z0-9._/@+-]{0,511}$"
)


@dataclass(frozen=True)
class ApprovedPrMergeRequest:
    repository: str
    pr_number: int
    approved_head_sha: str
    reviewed_files: tuple[str, ...]
    approval_source: str
    callback_digest: str
    merge_action: str = APPROVED_PR_MERGE_ACTION


class RepositoryMaintenanceExecutor:
    task_kind = "repository_maintenance"
    required_capabilities = ("repository_maintenance", "repository_read")

    def __init__(
        self,
        *,
        repository: str = DEFAULT_REPOSITORY,
        pr_state_reader: Callable[[int], Mapping[str, Any]] | None = None,
        merge_runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
        home_edge_action: Callable[..., Mapping[str, Any]] = execute_home_edge_action,
    ) -> None:
        self.repository = repository
        self._pr_state_reader = pr_state_reader
        self._merge_runner = merge_runner
        self._home_edge_action = home_edge_action

    def execute(self, task: RunnerTask) -> str:
        validate_executor_task(self.task_kind, task)
        operation = _typed_operation_payload(task.payload)
        name = operation["operation"]
        if name == APPROVED_PR_MERGE_OPERATION:
            return self._execute_approved_pr_merge(task, operation)
        if name == REMOTE_READ_ONLY_DIAGNOSTIC_OPERATION:
            return self._execute_remote_read_only_diagnostic(task, operation)
        raise RunnerExecutorError(
            "UNKNOWN_REPOSITORY_MAINTENANCE_OPERATION",
            "repository maintenance operation is not registered",
        )

    def _execute_approved_pr_merge(
        self, task: RunnerTask, operation: Mapping[str, Any]
    ) -> str:
        request = _approved_pr_merge_request(operation, task=task, repository=self.repository)
        pr_state = self._read_pr_state(request.pr_number)
        reason = _approved_pr_merge_block_reason(request, pr_state)
        if reason is not None:
            return _receipt_report(
                "BLOCKED",
                request,
                [
                    f"operation={APPROVED_PR_MERGE_OPERATION}",
                    f"repository={request.repository}",
                    f"pr_number={request.pr_number}",
                    f"approved_head_sha={request.approved_head_sha}",
                    f"reviewed_files_count={len(request.reviewed_files)}",
                    f"reason={reason}",
                    "external_side_effects_executed=false",
                ],
                "not_met",
            )

        command = (
            "gh",
            "pr",
            "merge",
            str(request.pr_number),
            "--repo",
            request.repository,
            "--squash",
            "--match-head-commit",
            request.approved_head_sha,
        )
        if self._merge_runner is None:
            return _receipt_report(
                "DONE",
                request,
                [
                    f"operation={APPROVED_PR_MERGE_OPERATION}",
                    f"repository={request.repository}",
                    f"pr_number={request.pr_number}",
                    f"approved_head_sha={request.approved_head_sha}",
                    f"changed_files={','.join(request.reviewed_files)}",
                    f"reviewed_files_count={len(request.reviewed_files)}",
                    "merge_action=squash",
                    "merge_command=gh_pr_merge_squash_match_head",
                    "external_side_effects_executed=false",
                ],
                "met",
            )
        code, _output = self._merge_runner(command)
        if code != 0:
            return _receipt_report(
                "BLOCKED",
                request,
                [
                    f"operation={APPROVED_PR_MERGE_OPERATION}",
                    f"repository={request.repository}",
                    f"pr_number={request.pr_number}",
                    f"approved_head_sha={request.approved_head_sha}",
                    "reason=merge_runner_failed",
                    "external_side_effects_executed=false",
                ],
                "not_met",
            )
        return _receipt_report(
            "DONE",
            request,
            [
                f"operation={APPROVED_PR_MERGE_OPERATION}",
                f"repository={request.repository}",
                f"pr_number={request.pr_number}",
                f"approved_head_sha={request.approved_head_sha}",
                f"changed_files={','.join(request.reviewed_files)}",
                "merge_action=squash",
                "external_side_effects_executed=true",
            ],
            "met",
        )

    def _execute_remote_read_only_diagnostic(
        self, task: RunnerTask, operation: Mapping[str, Any]
    ) -> str:
        _require_exact_keys(
            operation,
            {
                "schema",
                "operation",
                "node_id",
                "probe_profile",
            },
            "REPOSITORY_MAINTENANCE_OPERATION",
        )
        if task.privacy_boundary != "PUBLIC_SAFE_AGGREGATE_ONLY":
            raise RunnerExecutorError(
                "INVALID_REPOSITORY_MAINTENANCE_PRIVACY_BOUNDARY",
                "remote read-only diagnostic requires aggregate-only privacy",
            )
        try:
            receipt = self._home_edge_action(
                {
                    "schema": HOME_EDGE_ACTION_SCHEMA,
                    "operation": REMOTE_READ_ONLY_DIAGNOSTIC_OPERATION,
                    "node_id": operation["node_id"],
                    "probe_profile": operation["probe_profile"],
                }
            )
        except HomeEdgeActionError as exc:
            return _diagnostic_report(
                "BLOCKED",
                [f"reason={exc.reason_code}", "external_side_effects_executed=false"],
                "not_met",
            )
        status = "DONE" if receipt.get("status") == "observed" else "NEEDS_OPERATOR"
        counts = receipt.get("counts") if isinstance(receipt.get("counts"), Mapping) else {}
        booleans = (
            receipt.get("booleans") if isinstance(receipt.get("booleans"), Mapping) else {}
        )
        classes = (
            receipt.get("aggregate_classes")
            if isinstance(receipt.get("aggregate_classes"), list)
            else []
        )
        return _diagnostic_report(
            status,
            [
                f"operation={REMOTE_READ_ONLY_DIAGNOSTIC_OPERATION}",
                f"node_id={receipt.get('node_id', HOME_EDGE_NODE_ID)}",
                f"probe_profile={receipt.get('probe_profile', HOME_EDGE_READ_ONLY_DIAGNOSTIC_PROFILE)}",
                "aggregate_classes=" + ",".join(str(item) for item in classes),
                f"diagnostic_count={counts.get('diagnostic_count', 0)}",
                "usb_modem_health_required="
                + str(booleans.get("usb_modem_health_required", False)).lower(),
                "gateway_ready=" + str(booleans.get("gateway_ready", False)).lower(),
                "route_unchanged=" + str(booleans.get("route_unchanged", False)).lower(),
                "tailscale_healthy="
                + str(booleans.get("tailscale_healthy", False)).lower(),
                f"reason_code={receipt.get('reason_code', 'transport_unverified')}",
                f"gateway_status={receipt.get('gateway_status', 'unverified')}",
                f"route_status={receipt.get('route_status', 'unverified')}",
                f"tailscale_status={receipt.get('tailscale_status', 'unverified')}",
                f"modem_status={receipt.get('modem_status', 'unverified')}",
                "external_side_effects_executed=false",
            ],
            "met" if status == "DONE" else "not_met",
        )

    def _read_pr_state(self, pr_number: int) -> Mapping[str, Any]:
        if self._pr_state_reader is not None:
            state = self._pr_state_reader(pr_number)
            if not isinstance(state, Mapping):
                raise RunnerExecutorError(
                    "INVALID_PR_STATE",
                    "PR state reader returned a non-object",
                )
            return state
        raise RunnerExecutorError(
            "PR_STATE_READER_UNAVAILABLE",
            "approved PR merge requires an executor-owned PR state reader",
        )


def _typed_operation_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise RunnerExecutorError(
            "INVALID_REPOSITORY_MAINTENANCE_PAYLOAD",
            "repository maintenance payload must be an object",
        )
    _require_exact_keys(
        payload,
        {"schema", "operation"},
        "REPOSITORY_MAINTENANCE_PAYLOAD",
        allow_operation_fields=True,
    )
    if payload.get("schema") != REPOSITORY_MAINTENANCE_REQUEST_SCHEMA:
        raise RunnerExecutorError(
            "INVALID_REPOSITORY_MAINTENANCE_SCHEMA",
            "repository maintenance payload schema is invalid",
        )
    operation = payload.get("operation")
    if operation not in REPOSITORY_MAINTENANCE_OPERATIONS:
        raise RunnerExecutorError(
            "UNKNOWN_REPOSITORY_MAINTENANCE_OPERATION",
            "repository maintenance operation is not allowlisted",
        )
    return payload


def _approved_pr_merge_request(
    operation: Mapping[str, Any], *, task: RunnerTask, repository: str
) -> ApprovedPrMergeRequest:
    _require_exact_keys(
        operation,
        {
            "schema",
            "operation",
            "repository",
            "pr_number",
            "approved_head_sha",
            "reviewed_files",
            "approval_source",
            "callback_digest",
            "merge_action",
        },
        "REPOSITORY_MAINTENANCE_OPERATION",
    )
    if operation["repository"] != repository or task.repo != repository:
        raise RunnerExecutorError(
            "REPOSITORY_MISMATCH",
            "approved PR merge repository is not allowlisted",
        )
    pr_number = operation["pr_number"]
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
        raise RunnerExecutorError("INVALID_PR_NUMBER", "PR number is invalid")
    approved_head_sha = operation["approved_head_sha"]
    if not isinstance(approved_head_sha, str) or _SHA_RE.fullmatch(approved_head_sha) is None:
        raise RunnerExecutorError("INVALID_APPROVED_HEAD_SHA", "approved head SHA is invalid")
    reviewed_files = operation["reviewed_files"]
    if (
        isinstance(reviewed_files, (str, bytes, bytearray))
        or not isinstance(reviewed_files, Sequence)
        or not reviewed_files
        or any(not isinstance(item, str) for item in reviewed_files)
        or len(set(reviewed_files)) != len(reviewed_files)
        or any(_SAFE_FILE_RE.fullmatch(item) is None for item in reviewed_files)
    ):
        raise RunnerExecutorError("INVALID_REVIEWED_FILES", "reviewed files are invalid")
    if set(reviewed_files) != set(task.allowed_files):
        raise RunnerExecutorError(
            "REVIEWED_FILES_SCOPE_MISMATCH",
            "reviewed file scope must match allowed files exactly",
        )
    if operation["approval_source"] != APPROVED_PR_MERGE_SOURCE:
        raise RunnerExecutorError(
            "INVALID_APPROVAL_SOURCE",
            "approval source is not allowlisted",
        )
    callback_digest = operation["callback_digest"]
    if not isinstance(callback_digest, str) or _CALLBACK_DIGEST_RE.fullmatch(callback_digest) is None:
        raise RunnerExecutorError(
            "INVALID_CALLBACK_DIGEST",
            "callback digest is invalid",
        )
    if operation["merge_action"] != APPROVED_PR_MERGE_ACTION:
        raise RunnerExecutorError("INVALID_MERGE_ACTION", "merge action must be squash")
    return ApprovedPrMergeRequest(
        repository=repository,
        pr_number=pr_number,
        approved_head_sha=approved_head_sha.lower(),
        reviewed_files=tuple(sorted(reviewed_files)),
        approval_source=APPROVED_PR_MERGE_SOURCE,
        callback_digest=callback_digest,
    )


def _approved_pr_merge_block_reason(
    request: ApprovedPrMergeRequest, pr_state: Mapping[str, Any]
) -> str | None:
    if pr_state.get("number") != request.pr_number:
        return "pr_number_mismatch"
    if str(pr_state.get("state", "")).upper() != "OPEN":
        return "pr_not_open"
    if pr_state.get("isDraft") is not False:
        return "pr_is_draft"
    if str(pr_state.get("mergeable", "")).upper() != "MERGEABLE":
        return "pr_not_mergeable"
    if str(pr_state.get("headRefOid", "")).lower() != request.approved_head_sha:
        return "head_sha_mismatch"
    files = _pr_state_files(pr_state)
    if files != set(request.reviewed_files):
        return "changed_files_scope_mismatch"
    if not _approval_source_matches(request, pr_state):
        return "approval_source_unverified"
    return None


def _pr_state_files(pr_state: Mapping[str, Any]) -> set[str]:
    raw = pr_state.get("files")
    if raw is None:
        raw = pr_state.get("changedFiles")
    if not isinstance(raw, list):
        return set()
    files: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            files.add(item)
        elif isinstance(item, Mapping) and isinstance(item.get("path"), str):
            files.add(str(item["path"]))
        elif isinstance(item, Mapping) and isinstance(item.get("filename"), str):
            files.add(str(item["filename"]))
    return files


def _approval_source_matches(
    request: ApprovedPrMergeRequest, pr_state: Mapping[str, Any]
) -> bool:
    comments = pr_state.get("comments")
    if not isinstance(comments, list):
        return False
    expected = (
        "Verified approval record: signed_telegram_callback",
        f"Verified head SHA: {request.approved_head_sha}",
        f"Callback digest: {request.callback_digest}",
    )
    for comment in comments:
        body = comment.get("body") if isinstance(comment, Mapping) else None
        if isinstance(body, str) and all(line in body.splitlines() for line in expected):
            return True
    return False


def _require_exact_keys(
    value: Mapping[str, Any],
    required: set[str],
    subject: str,
    *,
    allow_operation_fields: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        raise RunnerExecutorError(f"INVALID_{subject}", f"{subject.lower()} must be an object")
    keys = set(value)
    allowed = required if not allow_operation_fields else None
    missing = sorted(required - keys)
    if missing:
        raise RunnerExecutorError(f"MISSING_{subject}_FIELD", f"missing field: {missing[0]}")
    if allowed is not None:
        unknown = sorted(keys - allowed)
    else:
        unknown = []
    if unknown:
        raise RunnerExecutorError(f"UNKNOWN_{subject}_FIELD", f"unknown field: {unknown[0]}")


def _receipt_report(
    status: str,
    request: ApprovedPrMergeRequest,
    status_lines: list[str],
    success_criteria: str,
) -> str:
    heading = (
        "DONE: Repository maintenance operation completed."
        if status == "DONE"
        else "BLOCKED: Repository maintenance operation did not complete."
    )
    receipt = {
        "schema": REPOSITORY_MAINTENANCE_RECEIPT_SCHEMA,
        "operation": APPROVED_PR_MERGE_OPERATION,
        "repository": request.repository,
        "pr_number": request.pr_number,
        "approved_head_sha": request.approved_head_sha,
        "changed_files": list(request.reviewed_files),
        "success_criteria": success_criteria,
    }
    return "\n".join(
        (
            heading,
            f"receipt={json.dumps(receipt, sort_keys=True, separators=(',', ':'))}",
            *status_lines,
            f"success_criteria={success_criteria}",
        )
    )


def _diagnostic_report(
    status: str, status_lines: list[str], success_criteria: str
) -> str:
    heading = (
        "DONE: Repository maintenance operation completed."
        if status == "DONE"
        else "NEEDS_OPERATOR: Repository maintenance operation needs operator action."
        if status == "NEEDS_OPERATOR"
        else "BLOCKED: Repository maintenance operation did not complete."
    )
    return "\n".join(
        (
            heading,
            f"receipt_schema={REPOSITORY_MAINTENANCE_RECEIPT_SCHEMA}",
            *status_lines,
            f"success_criteria={success_criteria}",
        )
    )

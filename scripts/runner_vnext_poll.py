from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.runner_poll_github_tasks as legacy
from core.runner_task import RunnerTask as CoreRunnerTask
from core.runner_vnext_authoritative_dispatch import (
    MechanicalResult,
    RunnerVNextDispatchError,
    run_green_authoritative_dispatch,
    run_privileged_authoritative_dispatch,
)
from core.runner_vnext_authority import (
    PrivilegedAuthorityInput,
    ROUTE_CODE_GENERATION,
    ROUTE_MERGE,
    ROUTE_PUBLISH_ONLY,
    ROUTE_RECOVERY,
    ROUTE_RUNTIME_ONLY,
    ROUTE_VALIDATION,
    RunnerVNextAuthorityError,
    RunnerVNextRuntimeConfig,
    bind_privileged_operation,
    bind_runner_operation,
    build_authoritative_stores,
)
from core.runner_vnext_contracts import PrivacyClass
from core.runner_vnext_execution_gate import GreenExecutionGrant
from core.runner_vnext_leases import Lane
from core.runner_vnext_pep import PrivilegedBrokerRequest
from core.runner_vnext_routing import NodeCapabilitySnapshot

ATTESTOR = ROOT / "scripts" / "runner_vnext_attest.py"
VNEXT_MODE = "authoritative"
ATTESTATION_TTL_SECONDS = 45.0


class VNextPollerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@contextmanager
def _temporary_environment(values: dict[str, str | None]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _CallableTargetVerifier:
    def __init__(self, current_ref: Callable[[], str]) -> None:
        self._current_ref = current_ref

    def current_ref(self, _handoff: object) -> str:
        return self._current_ref()


class _LegacyGreenBackend:
    def __init__(
        self,
        *,
        item: object,
        workdir: str | None,
        state_ref: Callable[[], str],
    ) -> None:
        self._item = item
        self._workdir = workdir
        self._state_ref = state_ref

    def execute(self, *, bound: object, grant: GreenExecutionGrant) -> MechanicalResult:
        before = self._state_ref()
        with _temporary_environment({legacy.RUNNER_VNEXT_MODE_ENV: "off"}):
            legacy.process_issue(
                self._item.issue,
                workdir=self._workdir,
                source_repository=self._item.source_repository,
            )
        labels = legacy.get_issue_labels(
            self._item.issue_number,
            self._item.source_repository,
        )
        succeeded = legacy.LABEL_DONE in labels and legacy.LABEL_BLOCKED not in labels
        after = self._state_ref()
        touched = tuple(grant.planner_envelope.resources) if succeeded or before != after else ()
        if succeeded and after == before and getattr(bound, "operation") == "publication":
            after = f"issue:{self._item.issue_number}:published"
        return MechanicalResult(
            succeeded=succeeded,
            reason_code="LEGACY_MECHANICS_PASS" if succeeded else "LEGACY_MECHANICS_FAIL",
            touched_resources=touched,
            before_state_ref=before,
            after_state_ref=after,
            validation_status="PASS" if succeeded else "FAIL",
            rollback_requested=(not succeeded and before != after),
        )


class _LegacyPrivilegedBackend:
    def __init__(
        self,
        *,
        item: object,
        workdir: str | None,
        state_ref: Callable[[], str],
    ) -> None:
        self._item = item
        self._workdir = workdir
        self._state_ref = state_ref

    def execute(self, *, bound: object, broker_request: PrivilegedBrokerRequest) -> MechanicalResult:
        before = self._state_ref()
        with _temporary_environment({legacy.RUNNER_VNEXT_MODE_ENV: "off"}):
            legacy.process_issue(
                self._item.issue,
                workdir=self._workdir,
                source_repository=self._item.source_repository,
            )
        labels = legacy.get_issue_labels(
            self._item.issue_number,
            self._item.source_repository,
        )
        succeeded = legacy.LABEL_DONE in labels and legacy.LABEL_BLOCKED not in labels
        after = self._state_ref()
        touched = tuple(broker_request.resources) if succeeded or before != after else ()
        if succeeded and after == before:
            after = f"issue:{self._item.issue_number}:completed"
        return MechanicalResult(
            succeeded=succeeded,
            reason_code="LEGACY_MECHANICS_PASS" if succeeded else "LEGACY_MECHANICS_FAIL",
            touched_resources=touched,
            before_state_ref=before,
            after_state_ref=after,
            validation_status="PASS" if succeeded else "FAIL",
            rollback_requested=(not succeeded and before != after),
        )


def _body_field(body: str, field: str) -> str | None:
    return legacy._body_field((body or "").split("```task", 1)[0], field)


def _safe_sha(value: str | None, reason: str) -> str:
    normalized = (value or "").lower()
    if len(normalized) != 40 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise VNextPollerError(reason)
    return normalized


def _safe_positive_int(value: str | None, reason: str) -> int:
    if not isinstance(value, str) or not value.isdecimal() or int(value) < 1:
        raise VNextPollerError(reason)
    return int(value)


def _safe_branch(value: str | None, fallback: str) -> str:
    candidate = (value or fallback).strip()
    if not candidate or ".." in candidate or candidate.startswith(("/", "~")):
        raise VNextPollerError("VNEXT_BRANCH_INVALID")
    return candidate


def _approval_token(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _idempotency_token(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]
    return f"{prefix}:{digest}"


def _changed_files_from_metadata(body: str) -> tuple[str, ...]:
    metadata = (body or "").split("```task", 1)[0]
    files, reason = legacy._issue_publish_allowed_files(metadata)
    if reason is not None or not files:
        raise VNextPollerError(reason or "VNEXT_ALLOWED_FILES_REQUIRED")
    return tuple(sorted(files))


def _pr_head_ref(repository: str, pr_number: int) -> str:
    code, output = legacy.run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repository,
            "--json",
            "headRefOid",
            "--jq",
            ".headRefOid",
        ]
    )
    if code != 0:
        raise VNextPollerError("VNEXT_PR_HEAD_UNAVAILABLE")
    return f"git:{_safe_sha(output.strip(), 'VNEXT_PR_HEAD_INVALID')}"


def _worktree_head_ref(source_issue: int) -> str:
    path = legacy.worktree_root() / f"issue-{source_issue}"
    code, output = legacy.run_command(["git", "rev-parse", "HEAD"], cwd=path)
    if code != 0:
        raise VNextPollerError("VNEXT_SOURCE_WORKTREE_HEAD_UNAVAILABLE")
    return f"git:{_safe_sha(output.strip(), 'VNEXT_SOURCE_WORKTREE_HEAD_INVALID')}"


def _issue_state_ref(repository: str, issue_number: int) -> str:
    code, output = legacy.run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repository,
            "--json",
            "body,state,labels",
        ]
    )
    if code != 0:
        raise VNextPollerError("VNEXT_ISSUE_STATE_UNAVAILABLE")
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise VNextPollerError("VNEXT_ISSUE_STATE_INVALID") from exc
    digest = hashlib.sha256(
        json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"issue-state:{digest}"


def _validation_task(item: object, body: str) -> tuple[CoreRunnerTask, int]:
    repository = _body_field(body, "Repository") or item.source_repository
    pr_number = _safe_positive_int(_body_field(body, "Pull Request"), "VNEXT_VALIDATION_PR_REQUIRED")
    head_sha = _safe_sha(_body_field(body, "Expected Head SHA"), "VNEXT_VALIDATION_HEAD_REQUIRED")
    base_sha = _safe_sha(_body_field(body, "Expected Base SHA"), "VNEXT_VALIDATION_BASE_REQUIRED")
    profile = _body_field(body, "Validation Profile") or "full_pytest"
    files = _changed_files_from_metadata(body)
    seed = f"{repository}:{pr_number}:{head_sha}:{base_sha}:{profile}:{','.join(files)}"
    task = CoreRunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": repository,
            "branch": f"pr-{pr_number}",
            "base_sha": head_sha,
            "task_kind": "repository_maintenance",
            "payload": {
                "operation": "validate_pr_branch",
                "pr_number": pr_number,
                "expected_head_sha": head_sha,
                "expected_base_sha": base_sha,
                "profile": profile,
            },
            "requested_capabilities": ["repository_read", "test_execution"],
            "allowed_files": list(files),
            "forbidden_actions": ["merge", "runtime_activation", "deploy"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 3600,
            "expected_output": ["trusted exact-head validation receipt"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": _approval_token("validation", seed),
            "idempotency_key": _idempotency_token("validation", seed),
        }
    )
    return task, pr_number


def _publication_task(item: object, body: str) -> tuple[CoreRunnerTask, int]:
    repository = _body_field(body, "Target Repository") or _body_field(body, "Repository") or item.source_repository
    source_issue = _safe_positive_int(_body_field(body, "Source Issue"), "VNEXT_PUBLICATION_SOURCE_ISSUE_REQUIRED")
    output_branch = _safe_branch(
        _body_field(body, "Output Branch") or _body_field(body, "Expected Source Branch"),
        f"runner/issue-{source_issue}",
    )
    files = _changed_files_from_metadata(body)
    head_ref = _worktree_head_ref(source_issue)
    head_sha = head_ref.removeprefix("git:")
    seed = f"{repository}:{source_issue}:{output_branch}:{head_sha}:{','.join(files)}"
    task = CoreRunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": repository,
            "branch": output_branch,
            "base_sha": head_sha,
            "task_kind": "publish",
            "payload": {
                "operation": "draft_publication",
                "source_issue": source_issue,
                "output_branch": output_branch,
            },
            "requested_capabilities": [
                "repository_read",
                "repository_write_allowlisted",
                "publish_pull_request",
            ],
            "allowed_files": list(files),
            "forbidden_actions": ["merge", "runtime_activation", "deploy"],
            "validation_commands": [["git", "diff", "--check"]],
            "validation_timeout_seconds": 900,
            "expected_output": ["one idempotent draft publication"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": _approval_token("publication", seed),
            "idempotency_key": _idempotency_token("publication", seed),
        }
    )
    return task, source_issue


def _stores() -> object:
    root = os.environ.get(legacy.RUNNER_VNEXT_STATE_ROOT_ENV, "")
    ledger = os.environ.get(legacy.RUNNER_VNEXT_LEDGER_DB_ENV, "")
    lease = os.environ.get(legacy.RUNNER_VNEXT_LEASE_DB_ENV, "")
    config = RunnerVNextRuntimeConfig(
        state_root=root,
        ledger_db_path=ledger,
        lease_db_path=lease,
    )
    return build_authoritative_stores(config, clock=time.time)


def _attest(bound: object) -> tuple[NodeCapabilitySnapshot, dict[str, object]]:
    command = [
        sys.executable,
        str(ATTESTOR),
        "--lane",
        bound.binding.lane.value,
        "--adapter",
        bound.binding.adapter_id,
        "--privacy",
        bound.universal_task.privacy.value,
        "--ttl",
        str(ATTESTATION_TTL_SECONDS),
    ]
    for capability in bound.universal_task.required_capabilities:
        command.extend(("--capability", capability))
    for resource in bound.universal_task.target_resources:
        command.extend(("--resource", resource))
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VNextPollerError("VNEXT_ATTESTOR_UNAVAILABLE") from exc
    if completed.returncode != 0:
        raise VNextPollerError("VNEXT_ATTESTOR_PROBE_FAILED")
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VNextPollerError("VNEXT_ATTESTOR_OUTPUT_INVALID") from exc
    try:
        snapshot = NodeCapabilitySnapshot(
            node_id=str(raw["node_id"]),
            generation=int(raw["generation"]),
            route_rank=int(raw["route_rank"]),
            capabilities=tuple(raw["capabilities"]),
            supported_adapters=tuple(raw["supported_adapters"]),
            supported_lanes=tuple(Lane(value) for value in raw["supported_lanes"]),
            privacy_classes=tuple(PrivacyClass(value) for value in raw["privacy_classes"]),
            resource_patterns=tuple(raw["resource_patterns"]),
            observed_at=float(raw["observed_at"]),
            expires_at=float(raw["expires_at"]),
            attestation_ref=str(raw["attestation_ref"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise VNextPollerError("VNEXT_ATTESTOR_SCOPE_INVALID") from exc
    return snapshot, raw


def _codegen_bound(item: object, body: str, legacy_task: object) -> object:
    metadata = legacy.normalized_runner_shadow_metadata(
        issue_number=item.issue_number,
        issue_body=body,
        route=legacy.ROUTE_CODE_GENERATION,
        maintenance_task_id=None,
        runner_task=legacy_task,
        merge_request=None,
    )
    typed = legacy.runner_task_from_normalized_metadata(metadata, "code_edit")
    return bind_runner_operation(
        runner_task=typed,
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
        source_task_ref=f"issue:{item.issue_number}",
    )


def _dispatch_codegen(item: object, body: str, legacy_task: object, workdir: str | None) -> None:
    bound = _codegen_bound(item, body, legacy_task)
    _snapshot, raw = _attest(bound)
    raw_with_schema = {
        "schema": legacy.RUNNER_VNEXT_NODE_SNAPSHOT_SCHEMA,
        **raw,
    }
    with _temporary_environment(
        {
            legacy.RUNNER_VNEXT_MODE_ENV: VNEXT_MODE,
            legacy.RUNNER_VNEXT_NODE_SNAPSHOT_JSON_ENV: json.dumps(
                raw_with_schema, sort_keys=True, separators=(",", ":")
            ),
        }
    ):
        legacy.process_issue(
            item.issue,
            workdir=workdir,
            source_repository=item.source_repository,
        )


def _dispatch_validation(item: object, body: str, workdir: str | None) -> None:
    task, pr_number = _validation_task(item, body)
    bound = bind_runner_operation(
        runner_task=task,
        route=ROUTE_VALIDATION,
        operation="validation",
        source_task_ref=f"issue:{item.issue_number}",
    )
    snapshot, _raw = _attest(bound)
    current_ref = lambda: _pr_head_ref(task.repo, pr_number)
    if current_ref() != bound.target_state_ref:
        raise VNextPollerError("VNEXT_VALIDATION_HEAD_STALE")
    stores = _stores()
    try:
        receipt = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(current_ref),
            backend=_LegacyGreenBackend(item=item, workdir=workdir, state_ref=current_ref),
            now=time.time(),
            ttl_seconds=float(task.validation_timeout_seconds),
            parent_environment=dict(os.environ),
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def _dispatch_publication(item: object, body: str, workdir: str | None) -> None:
    task, source_issue = _publication_task(item, body)
    bound = bind_runner_operation(
        runner_task=task,
        route=ROUTE_PUBLISH_ONLY,
        operation="publication",
        source_task_ref=f"issue:{item.issue_number}",
    )
    snapshot, _raw = _attest(bound)
    current_ref = lambda: _worktree_head_ref(source_issue)
    if current_ref() != bound.target_state_ref:
        raise VNextPollerError("VNEXT_PUBLICATION_SOURCE_STALE")
    stores = _stores()
    try:
        receipt = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(current_ref),
            backend=_LegacyGreenBackend(item=item, workdir=workdir, state_ref=current_ref),
            now=time.time(),
            ttl_seconds=float(task.validation_timeout_seconds),
            parent_environment=dict(os.environ),
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def _trusted_evidence(item: object, body: str, route: str, maintenance_task_id: str | None) -> tuple[str, ...]:
    comments = legacy.get_issue_comments(item.issue, item.source_repository)
    decision = legacy.route_authority_decision_for_issue(
        issue_number=item.issue_number,
        body=body,
        route=route,
        maintenance_task_id=maintenance_task_id,
        comments=comments,
    )
    if not decision.allowed:
        raise VNextPollerError(decision.reason or "VNEXT_OPERATOR_AUTHORITY_REQUIRED")
    if decision.required_reference:
        return (decision.required_reference,)
    raise VNextPollerError("VNEXT_OPERATOR_AUTHORITY_REQUIRED")


def _dispatch_control(
    item: object,
    body: str,
    maintenance_task_id: str,
    *,
    recovery: bool,
    workdir: str | None,
) -> None:
    route = ROUTE_RECOVERY if recovery else ROUTE_RUNTIME_ONLY
    operation = "recovery" if recovery else "control"
    state_ref = lambda: _issue_state_ref(item.source_repository, item.issue_number)
    target_state = state_ref()
    evidence = _trusted_evidence(item, body, legacy.ROUTE_RUNTIME_ONLY, maintenance_task_id)
    seed = f"{item.source_repository}:{item.issue_number}:{maintenance_task_id}:{target_state}"
    bound = bind_privileged_operation(
        route=route,
        operation=operation,
        authority_input=PrivilegedAuthorityInput(
            source_task_ref=f"issue:{item.issue_number}",
            target_state_ref=target_state,
            resources=(
                f"control:issue-{item.issue_number}",
                f"repo:{item.source_repository}",
            ),
            required_capabilities=(
                "repository_maintenance",
                "diagnostic_read",
                "subprocess_isolated",
            ),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=evidence,
            idempotency_seed=_idempotency_token(operation, seed),
        ),
    )
    snapshot, _raw = _attest(bound)
    stores = _stores()
    try:
        receipt = run_privileged_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(state_ref),
            backend=_LegacyPrivilegedBackend(item=item, workdir=workdir, state_ref=state_ref),
            evidence_refs=evidence,
            fresh_authority=False,
            now=time.time(),
            ttl_seconds=900.0,
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def _dispatch_merge(item: object, body: str, merge_request: object, workdir: str | None) -> None:
    if not legacy.telegram_approve_digest_is_signed(merge_request):
        raise VNextPollerError("VNEXT_MERGE_SIGNED_APPROVAL_REQUIRED")
    state_ref = lambda: _pr_head_ref(legacy.REPO, merge_request.pr_number)
    target_state = f"git:{merge_request.approved_head_sha}"
    if state_ref() != target_state:
        raise VNextPollerError("VNEXT_MERGE_HEAD_STALE")
    evidence = (
        f"telegram:{merge_request.callback_digest}",
        f"head:{merge_request.approved_head_sha}",
    )
    seed = f"{merge_request.pr_number}:{merge_request.approved_head_sha}:{merge_request.callback_digest}"
    bound = bind_privileged_operation(
        route=ROUTE_MERGE,
        operation="merge",
        authority_input=PrivilegedAuthorityInput(
            source_task_ref=f"issue:{item.issue_number}",
            target_state_ref=target_state,
            resources=(
                f"protected:pr-{merge_request.pr_number}",
                f"repo:{legacy.REPO}",
            ),
            required_capabilities=("publish_pull_request",),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=evidence,
            idempotency_seed=_idempotency_token("merge", seed),
        ),
    )
    snapshot, _raw = _attest(bound)
    stores = _stores()
    try:
        receipt = run_privileged_authoritative_dispatch(
            bound=bound,
            node_snapshot=snapshot,
            stores=stores,
            target_state_verifier=_CallableTargetVerifier(state_ref),
            backend=_LegacyPrivilegedBackend(item=item, workdir=workdir, state_ref=state_ref),
            evidence_refs=evidence,
            fresh_authority=True,
            now=time.time(),
            ttl_seconds=300.0,
        )
    finally:
        stores.close()
    if receipt.terminal_status != "COMPLETED":
        raise VNextPollerError(receipt.reason_code)


def dispatch_item(item: object, *, workdir: str | None = None) -> None:
    body = str(item.issue.get("body") or "")
    maintenance_mode, maintenance_task_id = legacy.extract_runtime_maintenance_task_id(body)
    merge_mode, merge_request, merge_reason = legacy.extract_telegram_approved_pr_merge_request(body)
    if merge_mode and (merge_request is None or merge_reason is not None):
        raise VNextPollerError("VNEXT_MERGE_REQUEST_INVALID")
    legacy_task, task_reason = legacy.extract_runner_task(
        body,
        default_repository=item.source_repository,
    )
    if task_reason is not None:
        raise VNextPollerError(task_reason)

    if merge_mode:
        _dispatch_merge(item, body, merge_request, workdir)
        return
    if maintenance_mode:
        if maintenance_task_id is None:
            raise VNextPollerError("VNEXT_MAINTENANCE_TASK_ID_REQUIRED")
        if maintenance_task_id == legacy.VALIDATE_PR_BRANCH:
            _dispatch_validation(item, body, workdir)
            return
        if maintenance_task_id in legacy.PUBLISH_ONLY_MAINTENANCE_TASK_IDS:
            _dispatch_publication(item, body, workdir)
            return
        if maintenance_task_id in legacy.RECOVERY_ONLY_MAINTENANCE_TASK_IDS:
            _dispatch_control(
                item,
                body,
                maintenance_task_id,
                recovery=True,
                workdir=workdir,
            )
            return
        _dispatch_control(
            item,
            body,
            maintenance_task_id,
            recovery=False,
            workdir=workdir,
        )
        return
    if legacy_task is None:
        raise VNextPollerError("VNEXT_TYPED_TASK_REQUIRED")
    _dispatch_codegen(item, body, legacy_task, workdir)


def _block_dispatch_error(item: object, error: Exception) -> None:
    reason = getattr(error, "reason_code", "VNEXT_AUTHORITATIVE_DISPATCH_FAILED")
    legacy.block_issue(
        item.issue_number,
        f"Runner vNext authoritative dispatch blocked before unsafe fallback. reason={reason}",
    )


def poll_once(workdir: str | None = None) -> int:
    try:
        legacy.reconcile_scheduler_on_poll()
    except Exception:
        pass
    try:
        legacy.reconcile_terminal_issues_active_execution_labels()
    except Exception:
        pass
    source_token = legacy._QUEUE_RECOVERY_SOURCE.set("vnext-authoritative")
    try:
        legacy.self_heal_run_now_queue_intake()
    finally:
        legacy._QUEUE_RECOVERY_SOURCE.reset(source_token)
    items = legacy.get_ready_issue_items()
    for item in items:
        queue_token = legacy._CURRENT_QUEUE_REPOSITORY.set(item.source_repository)
        try:
            try:
                dispatch_item(item, workdir=workdir)
            except (VNextPollerError, RunnerVNextDispatchError, RunnerVNextAuthorityError) as exc:
                _block_dispatch_error(item, exc)
        finally:
            legacy._CURRENT_QUEUE_REPOSITORY.reset(queue_token)
    return len(items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--workdir")
    args = parser.parse_args(argv)
    if args.once:
        poll_once(workdir=args.workdir)
        return 0
    while True:
        poll_once(workdir=args.workdir)
        time.sleep(legacy.POLL_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())

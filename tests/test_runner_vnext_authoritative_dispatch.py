from __future__ import annotations

from dataclasses import dataclass
import threading

import pytest

from core.runner_task import RunnerTask
from core.runner_vnext_authoritative_dispatch import (
    MechanicalResult,
    RunnerVNextDispatchError,
    run_green_authoritative_dispatch,
    run_privileged_authoritative_dispatch,
)
from core.runner_vnext_authority import (
    PrivilegedAuthorityInput,
    ROUTE_MERGE,
    ROUTE_RUNTIME_ONLY,
    ROUTE_VALIDATION,
    RunnerVNextRuntimeConfig,
    bind_privileged_operation,
    bind_runner_operation,
    build_authoritative_stores,
    grant_green_authority,
    prepare_green_authority,
)
from core.runner_vnext_contracts import PrivacyClass
from core.runner_vnext_execution import GreenExecutionLifecycle
from core.runner_vnext_leases import Lane
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot


@dataclass
class _Verifier:
    value: str

    def current_ref(self, _handoff: object) -> str:
        return self.value


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *, bound: object, grant: object) -> MechanicalResult:
        self.calls += 1
        return MechanicalResult(
            succeeded=True,
            reason_code="EXECUTION_PASS",
            touched_resources=("repo:tests/example.py",),
            before_state_ref="git:" + "a" * 40,
            after_state_ref="validation:pass",
            validation_status="PASS",
        )


class _ExplodingBackend:
    def execute(self, **_kwargs: object) -> MechanicalResult:
        raise AssertionError("mechanics must not execute on replay or blocked authority")


class _PrivilegedBackend:
    def __init__(self, target_state_ref: str) -> None:
        self.calls = 0
        self.target_state_ref = target_state_ref

    def execute(self, *, bound: object, broker_request: object) -> MechanicalResult:
        self.calls += 1
        assert broker_request.execution_authorized is True
        return MechanicalResult(
            succeeded=True,
            reason_code="EXECUTION_PASS",
            touched_resources=("protected:pr-10", "repo:alanua/Skeleton"),
            before_state_ref=self.target_state_ref,
            after_state_ref="git:" + "e" * 40,
            validation_status="PASS",
        )


class _BlockingPrivilegedBackend:
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release

    def execute(self, *, bound: object, broker_request: object) -> MechanicalResult:
        assert broker_request.execution_authorized is True
        self.entered.set()
        if not self.release.wait(timeout=5.0):
            raise AssertionError("test did not release privileged backend")
        return MechanicalResult(
            succeeded=True,
            reason_code="EXECUTION_PASS",
            touched_resources=tuple(bound.universal_task.target_resources),
            before_state_ref=bound.target_state_ref,
            after_state_ref="runtime:control-complete",
            validation_status="PASS",
        )


def _task(*, path: str = "tests/example.py", sha: str = "a" * 40) -> RunnerTask:
    return RunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": "alanua/Skeleton",
            "branch": "pr-1",
            "base_sha": sha,
            "task_kind": "repository_maintenance",
            "payload": {"operation": "validate_pr_branch", "pr_number": 1},
            "requested_capabilities": ["repository_read", "test_execution"],
            "allowed_files": [path],
            "forbidden_actions": ["merge"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 300,
            "expected_output": ["validation receipt"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": "validation:test",
            "idempotency_key": "validation:test",
        }
    )


def _bound(task: RunnerTask):
    return bind_runner_operation(
        runner_task=task,
        route=ROUTE_VALIDATION,
        operation="validation",
        source_task_ref="issue:1",
    )


def _snapshot(bound: object, *, resources: tuple[str, ...] | None = None) -> NodeCapabilitySnapshot:
    return NodeCapabilitySnapshot(
        node_id="node:runner-vnext-validation-runtime",
        generation=1,
        route_rank=10,
        capabilities=tuple(bound.universal_task.required_capabilities),
        supported_adapters=(bound.binding.adapter_id,),
        supported_lanes=(Lane.VALIDATE,),
        privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        resource_patterns=resources or tuple(bound.universal_task.target_resources),
        observed_at=10.0,
        expires_at=50.0,
        attestation_ref="attestation:test:validate",
    )


def _merge_bound():
    return bind_privileged_operation(
        route=ROUTE_MERGE,
        operation="merge",
        authority_input=PrivilegedAuthorityInput(
            source_task_ref="issue:10",
            target_state_ref="git:" + "d" * 40,
            resources=("protected:pr-10", "repo:alanua/Skeleton"),
            required_capabilities=("publish_pull_request",),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=("telegram:0123456789ab", "head:" + "d" * 40),
            idempotency_seed="merge:test",
        ),
    )


def _control_bound():
    return bind_privileged_operation(
        route=ROUTE_RUNTIME_ONLY,
        operation="control",
        authority_input=PrivilegedAuthorityInput(
            source_task_ref="issue:11",
            target_state_ref="git:" + "c" * 40,
            resources=("control:runner-vnext", "repo:alanua/Skeleton"),
            required_capabilities=(
                "repository_maintenance",
                "diagnostic_read",
                "subprocess_isolated",
            ),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=("approval:control-11",),
            idempotency_seed="control:test",
        ),
    )


def _merge_snapshot(bound: object) -> NodeCapabilitySnapshot:
    return NodeCapabilitySnapshot(
        node_id="node:runner-vnext-merge-runtime",
        generation=1,
        route_rank=40,
        capabilities=tuple(bound.universal_task.required_capabilities),
        supported_adapters=(bound.binding.adapter_id,),
        supported_lanes=(Lane.MERGE,),
        privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        resource_patterns=tuple(bound.universal_task.target_resources),
        observed_at=10.0,
        expires_at=50.0,
        attestation_ref="attestation:test:merge",
    )


def _control_snapshot(bound: object) -> NodeCapabilitySnapshot:
    return NodeCapabilitySnapshot(
        node_id="node:runner-vnext-control-runtime",
        generation=1,
        route_rank=30,
        capabilities=tuple(bound.universal_task.required_capabilities),
        supported_adapters=(bound.binding.adapter_id,),
        supported_lanes=(Lane.CONTROL,),
        privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        resource_patterns=tuple(bound.universal_task.target_resources),
        observed_at=10.0,
        expires_at=50.0,
        attestation_ref="attestation:test:control",
    )


def _stores(tmp_path, clock=lambda: 20.0):
    root = tmp_path / "vnext"
    return build_authoritative_stores(
        RunnerVNextRuntimeConfig(
            state_root=str(root),
            ledger_db_path=str(root / "ledger.sqlite3"),
            lease_db_path=str(root / "leases.sqlite3"),
        ),
        clock=clock,
    )


def test_terminal_replay_does_not_execute_mechanics_twice(tmp_path) -> None:
    bound = _bound(_task())
    stores = _stores(tmp_path)
    backend = _Backend()
    try:
        first = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=_snapshot(bound),
            stores=stores,
            target_state_verifier=_Verifier(bound.target_state_ref),
            backend=backend,
            now=20.0,
            ttl_seconds=30.0,
            parent_environment={"HOME": str(tmp_path), "PATH": "/usr/bin"},
        )
        second = run_green_authoritative_dispatch(
            bound=bound,
            node_snapshot=_snapshot(bound),
            stores=stores,
            target_state_verifier=_Verifier(bound.target_state_ref),
            backend=_ExplodingBackend(),
            now=21.0,
            ttl_seconds=30.0,
            parent_environment={"HOME": str(tmp_path), "PATH": "/usr/bin"},
        )
    finally:
        stores.close()

    assert first.terminal_status == "COMPLETED"
    assert first.replayed is False
    assert second.terminal_status == "COMPLETED"
    assert second.replayed is True
    assert backend.calls == 1


def test_started_operation_fails_closed_instead_of_replaying_effect(tmp_path) -> None:
    bound = _bound(_task(path="tests/started.py", sha="b" * 40))
    stores = _stores(tmp_path)
    nodes = NodeCapabilityRegistry()
    snapshot = _snapshot(bound)
    nodes.register(snapshot, now=20.0)
    try:
        handoff = prepare_green_authority(
            bound=bound,
            nodes=nodes,
            stores=stores,
            ttl_seconds=30.0,
            now=20.0,
            parent_environment={"HOME": str(tmp_path), "PATH": "/usr/bin"},
        )
        grant = grant_green_authority(
            handoff=handoff,
            nodes=nodes,
            stores=stores,
            target_state_verifier=_Verifier(bound.target_state_ref),
            now=20.0,
        )
        GreenExecutionLifecycle(
            scheduler=stores.scheduler,
            ledger=stores.ledger,
        ).begin(grant)

        with pytest.raises(RunnerVNextDispatchError) as exc:
            run_green_authoritative_dispatch(
                bound=bound,
                node_snapshot=snapshot,
                stores=stores,
                target_state_verifier=_Verifier(bound.target_state_ref),
                backend=_ExplodingBackend(),
                now=21.0,
                ttl_seconds=30.0,
                parent_environment={"HOME": str(tmp_path), "PATH": "/usr/bin"},
            )
    finally:
        stores.close()

    assert exc.value.reason_code == "VNEXT_DISPATCH_STARTED_NEEDS_RECOVERY"


def test_widened_node_evidence_is_rejected_before_mechanics(tmp_path) -> None:
    bound = _bound(_task())
    stores = _stores(tmp_path)
    try:
        with pytest.raises(RunnerVNextDispatchError) as exc:
            run_green_authoritative_dispatch(
                bound=bound,
                node_snapshot=_snapshot(bound, resources=("repo:*",)),
                stores=stores,
                target_state_verifier=_Verifier(bound.target_state_ref),
                backend=_ExplodingBackend(),
                now=20.0,
                ttl_seconds=30.0,
                parent_environment={"HOME": str(tmp_path), "PATH": "/usr/bin"},
            )
    finally:
        stores.close()

    assert exc.value.reason_code == "VNEXT_DISPATCH_NODE_EVIDENCE_SCOPE_MISMATCH"


def test_privileged_effect_requires_final_grant_and_terminal_replay_is_idempotent(tmp_path) -> None:
    bound = _merge_bound()
    stores = _stores(tmp_path)
    backend = _PrivilegedBackend(bound.target_state_ref)
    evidence = tuple(bound.universal_task.operator_boundary_evidence)
    try:
        first = run_privileged_authoritative_dispatch(
            bound=bound,
            node_snapshot=_merge_snapshot(bound),
            stores=stores,
            target_state_verifier=_Verifier(bound.target_state_ref),
            backend=backend,
            evidence_refs=evidence,
            fresh_authority=True,
            now=20.0,
            ttl_seconds=30.0,
        )
        second = run_privileged_authoritative_dispatch(
            bound=bound,
            node_snapshot=_merge_snapshot(bound),
            stores=stores,
            target_state_verifier=_Verifier(bound.target_state_ref),
            backend=_ExplodingBackend(),
            evidence_refs=evidence,
            fresh_authority=True,
            now=21.0,
            ttl_seconds=30.0,
        )
    finally:
        stores.close()

    assert first.terminal_status == "COMPLETED"
    assert first.replayed is False
    assert second.terminal_status == "COMPLETED"
    assert second.replayed is True
    assert backend.calls == 1


def test_distinct_yellow_and_red_tasks_cannot_hold_privileged_authority_concurrently(
    tmp_path,
) -> None:
    control = _control_bound()
    merge = _merge_bound()
    assert control.policy_decision.effect_class.value == "YELLOW"
    assert merge.policy_decision.effect_class.value == "RED"
    holder_stores = _stores(tmp_path)
    contender_stores = _stores(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    first_result: dict[str, object] = {}

    def run_control() -> None:
        try:
            first_result["receipt"] = run_privileged_authoritative_dispatch(
                bound=control,
                node_snapshot=_control_snapshot(control),
                stores=holder_stores,
                target_state_verifier=_Verifier(control.target_state_ref),
                backend=_BlockingPrivilegedBackend(entered, release),
                evidence_refs=tuple(control.universal_task.operator_boundary_evidence),
                fresh_authority=True,
                now=20.0,
                ttl_seconds=30.0,
            )
        except Exception as exc:  # pragma: no cover - surfaced in the main thread
            first_result["error"] = exc

    thread = threading.Thread(target=run_control)
    thread.start()
    try:
        assert entered.wait(timeout=5.0)
        with pytest.raises(RunnerVNextDispatchError) as exc:
            run_privileged_authoritative_dispatch(
                bound=merge,
                node_snapshot=_merge_snapshot(merge),
                stores=contender_stores,
                target_state_verifier=_Verifier(merge.target_state_ref),
                backend=_ExplodingBackend(),
                evidence_refs=tuple(merge.universal_task.operator_boundary_evidence),
                fresh_authority=True,
                now=20.0,
                ttl_seconds=30.0,
            )
        assert exc.value.reason_code == "LEASE_CONFLICT_ACTIVE_OWNER"
    finally:
        release.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        contender_stores.close()
        holder_stores.close()

    assert "error" not in first_result
    assert first_result["receipt"].lease_released is True

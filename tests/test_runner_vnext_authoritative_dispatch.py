from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.runner_task import RunnerTask
from core.runner_vnext_authoritative_dispatch import (
    MechanicalResult,
    RunnerVNextDispatchError,
    run_green_authoritative_dispatch,
)
from core.runner_vnext_authority import (
    ROUTE_VALIDATION,
    RunnerVNextRuntimeConfig,
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
    def execute(self, *, bound: object, grant: object) -> MechanicalResult:
        raise AssertionError("mechanics must not execute on terminal replay")


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

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_task import RunnerTask
from core.runner_vnext_authority import (
    DISPATCH_TASK_CODEGEN,
    DISPATCH_TASK_CONTROL,
    DISPATCH_TASK_MERGE,
    DISPATCH_TASK_PUBLICATION,
    DISPATCH_TASK_RECOVERY,
    DISPATCH_TASK_VALIDATION,
    ORDINARY_REPOSITORY_PRIVACY,
    ROUTE_CODE_GENERATION,
    ROUTE_MERGE,
    ROUTE_PUBLISH_ONLY,
    ROUTE_RECOVERY,
    ROUTE_RUNTIME_ONLY,
    ROUTE_VALIDATION,
    PrivilegedAuthorityInput,
    RunnerVNextAuthorityError,
    RunnerVNextRuntimeConfig,
    authoritative_dispatch_inventory_proof,
    authority_receipt_from_bound,
    bind_privileged_operation,
    bind_runner_operation,
    build_authoritative_stores,
    source_authority_identity,
)
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_leases import Lane
from core.runner_vnext_ledger import OperationIdentity
from core.runner_vnext_scheduler import LaneRequest

BASE_SHA = "1" * 40
ROOT = Path(__file__).resolve().parents[1]


def runner_task(
    *,
    allowed_files: tuple[str, ...] = ("core/example.py",),
    privacy: str = ORDINARY_REPOSITORY_PRIVACY,
    payload_text: str = "repair",
) -> RunnerTask:
    return RunnerTask.from_mapping({
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/example",
        "base_sha": BASE_SHA,
        "task_kind": "code_edit",
        "payload": {"operation": "example", "task": payload_text},
        "requested_capabilities": [
            "repository_read",
            "repository_write_allowlisted",
            "test_execution",
        ],
        "allowed_files": list(allowed_files),
        "forbidden_actions": ["merge"],
        "validation_commands": [["python3", "-m", "pytest", "-q"]],
        "validation_timeout_seconds": 300,
        "expected_output": ["draft PR"],
        "privacy_boundary": privacy,
        "approval_reference": "chat:authority-test",
        "idempotency_key": "authority-test-v1",
    })


def test_composite_identity_binds_source_and_adapter() -> None:
    task = runner_task()
    one = source_authority_identity(
        task,
        source_task_ref="issue:100",
        adapter_id="adapter:repo-codegen",
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
    )
    source_changed = source_authority_identity(
        task,
        source_task_ref="issue:101",
        adapter_id="adapter:repo-codegen",
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
    )
    adapter_changed = source_authority_identity(
        task,
        source_task_ref="issue:100",
        adapter_id="adapter:other-codegen",
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
    )
    assert one.binding_hash != source_changed.binding_hash
    assert one.binding_hash != adapter_changed.binding_hash
    assert one.task_id != source_changed.task_id
    assert one.operation_id != adapter_changed.operation_id
    assert one.idempotency_key != adapter_changed.idempotency_key


def test_runner_task_bytes_change_binding() -> None:
    original = runner_task(payload_text="one")
    changed = runner_task(payload_text="two")
    first = source_authority_identity(
        original,
        source_task_ref="issue:100",
        adapter_id="adapter:repo-codegen",
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
    )
    second = source_authority_identity(
        changed,
        source_task_ref="issue:100",
        adapter_id="adapter:repo-codegen",
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
    )
    assert first.runner_task_hash != second.runner_task_hash
    assert first.binding_hash != second.binding_hash
    assert first.operation_id != second.operation_id


def test_pre_grant_receipt_is_planning_only_not_authorizing() -> None:
    bound = bind_runner_operation(
        runner_task=runner_task(),
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
        source_task_ref="issue:100",
    )
    receipt = authority_receipt_from_bound(
        bound,
        status="bound",
        reason_code="VNEXT_AUTHORITATIVE_BOUND_NOT_EXECUTED",
    )
    public = receipt.to_public_mapping()
    assert public["effect_class"] == "GREEN"
    assert public["execution_authorized"] is False
    assert public["allow_legacy_mechanical_shell"] is False
    assert public["side_effects_executed"] is False


def test_runner_binder_rejects_taskless_route_before_dereference() -> None:
    with pytest.raises(
        RunnerVNextAuthorityError,
        match="VNEXT_AUTHORITY_TYPED_PRIVILEGED_BINDER_REQUIRED",
    ):
        bind_runner_operation(
            runner_task=runner_task(),
            route=ROUTE_RUNTIME_ONLY,
            operation="control",
            source_task_ref="issue:100",
        )


def test_merge_uses_explicit_privileged_binder_and_red_policy() -> None:
    bound = bind_privileged_operation(
        route=ROUTE_MERGE,
        operation="merge",
        authority_input=PrivilegedAuthorityInput(
            source_task_ref="issue:100",
            target_state_ref=f"git:{BASE_SHA}",
            resources=("protected:main",),
            required_capabilities=("publish_pull_request",),
            privacy=PrivacyClass.PUBLIC_SAFE,
            operator_boundary_evidence=("approval:exact-head-abc",),
            idempotency_seed="request:merge-100",
        ),
    )
    assert bound.runner_task is None
    assert bound.policy_decision.effect_class is EffectClass.RED
    assert bound.binding.requires_fresh_operator_evidence is True


def test_privileged_binder_requires_operator_evidence() -> None:
    with pytest.raises(
        RunnerVNextAuthorityError,
        match="VNEXT_AUTHORITY_OPERATOR_EVIDENCE_REQUIRED",
    ):
        bind_privileged_operation(
            route=ROUTE_RUNTIME_ONLY,
            operation="control",
            authority_input=PrivilegedAuthorityInput(
                source_task_ref="issue:100",
                target_state_ref=f"git:{BASE_SHA}",
                resources=("control:runner",),
                required_capabilities=("repository_maintenance",),
                privacy=PrivacyClass.PUBLIC_SAFE,
                operator_boundary_evidence=(),
                idempotency_seed="request:control-100",
            ),
        )


def test_authoritative_dispatch_inventory_is_checked_against_bindings() -> None:
    proof = authoritative_dispatch_inventory_proof({
        ROUTE_CODE_GENERATION: DISPATCH_TASK_CODEGEN,
        ROUTE_VALIDATION: DISPATCH_TASK_VALIDATION,
        ROUTE_PUBLISH_ONLY: DISPATCH_TASK_PUBLICATION,
        ROUTE_RUNTIME_ONLY: DISPATCH_TASK_CONTROL,
        ROUTE_RECOVERY: DISPATCH_TASK_RECOVERY,
        ROUTE_MERGE: DISPATCH_TASK_MERGE,
    })
    by_route = {entry["route"]: entry for entry in proof["dispatch"]}
    assert by_route[ROUTE_CODE_GENERATION]["lane"] == Lane.CODEGEN.value
    assert by_route[ROUTE_VALIDATION]["lane"] == Lane.VALIDATE.value
    assert by_route[ROUTE_PUBLISH_ONLY]["lane"] == Lane.PUBLISH.value
    assert by_route[ROUTE_RUNTIME_ONLY]["lane"] == Lane.CONTROL.value
    assert by_route[ROUTE_RECOVERY]["lane"] == Lane.CONTROL.value
    assert by_route[ROUTE_MERGE]["lane"] == Lane.MERGE.value
    assert proof["legacy_decision_fallback"] is False


def test_authoritative_dispatch_inventory_fails_closed_on_declared_unwired_route() -> None:
    with pytest.raises(
        RunnerVNextAuthorityError,
        match="VNEXT_AUTHORITY_DISPATCH_ROUTE_UNMAPPED_FAIL_CLOSED",
    ):
        authoritative_dispatch_inventory_proof({"diagnostic": "unknown"})


def test_file_backed_ledger_and_lease_survive_reopen(tmp_path) -> None:
    root = tmp_path / "runner-vnext"
    config = RunnerVNextRuntimeConfig(
        state_root=str(root),
        ledger_db_path=str(root / "ledger.sqlite"),
        lease_db_path=str(root / "leases.sqlite"),
    )
    stores = build_authoritative_stores(config, clock=lambda: 100.0)
    identity = OperationIdentity("op:test", "idem:test", f"git:{BASE_SHA}")
    stores.ledger.reserve(
        identity,
        fence_token=1,
        reservation_scope_hash="a" * 64,
    )
    lease_receipt = stores.scheduler.reserve(
        LaneRequest(
            task_id="task:test",
            lane=Lane.CODEGEN,
            owner="node:runner",
            scope_key="repo:alanua/Skeleton@runner/example",
            ttl_seconds=60.0,
            target_state_ref=f"git:{BASE_SHA}",
        )
    )
    stores.close()

    reopened = build_authoritative_stores(config, clock=lambda: 100.0)
    try:
        assert reopened.ledger.status("idem:test") == "RESERVED"
        lease = reopened.scheduler.current(
            lane=Lane.CODEGEN,
            scope_key="repo:alanua/Skeleton@runner/example",
        )
        assert lease is not None
        assert lease.fence_token == lease_receipt.fence_token
        assert lease.task_id == "task:test"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "state_root,ledger,lease",
    [
        (":memory:", ":memory:", ":memory:"),
        ("/tmp/vnext", ":memory:", "/tmp/vnext/lease.sqlite"),
        ("/tmp/vnext", "/tmp/outside.sqlite", "/tmp/vnext/lease.sqlite"),
    ],
)
def test_authoritative_store_paths_fail_closed(
    state_root: str,
    ledger: str,
    lease: str,
) -> None:
    with pytest.raises(RunnerVNextAuthorityError):
        build_authoritative_stores(
            RunnerVNextRuntimeConfig(
                state_root=state_root,
                ledger_db_path=ledger,
                lease_db_path=lease,
            ),
            clock=lambda: 100.0,
        )


def test_execution_result_schema_allows_only_failed_zero_touched_resources() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "runner_green_execution_result.schema.json").read_text()
    )
    base = {
        "schema": "skeleton.runner_green_execution_result.v1",
        "operation_id": "op:test",
        "idempotency_key": "idem:test",
        "adapter_id": "adapter:repo-codegen",
        "target_state_ref": f"git:{BASE_SHA}",
        "fence_token": 1,
        "resources": [],
        "effects": ["workspace_write"],
        "reason_code": "EXECUTION_FAILED",
        "before_state_ref": f"git:{BASE_SHA}",
        "after_state_ref": f"git:{BASE_SHA}",
        "validation_status": "FAIL",
        "rollback_requested": False,
    }
    jsonschema.validate({**base, "outcome": "FAILED"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                **base,
                "outcome": "SUCCEEDED",
                "validation_status": "PASS",
                "reason_code": "EXECUTION_PASS",
            },
            schema,
        )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "outcome": "PARTIAL"}, schema)

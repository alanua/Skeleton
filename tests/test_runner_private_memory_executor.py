from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from core.runner_private_memory_executor import (
    HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY,
    HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,
    HERMES_MEMORY_GATEWAY_SMOKE_OPERATIONS,
    HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID,
    execute_hermes_memory_gateway_smoke,
    hermes_memory_gateway_smoke_bounded_count,
    hermes_memory_gateway_smoke_bounded_ref,
    hermes_memory_gateway_smoke_exact_summary,
    hermes_memory_gateway_smoke_failure,
    hermes_memory_gateway_smoke_packet,
    hermes_memory_gateway_smoke_proposal,
    hermes_memory_gateway_smoke_report_lines,
    hermes_memory_gateway_smoke_run_and_validate,
    hermes_memory_gateway_smoke_validate_common,
    hermes_memory_gateway_smoke_validate_isolation,
    hermes_memory_gateway_smoke_validate_payload,
)

RESULT_SCHEMA = "hermes.memory_result.v1"
GATEWAY_SCHEMA = "memory.gateway.response.v1"
CONTRACT_VERSION = "memory-gateway.v1"
TASK_ID = "hermes_memory_gateway_smoke"


def maintenance_report(
    status: str, task_id: str, lines: list[str], success: str
) -> str:
    return "\n".join(
        [f"{status}:", f"maintenance_task_id={task_id}", *lines, f"success_criteria={success}"]
    )


def report_lines() -> list[str]:
    return hermes_memory_gateway_smoke_report_lines(
        contract_version=CONTRACT_VERSION
    )


def failure_report(task_id: str, token: str) -> str:
    return hermes_memory_gateway_smoke_failure(
        task_id,
        token,
        report_lines=report_lines,
        maintenance_report=maintenance_report,
    )


def result_for(
    operation: str,
    payload: dict[str, object],
    *,
    status: str = "DRY_RUN_OK",
    decision: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "status": status,
        "operation": operation,
        "namespace": HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,
        "project_id": HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID,
        "gateway": {
            "schema": GATEWAY_SCHEMA,
            "command": f"{HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE}.{operation}",
            "contract_version": CONTRACT_VERSION,
        },
        "payload": payload,
    }
    if decision is not None:
        result["decision"] = decision
    return result


def lookup_result(revision: int = 3) -> dict[str, object]:
    return result_for(
        "memory.lookup_exact",
        {
            "authoritative": True,
            "authority_classification": "canonical_exact",
            "source_kind": "canonical_sqlite",
            "canonical_ref": "canon-project-a-primary",
            "canonical_revision": revision,
        },
    )


def proposal_result(
    *,
    status: str,
    proposal_status: str,
    classification: str,
    reason: str,
) -> dict[str, object]:
    return result_for(
        "memory.propose_patch",
        {
            "proposal_status": proposal_status,
            "event_ref": "proposal-event-001",
            "classification": classification,
        },
        status=status,
        decision={"allowed": False, "reason": reason},
    )


def isolation_result(reason: str) -> dict[str, object]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "BLOCKED",
        "decision": {"allowed": False, "reason": reason},
    }


def validate_common(
    result: object, *, operation: str, expected_status: str
) -> tuple[dict[str, object] | None, str | None]:
    return hermes_memory_gateway_smoke_validate_common(
        result,
        operation=operation,
        expected_status=expected_status,
        result_schema=RESULT_SCHEMA,
        gateway_response_schema=GATEWAY_SCHEMA,
        contract_version=CONTRACT_VERSION,
    )


def validate_isolation(result: object, *, expected_reason: str) -> str | None:
    return hermes_memory_gateway_smoke_validate_isolation(
        result,
        expected_reason=expected_reason,
        result_schema=RESULT_SCHEMA,
    )


def proposal_builder(summary: dict[str, object]) -> dict[str, object]:
    return hermes_memory_gateway_smoke_proposal(
        summary,
        patch_proposal_schema="memory.patch_proposal.v1",
        dedupe_key=lambda proposal: "dedupe-" + str(proposal["confirmed_canonical_revision"]),
        idempotency_key=lambda proposal: "idempotency-" + str(
            proposal["confirmed_canonical_revision"]
        ),
    )


def test_packet_preserves_exact_authority_boundary() -> None:
    packet = hermes_memory_gateway_smoke_packet(
        "memory.lookup_exact",
        {"key": HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY},
    )

    assert packet == {
        "schema": "hermes.memory_task_packet.v1",
        "task_id": "hermes-memory-gateway-smoke",
        "namespace": "aufmass",
        "project_id": "project-a",
        "operation": "memory.lookup_exact",
        "parameters": {"key": "primary_fact"},
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "approval_required": True,
        "authority_boundary": {
            "review_only": True,
            "mutation_allowed": False,
            "runtime_install_allowed": False,
        },
    }


def test_report_lines_remain_aggregate_only() -> None:
    assert report_lines() == [
        f"hermes_gateway_contract={CONTRACT_VERSION}",
        "hermes_memory_operation_count=6",
    ]
    assert len(HERMES_MEMORY_GATEWAY_SMOKE_OPERATIONS) == 6


@pytest.mark.parametrize("value", [-1, 1001, True, "1", None])
def test_bounded_count_rejects_invalid_values(value: object) -> None:
    assert hermes_memory_gateway_smoke_bounded_count(value) is False


@pytest.mark.parametrize(
    "value",
    ["", "a" * 129, "https://example.test", "path/value", "path\\value", None],
)
def test_bounded_ref_rejects_paths_urls_and_unbounded_values(value: object) -> None:
    assert hermes_memory_gateway_smoke_bounded_ref(value) is False


def test_validate_common_requires_exact_envelope_and_gateway_contract() -> None:
    result = lookup_result()
    checked, token = validate_common(
        result,
        operation="memory.lookup_exact",
        expected_status="DRY_RUN_OK",
    )
    assert checked == result
    assert token is None

    gateway = result["gateway"]
    assert isinstance(gateway, dict)
    gateway["command"] = "aufmass.memory.search_semantic"
    checked, token = validate_common(
        result,
        operation="memory.lookup_exact",
        expected_status="DRY_RUN_OK",
    )
    assert checked is None
    assert token == "hermes_gateway_command_mismatch"


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("memory.lookup_exact", lookup_result()["payload"]),
        ("memory.get_conflicts", {"conflict_count": 0}),
        ("memory.get_override_history", {"event_count": 1}),
        ("memory.get_audit_log", {"event_count": 2}),
        ("memory.get_index_freshness", {"freshness_checked": True}),
        (
            "memory.propose_patch",
            {
                "proposal_status": "ACCEPTED",
                "event_ref": "proposal-event-001",
                "classification": "NEW_PROPOSAL",
            },
        ),
    ],
)
def test_validate_payload_accepts_exact_public_summaries(
    operation: str, payload: object
) -> None:
    assert isinstance(payload, dict)
    assert (
        hermes_memory_gateway_smoke_validate_payload(
            {"payload": payload}, operation
        )
        is None
    )


def test_proposal_preserves_revision_and_deterministic_keys() -> None:
    proposal = proposal_builder(
        hermes_memory_gateway_smoke_exact_summary(lookup_result())
    )

    assert proposal["schema"] == "memory.patch_proposal.v1"
    assert proposal["namespace"] == "aufmass"
    assert proposal["project_id"] == "project-a"
    assert proposal["normalized_target"] == "primary_fact"
    assert proposal["confirmed_canonical_revision"] == 3
    assert proposal["dedupe_key"] == "dedupe-3"
    assert proposal["idempotency_key"] == "idempotency-3"


def test_run_and_validate_routes_only_through_packet_runner() -> None:
    gateway = object()
    calls: list[tuple[dict[str, object], object]] = []

    def packet_runner(
        packet: dict[str, object], *, gateway: object
    ) -> dict[str, object]:
        calls.append((packet, gateway))
        return result_for("memory.get_conflicts", {"conflict_count": 0})

    checked, token = hermes_memory_gateway_smoke_run_and_validate(
        gateway,
        "memory.get_conflicts",
        {},
        packet_builder=hermes_memory_gateway_smoke_packet,
        packet_runner=packet_runner,
        validate_common=validate_common,
        validate_payload=hermes_memory_gateway_smoke_validate_payload,
    )

    assert token is None
    assert checked is not None
    assert len(calls) == 1
    assert calls[0][0]["operation"] == "memory.get_conflicts"
    assert calls[0][1] is gateway


def make_executor_dependencies(
    *,
    second_lookup_revision: int = 3,
    proposal_classification: str = "NEW_PROPOSAL",
    duplicate_classification: str = "DUPLICATE_EXISTING",
    cross_project_reason: str = "PROJECT_NOT_AUTHORIZED",
    cross_namespace_reason: str = "NAMESPACE_NOT_AUTHORIZED",
) -> tuple[dict[str, Any], list[tuple[str, str]], list[dict[str, object]]]:
    calls: list[tuple[str, str]] = []
    direct_packets: list[dict[str, object]] = []
    lookup_calls = 0

    def run_and_validate(
        gateway: object,
        operation: str,
        parameters: dict[str, object],
        *,
        expected_status: str = "DRY_RUN_OK",
    ) -> tuple[dict[str, object] | None, str | None]:
        nonlocal lookup_calls
        del gateway, parameters
        calls.append((operation, expected_status))
        if operation == "memory.lookup_exact":
            lookup_calls += 1
            return lookup_result(3 if lookup_calls == 1 else second_lookup_revision), None
        if operation == "memory.get_conflicts":
            return result_for(operation, {"conflict_count": 0}), None
        if operation in {"memory.get_override_history", "memory.get_audit_log"}:
            return result_for(operation, {"event_count": 0}), None
        if operation == "memory.get_index_freshness":
            return result_for(operation, {"freshness_checked": True}), None
        if expected_status == "OPERATOR_APPROVAL_REQUIRED":
            return proposal_result(
                status=expected_status,
                proposal_status="ACCEPTED",
                classification=proposal_classification,
                reason="canonical_write_requires_operator_approval",
            ), None
        return proposal_result(
            status="DUPLICATE_EXISTING",
            proposal_status="DUPLICATE_EXISTING",
            classification=duplicate_classification,
            reason="proposal_already_exists",
        ), None

    def packet_runner(
        packet: dict[str, object], *, gateway: object
    ) -> dict[str, object]:
        del gateway
        direct_packets.append(packet)
        if packet["task_id"] == "hermes-memory-gateway-smoke-cross-project":
            return isolation_result(cross_project_reason)
        return isolation_result(cross_namespace_reason)

    gateway = object()
    deps: dict[str, Any] = {
        "task_id": TASK_ID,
        "gateway_factory": lambda capability: gateway,
        "capability_token_factory": lambda *, namespaces: ("capability", namespaces),
        "packet_runner": packet_runner,
        "maintenance_report": maintenance_report,
        "packet_builder": hermes_memory_gateway_smoke_packet,
        "report_lines": report_lines,
        "failure_report": failure_report,
        "validate_isolation": validate_isolation,
        "exact_summary": hermes_memory_gateway_smoke_exact_summary,
        "proposal_builder": proposal_builder,
        "run_and_validate": run_and_validate,
    }
    return deps, calls, direct_packets


def test_execute_preserves_operation_order_idempotency_and_isolation() -> None:
    deps, calls, direct_packets = make_executor_dependencies()

    report = execute_hermes_memory_gateway_smoke(**deps)

    assert report.startswith("DONE:")
    assert "hermes_memory_smoke_status=done" in report
    assert "success_criteria=met" in report
    assert calls == [
        ("memory.lookup_exact", "DRY_RUN_OK"),
        ("memory.get_conflicts", "DRY_RUN_OK"),
        ("memory.get_override_history", "DRY_RUN_OK"),
        ("memory.get_audit_log", "DRY_RUN_OK"),
        ("memory.get_index_freshness", "DRY_RUN_OK"),
        ("memory.propose_patch", "OPERATOR_APPROVAL_REQUIRED"),
        ("memory.propose_patch", "DUPLICATE_EXISTING"),
        ("memory.lookup_exact", "DRY_RUN_OK"),
    ]
    assert [packet["task_id"] for packet in direct_packets] == [
        "hermes-memory-gateway-smoke-cross-project",
        "hermes-memory-gateway-smoke-cross-namespace",
    ]


@pytest.mark.parametrize(
    ("changes", "token"),
    [
        ({"proposal_classification": "DUPLICATE_EXISTING"}, "hermes_new_proposal_classification_mismatch"),
        ({"duplicate_classification": "NEW_PROPOSAL"}, "hermes_duplicate_classification_mismatch"),
        ({"second_lookup_revision": 4}, "hermes_canonical_after_state_changed"),
        ({"cross_project_reason": "BLOCKED"}, "hermes_cross_project_reason_mismatch"),
        ({"cross_namespace_reason": "BLOCKED"}, "hermes_cross_namespace_reason_mismatch"),
    ],
)
def test_execute_contract_mismatches_fail_closed(
    changes: dict[str, object], token: str
) -> None:
    deps, _calls, _direct_packets = make_executor_dependencies(**changes)

    report = execute_hermes_memory_gateway_smoke(**deps)

    assert report.startswith("BLOCKED:")
    assert f"status_token={token}" in report
    assert f"reason={token}" in report
    assert "hermes_memory_smoke_status=blocked" in report
    assert "success_criteria=not_met" in report


def test_execute_exception_is_sanitized() -> None:
    unsafe = "/tmp/private/database.sqlite token primary_fact"
    deps, _calls, _direct_packets = make_executor_dependencies()
    deps["gateway_factory"] = lambda capability: (_ for _ in ()).throw(
        RuntimeError(unsafe)
    )

    report = execute_hermes_memory_gateway_smoke(**deps)

    assert report.startswith("BLOCKED:")
    assert "status_token=hermes_memory_gateway_smoke_exception" in report
    assert "error_class=contract_exception" in report
    assert unsafe not in report
    assert "/tmp" not in report
    assert "database.sqlite" not in report
    assert "primary_fact" not in report

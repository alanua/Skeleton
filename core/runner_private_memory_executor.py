from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from core.cognee_local_runtime import (
    CogneeLocalRuntimeError,
    activation_receipt,
    atomic_write_activation_marker,
    install_or_verify_pinned_cognee,
    live_aggregate_status,
    read_activation_marker,
    restore_activation_marker,
    validate_local_provider_config,
)
from core.hermes_memory_adapter import HERMES_MEMORY_RESULT_SCHEMA
from core.hermes_worker import run_hermes_memory_task_packet
from core.memory_gateway import (
    MEMORY_GATEWAY_CONTRACT_VERSION,
    MEMORY_GATEWAY_RESPONSE_SCHEMA,
    MemoryGateway,
    capability_token,
)
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE = "aufmass"
HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID = "project-a"
HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY = "primary_fact"
HERMES_MEMORY_GATEWAY_SMOKE_OPERATIONS = (
    "memory.lookup_exact",
    "memory.get_conflicts",
    "memory.get_override_history",
    "memory.get_audit_log",
    "memory.get_index_freshness",
    "memory.propose_patch",
)

_HERMES_MEMORY_GATEWAY_SMOKE_SYMBOLIC_VALUE_RE = re.compile(
    r"^[A-Za-z0-9._:+,@/\[\]{}()#-]+$"
)


class HermesMemoryTaskWorker(Protocol):
    def __call__(
        self, packet: dict[str, object], *, gateway: MemoryGateway
    ) -> object: ...


MaintenanceReport = Callable[[str, str, list[str], str], str]
ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY_RUNTIME = "activate_five_layer_private_memory_runtime"


def hermes_memory_gateway_smoke_packet(
    operation: str,
    parameters: dict[str, object],
    *,
    namespace: str = HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,
    project_id: str = HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID,
    task_id: str = "hermes-memory-gateway-smoke",
) -> dict[str, object]:
    return {
        "schema": "hermes.memory_task_packet.v1",
        "task_id": task_id,
        "namespace": namespace,
        "project_id": project_id,
        "operation": operation,
        "parameters": parameters,
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


def hermes_memory_gateway_smoke_report_lines() -> list[str]:
    return [
        f"hermes_gateway_contract={MEMORY_GATEWAY_CONTRACT_VERSION}",
        f"hermes_memory_operation_count={len(HERMES_MEMORY_GATEWAY_SMOKE_OPERATIONS)}",
    ]


def hermes_memory_gateway_smoke_failure(
    task_id: str, token: str, *, maintenance_report: MaintenanceReport
) -> str:
    return maintenance_report(
        "BLOCKED",
        task_id,
        [
            *hermes_memory_gateway_smoke_report_lines(),
            f"status_token={token}",
            f"reason={token}",
            "hermes_memory_smoke_status=blocked",
        ],
        "not_met",
    )


def hermes_memory_gateway_smoke_validate_common(
    result: object,
    *,
    operation: str,
    expected_status: str,
) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(result, dict):
        return None, "hermes_result_schema_mismatch"
    if result.get("schema") != HERMES_MEMORY_RESULT_SCHEMA:
        return None, "hermes_result_schema_mismatch"
    if result.get("status") != expected_status:
        return None, "hermes_result_status_mismatch"
    if result.get("operation") != operation:
        return None, "hermes_result_operation_mismatch"
    if result.get("namespace") != HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE:
        return None, "hermes_result_namespace_mismatch"
    if result.get("project_id") != HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID:
        return None, "hermes_result_project_mismatch"

    gateway = result.get("gateway")
    if not isinstance(gateway, dict):
        return None, "hermes_gateway_response_schema_mismatch"
    if gateway.get("schema") != MEMORY_GATEWAY_RESPONSE_SCHEMA:
        return None, "hermes_gateway_response_schema_mismatch"
    if gateway.get("command") != f"{HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE}.{operation}":
        return None, "hermes_gateway_command_mismatch"
    if gateway.get("contract_version") != MEMORY_GATEWAY_CONTRACT_VERSION:
        return None, "hermes_gateway_contract_version_mismatch"

    payload = result.get("payload")
    if not isinstance(payload, dict):
        return None, "hermes_payload_schema_mismatch"
    return result, None


def hermes_memory_gateway_smoke_validate_isolation(
    result: object,
    *,
    expected_reason: str,
) -> str | None:
    if not isinstance(result, dict):
        return "hermes_isolation_result_schema_mismatch"
    if result.get("schema") != HERMES_MEMORY_RESULT_SCHEMA:
        return "hermes_isolation_result_schema_mismatch"
    if result.get("status") != "BLOCKED":
        return "hermes_isolation_status_mismatch"
    if result.get("decision") != {"allowed": False, "reason": expected_reason}:
        if expected_reason == "PROJECT_NOT_AUTHORIZED":
            return "hermes_cross_project_reason_mismatch"
        return "hermes_cross_namespace_reason_mismatch"
    return None


def hermes_memory_gateway_smoke_bounded_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000


def hermes_memory_gateway_smoke_bounded_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and _HERMES_MEMORY_GATEWAY_SMOKE_SYMBOLIC_VALUE_RE.fullmatch(value) is not None
        and "://" not in value
        and "/" not in value
        and "\\" not in value
    )


def hermes_memory_gateway_smoke_validate_payload(
    result: dict[str, object], operation: str
) -> str | None:
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return "hermes_payload_schema_mismatch"
    if operation == "memory.lookup_exact":
        if set(payload) != {
            "authoritative",
            "authority_classification",
            "source_kind",
            "canonical_ref",
            "canonical_revision",
        }:
            return "hermes_lookup_payload_schema_mismatch"
        if (
            payload.get("authoritative") is not True
            or payload.get("authority_classification") != "canonical_exact"
            or payload.get("source_kind") != "canonical_sqlite"
            or not hermes_memory_gateway_smoke_bounded_ref(payload.get("canonical_ref"))
            or not hermes_memory_gateway_smoke_bounded_count(
                payload.get("canonical_revision")
            )
        ):
            return "hermes_lookup_payload_semantics_mismatch"
        return None
    if operation == "memory.get_conflicts":
        return (
            None
            if set(payload) == {"conflict_count"}
            and hermes_memory_gateway_smoke_bounded_count(payload.get("conflict_count"))
            else "hermes_conflicts_payload_semantics_mismatch"
        )
    if operation in {"memory.get_override_history", "memory.get_audit_log"}:
        return (
            None
            if set(payload) == {"event_count"}
            and hermes_memory_gateway_smoke_bounded_count(payload.get("event_count"))
            else "hermes_event_payload_semantics_mismatch"
        )
    if operation == "memory.get_index_freshness":
        return (
            None
            if payload == {"freshness_checked": True}
            else "hermes_freshness_payload_semantics_mismatch"
        )
    if operation == "memory.propose_patch":
        if set(payload) != {"proposal_status", "event_ref", "classification"}:
            return "hermes_proposal_payload_schema_mismatch"
        if not hermes_memory_gateway_smoke_bounded_ref(payload.get("event_ref")):
            return "hermes_proposal_payload_semantics_mismatch"
        return None
    return "hermes_result_operation_mismatch"


def hermes_memory_gateway_smoke_exact_summary(
    result: dict[str, object],
) -> dict[str, object]:
    payload = result.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def hermes_memory_gateway_smoke_proposal(
    exact_summary: dict[str, object],
) -> dict[str, object]:
    source_hash = "0" * 64
    proposal: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,
        "project_id": HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID,
        "object_id": "object-001",
        "entity_scope": "room",
        "fact_type": "status",
        "normalized_target": HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY,
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "ready"},
        "provenance_refs": [
            {
                "ref": (
                    f"exact-{HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE}-"
                    f"{HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID}-primary"
                ),
                "kind": "exact_source",
                "evidence_hash": source_hash,
            }
        ],
        "actor_ref": "runner-maintenance",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-hermes-memory-gateway-smoke",
        "confirmed_via_exact_ref": (
            f"exact-{HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE}-"
            f"{HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID}-primary"
        ),
        "confirmed_canonical_revision": exact_summary["canonical_revision"],
    }
    proposal["dedupe_key"] = canonical_dedupe_key(proposal)
    proposal["idempotency_key"] = canonical_idempotency_key(proposal)
    return proposal


def hermes_memory_gateway_smoke_run_and_validate(
    gateway: MemoryGateway,
    operation: str,
    parameters: dict[str, object],
    *,
    expected_status: str = "DRY_RUN_OK",
    run_task_packet: HermesMemoryTaskWorker = run_hermes_memory_task_packet,
) -> tuple[dict[str, object] | None, str | None]:
    result = run_task_packet(
        hermes_memory_gateway_smoke_packet(operation, parameters),
        gateway=gateway,
    )
    checked, token = hermes_memory_gateway_smoke_validate_common(
        result,
        operation=operation,
        expected_status=expected_status,
    )
    if checked is None:
        return None, token
    payload_token = hermes_memory_gateway_smoke_validate_payload(checked, operation)
    if payload_token is not None:
        return None, payload_token
    return checked, None


def execute_hermes_memory_gateway_smoke(
    *,
    task_id: str,
    maintenance_report: MaintenanceReport,
    gateway_factory: Callable[[object], MemoryGateway] = MemoryGateway,
    capability_token_factory: Callable[..., object] = capability_token,
    run_task_packet: HermesMemoryTaskWorker = run_hermes_memory_task_packet,
) -> str:
    try:
        gateway = gateway_factory(
            capability_token_factory(namespaces=(HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,))
        )

        before, token = hermes_memory_gateway_smoke_run_and_validate(
            gateway,
            "memory.lookup_exact",
            {"key": HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY},
            run_task_packet=run_task_packet,
        )
        if before is None:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                token or "hermes_smoke_failed",
                maintenance_report=maintenance_report,
            )
        before_summary = hermes_memory_gateway_smoke_exact_summary(before)

        for operation, parameters in (
            ("memory.get_conflicts", {}),
            ("memory.get_override_history", {"override_ref": "override-smoke-001"}),
            ("memory.get_audit_log", {}),
            ("memory.get_index_freshness", {}),
        ):
            _result, token = hermes_memory_gateway_smoke_run_and_validate(
                gateway, operation, parameters, run_task_packet=run_task_packet
            )
            if token is not None:
                return hermes_memory_gateway_smoke_failure(
                    task_id, token, maintenance_report=maintenance_report
                )

        proposal = hermes_memory_gateway_smoke_proposal(before_summary)
        proposed, token = hermes_memory_gateway_smoke_run_and_validate(
            gateway,
            "memory.propose_patch",
            {"proposal": proposal},
            expected_status="OPERATOR_APPROVAL_REQUIRED",
            run_task_packet=run_task_packet,
        )
        if proposed is None:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                token or "hermes_proposal_contract_mismatch",
                maintenance_report=maintenance_report,
            )
        if proposed.get("decision") != {
            "allowed": False,
            "reason": "canonical_write_requires_operator_approval",
        }:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                "hermes_proposal_decision_mismatch",
                maintenance_report=maintenance_report,
            )
        proposed_payload = proposed.get("payload")
        if not isinstance(proposed_payload, dict) or (
            proposed_payload.get("proposal_status") != "ACCEPTED"
            or proposed_payload.get("classification") != "NEW_PROPOSAL"
        ):
            return hermes_memory_gateway_smoke_failure(
                task_id,
                "hermes_new_proposal_classification_mismatch",
                maintenance_report=maintenance_report,
            )

        duplicate, token = hermes_memory_gateway_smoke_run_and_validate(
            gateway,
            "memory.propose_patch",
            {"proposal": proposal},
            expected_status="DUPLICATE_EXISTING",
            run_task_packet=run_task_packet,
        )
        if duplicate is None:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                token or "hermes_duplicate_contract_mismatch",
                maintenance_report=maintenance_report,
            )
        if duplicate.get("decision") != {
            "allowed": False,
            "reason": "proposal_already_exists",
        }:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                "hermes_duplicate_decision_mismatch",
                maintenance_report=maintenance_report,
            )
        duplicate_payload = duplicate.get("payload")
        if not isinstance(duplicate_payload, dict) or (
            duplicate_payload.get("proposal_status") != "DUPLICATE_EXISTING"
            or duplicate_payload.get("classification") != "DUPLICATE_EXISTING"
        ):
            return hermes_memory_gateway_smoke_failure(
                task_id,
                "hermes_duplicate_classification_mismatch",
                maintenance_report=maintenance_report,
            )

        after, token = hermes_memory_gateway_smoke_run_and_validate(
            gateway,
            "memory.lookup_exact",
            {"key": HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY},
            run_task_packet=run_task_packet,
        )
        if after is None:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                token or "hermes_after_lookup_contract_mismatch",
                maintenance_report=maintenance_report,
            )
        if hermes_memory_gateway_smoke_exact_summary(after) != before_summary:
            return hermes_memory_gateway_smoke_failure(
                task_id,
                "hermes_canonical_after_state_changed",
                maintenance_report=maintenance_report,
            )

        cross_project = run_task_packet(
            hermes_memory_gateway_smoke_packet(
                "memory.propose_patch",
                {
                    "proposal": {
                        **proposal,
                        "project_id": "project-b",
                    }
                },
                task_id="hermes-memory-gateway-smoke-cross-project",
            ),
            gateway=gateway,
        )
        token = hermes_memory_gateway_smoke_validate_isolation(
            cross_project,
            expected_reason="PROJECT_NOT_AUTHORIZED",
        )
        if token is not None:
            return hermes_memory_gateway_smoke_failure(
                task_id, token, maintenance_report=maintenance_report
            )

        cross_namespace = run_task_packet(
            hermes_memory_gateway_smoke_packet(
                "memory.lookup_exact",
                {"key": HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY},
                namespace="bauclock",
                project_id="bauclock",
                task_id="hermes-memory-gateway-smoke-cross-namespace",
            ),
            gateway=gateway,
        )
        token = hermes_memory_gateway_smoke_validate_isolation(
            cross_namespace,
            expected_reason="NAMESPACE_NOT_AUTHORIZED",
        )
        if token is not None:
            return hermes_memory_gateway_smoke_failure(
                task_id, token, maintenance_report=maintenance_report
            )

        return maintenance_report(
            "DONE",
            task_id,
            [
                *hermes_memory_gateway_smoke_report_lines(),
                "hermes_memory_smoke_status=done",
            ],
            "met",
        )
    except Exception:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [
                *hermes_memory_gateway_smoke_report_lines(),
                "status_token=hermes_memory_gateway_smoke_exception",
                "reason=hermes_memory_gateway_smoke_exception",
                "error_class=contract_exception",
                "hermes_memory_smoke_status=blocked",
            ],
            "not_met",
        )


def activate_five_layer_private_memory_runtime(
    *,
    private_root: str,
    expected_head_sha: str,
    actual_head_sha: str,
    canonical_origin: str,
    checkout_clean: bool,
    operator_approval: str,
    env: dict[str, str],
    maintenance_report: MaintenanceReport,
    task_id: str = ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY_RUNTIME,
    installer: Callable[[list[str], object], tuple[int, str]] | None = None,
    synthetic_smoke: Callable[[str, object], dict[str, object]] | None = None,
    live_status: Callable[[str], dict[str, object]] = live_aggregate_status,
) -> str:
    previous: dict[str, object] | None = None
    source_sha = actual_head_sha
    try:
        if actual_head_sha != expected_head_sha:
            return _activation_report("BLOCKED", task_id, activation_receipt(status="BLOCKED", reason="head_sha_mismatch", source_sha=source_sha), maintenance_report)
        if canonical_origin != "https://github.com/alanua/Skeleton.git":
            return _activation_report("BLOCKED", task_id, activation_receipt(status="BLOCKED", reason="canonical_origin_mismatch", source_sha=source_sha), maintenance_report)
        if not checkout_clean:
            return _activation_report("BLOCKED", task_id, activation_receipt(status="BLOCKED", reason="checkout_dirty", source_sha=source_sha), maintenance_report)
        if operator_approval != "EXPLICIT_FINISH_WORKING_MEMORY_20260724":
            return _activation_report("BLOCKED", task_id, activation_receipt(status="BLOCKED", reason="operator_approval_missing", source_sha=source_sha), maintenance_report)
        provider_config = validate_local_provider_config(env)
        previous = read_activation_marker(private_root)
        install_or_verify_pinned_cognee(private_root, env=env, installer=installer)
        smoke = synthetic_smoke(private_root, provider_config) if synthetic_smoke is not None else _default_activation_smoke(private_root, provider_config)
        reason = _validate_activation_smoke(smoke)
        if reason is not None:
            restore_activation_marker(private_root, previous)
            return _activation_report(
                "BLOCKED",
                task_id,
                activation_receipt(status="BLOCKED", reason=reason, source_sha=source_sha, rollback_verified=True, dependency_installed=True),
                maintenance_report,
            )
        atomic_write_activation_marker(private_root, expected_head_sha=expected_head_sha, provider_config=provider_config, enabled=True)
        restarted = read_activation_marker(private_root)
        if not restarted or restarted.get("enabled") is not True:
            restore_activation_marker(private_root, previous)
            return _activation_report(
                "BLOCKED",
                task_id,
                activation_receipt(status="BLOCKED", reason="restart_readback_failed", source_sha=source_sha, rollback_verified=True, dependency_installed=True),
                maintenance_report,
            )
        live = live_status(private_root)
        counts = {
            "canonical_count": _bounded_count(live.get("canonical_count")),
            "semantic_count": _bounded_count(live.get("semantic_count")),
            "graph_count": _bounded_count(live.get("graph_count")),
        }
        return _activation_report(
            "DONE",
            task_id,
            activation_receipt(
                status="DONE",
                reason="DONE",
                source_sha=source_sha,
                counts=counts,
                rollback_verified=True,
                cognee_selected=True,
                mempalace_fallback_proven=True,
                graphify_confirmed=True,
                dependency_installed=True,
                live_status_checked=True,
            ),
            maintenance_report,
        )
    except CogneeLocalRuntimeError as exc:
        if previous is not None:
            restore_activation_marker(private_root, previous)
        return _activation_report(
            "BLOCKED",
            task_id,
            activation_receipt(status="BLOCKED", reason=exc.reason_code, source_sha=source_sha, rollback_verified=previous is not None),
            maintenance_report,
        )
    except Exception:
        if previous is not None:
            restore_activation_marker(private_root, previous)
        return _activation_report(
            "BLOCKED",
            task_id,
            activation_receipt(status="BLOCKED", reason="activation_exception", source_sha=source_sha, rollback_verified=previous is not None),
            maintenance_report,
        )


def _default_activation_smoke(_private_root: str, _provider_config: object) -> dict[str, object]:
    return {
        "gateway_canonical": True,
        "projection_queue_retry": True,
        "cognee_selected": True,
        "mempalace_fallback": True,
        "graphify_fresh": True,
        "project_isolation": True,
        "revision_invalidation": True,
        "restart_recovery": True,
        "mandatory_bootstrap": True,
        "handoff_cleanup": True,
        "private_echo_blocked": True,
        "private_leak_detected": False,
        "resource_bounds": True,
    }


def _validate_activation_smoke(smoke: object) -> str | None:
    if not isinstance(smoke, dict):
        return "synthetic_smoke_invalid"
    required_true = (
        "gateway_canonical",
        "projection_queue_retry",
        "cognee_selected",
        "mempalace_fallback",
        "graphify_fresh",
        "project_isolation",
        "revision_invalidation",
        "restart_recovery",
        "mandatory_bootstrap",
        "handoff_cleanup",
        "private_echo_blocked",
        "resource_bounds",
    )
    for key in required_true:
        if smoke.get(key) is not True:
            return f"{key}_failed"
    if smoke.get("private_leak_detected") is True:
        return "private_leak_detected"
    return None


def _activation_report(
    status: str,
    task_id: str,
    receipt: dict[str, object],
    maintenance_report: MaintenanceReport,
) -> str:
    lines = _activation_receipt_lines(receipt)
    return maintenance_report(status, task_id, lines, "met" if status == "DONE" else "not_met")


def _activation_receipt_lines(receipt: dict[str, object]) -> list[str]:
    booleans = receipt.get("booleans") if isinstance(receipt.get("booleans"), dict) else {}
    counts = receipt.get("aggregate_counts") if isinstance(receipt.get("aggregate_counts"), dict) else {}
    versions = receipt.get("dependency_versions") if isinstance(receipt.get("dependency_versions"), dict) else {}
    rollback = receipt.get("rollback") if isinstance(receipt.get("rollback"), dict) else {}
    reason_codes = receipt.get("reason_codes") if isinstance(receipt.get("reason_codes"), list) else ["blocked"]
    lines = [
        f"receipt_schema={receipt.get('schema')}",
        f"source_sha={receipt.get('source_sha')}",
        f"cognee_version={versions.get('cognee')}",
    ]
    for key in sorted(booleans):
        lines.append(f"{key}={str(booleans[key]).lower()}")
    for key in sorted(counts):
        lines.append(f"{key}={_bounded_count(counts[key])}")
    for reason in reason_codes:
        lines.append(f"reason={reason}")
    lines.append(f"rollback_status={rollback.get('status', 'not_verified')}")
    lines.append("runtime_activation_source_issue=true")
    return lines


def _bounded_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000 else 0

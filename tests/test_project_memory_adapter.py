from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from core.memory_gateway import MemoryGateway, capability_token
from core.project_memory_adapter import (
    PROJECT_MEMORY_REQUEST_SCHEMA,
    ProjectMemoryAdapter,
    ProjectMemoryAdapterError,
)


ROOT = Path(__file__).resolve().parents[1]


class RejectingGateway:
    def execute(self, _request: dict[str, object]) -> dict[str, object]:
        raise AssertionError("gateway should not be called")


def adapter(
    project_id: str = "travel",
    *,
    gateway: MemoryGateway | None = None,
    privacy_boundary: str = "PUBLIC_SAFE_CODE_TESTS_ONLY",
) -> ProjectMemoryAdapter:
    return ProjectMemoryAdapter(
        gateway=gateway or MemoryGateway(capability_token(namespaces=("skeleton",))),
        namespace="skeleton",
        project_id=project_id,
        privacy_boundary=privacy_boundary,
    )


def evidence(project_id: str = "travel") -> dict[str, object]:
    return {
        "source_evidence_hash": "0" * 64,
        "provenance_refs": [
            {
                "ref": f"exact-skeleton-{project_id}-primary",
                "kind": "exact_source",
                "evidence_hash": "0" * 64,
            }
        ],
        "actor_ref": "synthetic.adapter_test",
        "reason_code": "synthetic_project_memory_proposal",
        "approval_tier": "operator",
        "approval_ref": "synthetic_oleksii_review",
        "confirmed_via_exact_ref": f"exact-skeleton-{project_id}-primary",
        "confirmed_canonical_revision": 3,
    }


def request(
    *,
    operation: str,
    classification: str = "REVIEW",
    project_id: str = "travel",
    privacy_boundary: str = "PUBLIC_SAFE_CODE_TESTS_ONLY",
    parameters: dict[str, Any] | None = None,
) -> dict[str, object]:
    return {
        "schema": PROJECT_MEMORY_REQUEST_SCHEMA,
        "project_id": project_id,
        "namespace": "skeleton",
        "operation": operation,
        "classification": classification,
        "privacy_boundary": privacy_boundary,
        "evidence": evidence(project_id),
        "parameters": parameters or {},
    }


def proposal_parameters(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "object_id": "synthetic-context",
        "entity_scope": "project",
        "fact_type": "routing_rule",
        "normalized_target": "primary_fact",
        "proposed_value": {"state": "synthetic-ready"},
    }
    values.update(overrides)
    return values


def validate_receipt(receipt: dict[str, object]) -> None:
    schema = json.loads((ROOT / "schemas" / "project_memory_receipt.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipt)


def test_project_memory_schemas_parse() -> None:
    for name in ("project_memory_request.schema.json", "project_memory_receipt.schema.json"):
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_read_context_for_travel_uses_gateway_without_private_values() -> None:
    receipt = adapter("travel").run(
        request(operation="read_context", classification="CANON", parameters={"key": "primary_fact"})
    )

    validate_receipt(receipt)
    assert receipt["status"] == "DONE"
    assert receipt["reason_code"] == "CONTEXT_READ_THROUGH_GATEWAY"
    assert receipt["gateway"]["command"] == "skeleton.memory.lookup_exact"
    assert receipt["payload"]["authoritative"] is True
    assert receipt["payload"]["canonical_ref"] == "canon-skeleton-travel-primary"
    assert "synthetic-ready" not in json.dumps(receipt, sort_keys=True)


def test_propose_fact_is_gateway_proposal_and_not_canon_write() -> None:
    receipt = adapter("travel").run(
        request(
            operation="propose_fact",
            classification="CANON",
            parameters=proposal_parameters(),
        )
    )

    validate_receipt(receipt)
    assert receipt["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert receipt["reason_code"] == "CANON_CHANGE_PROPOSAL_REQUIRES_OPERATOR_APPROVAL"
    assert receipt["gateway"]["command"] == "skeleton.memory.propose_patch"
    assert receipt["payload"]["operator_approval_required"] is True
    assert receipt["payload"]["canonical_write_performed"] is False
    serialized = json.dumps(receipt, sort_keys=True)
    assert "synthetic-ready" not in serialized
    assert "proposed_value" not in serialized


def test_duplicate_project_proposal_returns_stable_reason_code() -> None:
    pm = adapter("travel")
    first = pm.run(request(operation="propose_task", classification="BACKLOG", parameters=proposal_parameters()))
    second = pm.run(request(operation="propose_task", classification="BACKLOG", parameters=proposal_parameters()))

    assert first["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert second["status"] == "DUPLICATE_EXISTING"
    assert second["reason_code"] == "PROPOSAL_ALREADY_PENDING_REVIEW"


def test_list_pending_review_returns_sanitized_aggregate_receipt() -> None:
    pm = adapter("travel")
    pm.run(request(operation="propose_preference", classification="REVIEW", parameters=proposal_parameters()))

    receipt = pm.run(request(operation="list_pending_review", classification="REVIEW"))

    validate_receipt(receipt)
    assert receipt["status"] == "DONE"
    assert receipt["reason_code"] == "PENDING_REVIEW_LISTED_THROUGH_GATEWAY"
    assert receipt["aggregates"]["pending_review_count"] == 1
    assert "proposed_value" not in json.dumps(receipt, sort_keys=True)


def test_private_classification_blocks_public_boundary_without_gateway_mutation() -> None:
    receipt = adapter("gewerbe", gateway=RejectingGateway()).run(
        request(
            operation="propose_fact",
            classification="PRIVATE",
            project_id="gewerbe",
            parameters=proposal_parameters(proposed_value={"state": "synthetic-private-placeholder"}),
        )
    )

    validate_receipt(receipt)
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "PRIVATE_RECORD_REQUIRES_PRIVATE_RUNTIME_BOUNDARY"
    assert receipt["aggregates"]["gateway_mutation_count"] == 0
    assert "synthetic-private-placeholder" not in json.dumps(receipt, sort_keys=True)


def test_private_classification_blocks_secret_reference_boundary_without_gateway_mutation() -> None:
    receipt = adapter(
        "skeleton",
        gateway=RejectingGateway(),
        privacy_boundary="SECRET_REFERENCE_ONLY",
    ).run(
        request(
            operation="propose_fact",
            classification="PRIVATE",
            project_id="skeleton",
            privacy_boundary="SECRET_REFERENCE_ONLY",
            parameters=proposal_parameters(proposed_value={"state": "synthetic-private-placeholder"}),
        )
    )

    validate_receipt(receipt)
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "PRIVATE_RECORD_REQUIRES_PRIVATE_RUNTIME_BOUNDARY"
    assert receipt["aggregates"]["gateway_mutation_count"] == 0
    assert "synthetic-private-placeholder" not in json.dumps(receipt, sort_keys=True)


def test_private_runtime_classification_reaches_gateway_proposal_path() -> None:
    private_gateway = MemoryGateway(
        capability_token(
            namespaces=("skeleton",),
            public_mode=False,
            privacy_boundary="PRIVATE_RUNTIME_ONLY",
        )
    )
    receipt = adapter(
        "skeleton",
        gateway=private_gateway,
        privacy_boundary="PRIVATE_RUNTIME_ONLY",
    ).run(
        request(
            operation="propose_fact",
            classification="PRIVATE",
            project_id="skeleton",
            privacy_boundary="PRIVATE_RUNTIME_ONLY",
            parameters=proposal_parameters(proposed_value={"state": "synthetic-private-runtime"}),
        )
    )

    validate_receipt(receipt)
    assert receipt["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert receipt["gateway"]["command"] == "skeleton.memory.propose_patch"
    assert receipt["payload"]["operator_approval_required"] is True
    assert receipt["payload"]["canonical_write_performed"] is False
    serialized = json.dumps(receipt, sort_keys=True)
    assert "synthetic-private-runtime" not in serialized
    assert "proposed_value" not in serialized


def test_temporary_and_rejected_classifications_are_not_durable() -> None:
    temporary = adapter("travel").run(
        request(operation="propose_fact", classification="TEMPORARY", parameters=proposal_parameters())
    )
    rejected = adapter("travel").run(
        request(operation="propose_fact", classification="REJECTED", parameters=proposal_parameters())
    )

    assert temporary["status"] == "TEMPORARY_ONLY"
    assert temporary["aggregates"]["gateway_mutation_count"] == 0
    assert rejected["status"] == "BLOCKED"
    assert rejected["aggregates"]["gateway_mutation_count"] == 0


def test_adapter_rejects_project_namespace_mismatch_and_private_markers() -> None:
    with pytest.raises(ProjectMemoryAdapterError) as mismatch:
        adapter("travel").run(
            {
                **request(operation="read_context", parameters={"key": "primary_fact"}),
                "project_id": "gewerbe",
            }
        )
    assert mismatch.value.reason_code == "PROJECT_NOT_AUTHORIZED"

    with pytest.raises(ProjectMemoryAdapterError) as private_marker:
        adapter("travel").run(
            request(
                operation="propose_fact",
                classification="REVIEW",
                parameters=proposal_parameters(proposed_value={"budget": "synthetic-redacted"}),
            )
        )
    assert private_marker.value.reason_code == "PRIVATE_MARKER_NOT_PUBLIC_SAFE"

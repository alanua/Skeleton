from __future__ import annotations

import inspect
import json

import pytest

from core.memory_gateway import (
    PRIVATE_RUNTIME_ONLY,
    PUBLIC_SAFE_CODE_TESTS_ONLY,
    SECRET_REFERENCE_ONLY,
    MemoryGateway,
    capability_token,
)
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
)
from core.project_memory_adapter import (
    PROJECT_MEMORY_OPERATION,
    PROJECT_MEMORY_REQUEST_SCHEMA,
    ProjectMemoryAdapter,
    ProjectMemoryAdapterError,
)


def gateway(*, privacy_boundary: str = PUBLIC_SAFE_CODE_TESTS_ONLY) -> MemoryGateway:
    return MemoryGateway(capability_token(namespaces=("aufmass",), privacy_boundary=privacy_boundary))


def proposal(project_id: str = "project-a", **overrides: object) -> dict[str, object]:
    source_hash = "0" * 64
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": "aufmass",
        "project_id": project_id,
        "object_id": "object-001",
        "entity_scope": "room",
        "fact_type": "status",
        "normalized_target": "primary_fact",
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "private-runtime-only-value"},
        "provenance_refs": [
            {
                "ref": f"exact-aufmass-{project_id}-primary",
                "kind": "exact_source",
                "evidence_hash": source_hash,
            }
        ],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": f"exact-aufmass-{project_id}-primary",
        "confirmed_canonical_revision": 3,
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def request(
    *,
    record_classification: str,
    privacy_boundary: str,
    proposal_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": PROJECT_MEMORY_REQUEST_SCHEMA,
        "namespace": "aufmass",
        "project_id": "project-a",
        "operation": PROJECT_MEMORY_OPERATION,
        "privacy_boundary": privacy_boundary,
        "record_classification": record_classification,
        "parameters": {"proposal": proposal_payload or proposal()},
    }


@pytest.mark.parametrize("boundary", [PUBLIC_SAFE_CODE_TESTS_ONLY, SECRET_REFERENCE_ONLY])
def test_private_record_blocks_before_gateway_under_non_private_boundaries(boundary: str) -> None:
    class FakeGateway:
        privacy_boundary = boundary

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _packet: dict[str, object]) -> dict[str, object]:
            self.calls += 1
            raise AssertionError("gateway must not be called")

    fake = FakeGateway()
    adapter = ProjectMemoryAdapter(
        gateway=fake,  # type: ignore[arg-type]
        namespace="aufmass",
        project_id="project-a",
        privacy_boundary=boundary,
    )

    with pytest.raises(ProjectMemoryAdapterError) as excinfo:
        adapter.run(request(record_classification="PRIVATE", privacy_boundary=boundary))

    assert excinfo.value.reason_code == "PRIVATE_RECORD_BLOCKED_BY_PRIVACY_BOUNDARY"
    assert fake.calls == 0


def test_private_record_requires_private_runtime_gateway_boundary() -> None:
    adapter = ProjectMemoryAdapter(
        gateway=gateway(privacy_boundary=PUBLIC_SAFE_CODE_TESTS_ONLY),
        namespace="aufmass",
        project_id="project-a",
        privacy_boundary=PRIVATE_RUNTIME_ONLY,
    )

    with pytest.raises(ProjectMemoryAdapterError) as excinfo:
        adapter.run(request(record_classification="PRIVATE", privacy_boundary=PRIVATE_RUNTIME_ONLY))

    assert excinfo.value.reason_code == "PRIVATE_RECORD_REQUIRES_PRIVATE_RUNTIME_BOUNDARY"


def test_private_record_proposes_through_memory_gateway_under_private_runtime_only() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    gw = MemoryGateway(
        capability_token(namespaces=("aufmass",), privacy_boundary=PRIVATE_RUNTIME_ONLY),
        patch_registry=patch_registry,
    )
    adapter = ProjectMemoryAdapter(
        gateway=gw,
        namespace="aufmass",
        project_id="project-a",
        privacy_boundary=PRIVATE_RUNTIME_ONLY,
    )

    result = adapter.run(request(record_classification="PRIVATE", privacy_boundary=PRIVATE_RUNTIME_ONLY))

    assert result["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert result["decision"] == {"allowed": False, "reason": "canonical_write_requires_operator_approval"}
    assert result["payload"]["proposal_status"] == "ACCEPTED"
    assert result["payload"]["classification"] == "NEW_PROPOSAL"
    assert result["payload"]["canonical_write_performed"] is False
    assert result["payload"]["operator_approval_required"] is True


def test_private_record_public_receipt_does_not_expose_proposed_value() -> None:
    adapter = ProjectMemoryAdapter(
        gateway=gateway(privacy_boundary=PRIVATE_RUNTIME_ONLY),
        namespace="aufmass",
        project_id="project-a",
        privacy_boundary=PRIVATE_RUNTIME_ONLY,
    )

    result = adapter.run(request(record_classification="PRIVATE", privacy_boundary=PRIVATE_RUNTIME_ONLY))
    serialized = json.dumps(result, sort_keys=True)

    assert "proposed_value" not in serialized
    assert "private-runtime-only-value" not in serialized
    assert "secret" not in serialized.lower()
    assert ".sqlite" not in serialized


def test_canon_record_remains_proposal_only_pending_operator_approval() -> None:
    adapter = ProjectMemoryAdapter(
        gateway=gateway(),
        namespace="aufmass",
        project_id="project-a",
    )

    result = adapter.run(
        request(
            record_classification="CANON",
            privacy_boundary=PUBLIC_SAFE_CODE_TESTS_ONLY,
            proposal_payload=proposal(proposed_value={"state": "canon-candidate"}),
        )
    )

    assert result["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert result["payload"]["proposal_status"] == "ACCEPTED"
    assert result["payload"]["canonical_write_performed"] is False
    assert result["payload"]["operator_approval_required"] is True


def test_project_memory_adapter_uses_only_memory_gateway_for_access() -> None:
    source = inspect.getsource(ProjectMemoryAdapter)

    assert ".execute(" in source
    assert "sqlite" not in source.lower()
    assert "graphify" not in source.lower()
    assert "mempalace" not in source.lower()
    assert "open(" not in source

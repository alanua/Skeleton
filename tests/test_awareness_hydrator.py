from __future__ import annotations

import copy
import json
from pathlib import Path

from core.awareness_context import SECTION_NAMES
from core.awareness_hydrator import (
    AwarenessHydrationProviders,
    AwarenessHydrationRequest,
    hydrate_awareness_context,
    hydrate_from_read_models,
)
from core.capability_runtime_truth import RuntimeCapabilityEvidence, reconcile_capability_runtime_truth
from core.private_memory_history import content_hash
from core.private_memory_stack import PrivateMemoryStack


NOW = "2026-10-03T00:00:00Z"


def test_hydrator_selects_relevant_provider_inputs_without_mutation(tmp_path: Path) -> None:
    stack = _memory_stack(tmp_path)
    pending = _pending_work()
    capability_truth = _capability_truth()
    before = copy.deepcopy({"pending": pending, "capability_truth": capability_truth})

    result = hydrate_awareness_context(
        AwarenessHydrationRequest(
            project_id="skeleton",
            task_route="runner",
            task_body="Implement alpha document handling with repository write validation.",
            requested_capabilities=("repository_write_allowlisted",),
            now=NOW,
        ),
        AwarenessHydrationProviders(
            memory_stack=stack,
            pending_work=pending,
            capability_truth=capability_truth,
        ),
    )
    packet = result.packet
    receipt = result.public_receipt()
    serialized_receipt = json.dumps(receipt, sort_keys=True)

    assert tuple(packet["sections"]) == SECTION_NAMES
    assert tuple(receipt["section_hashes"]) == SECTION_NAMES
    assert receipt["counts"]["memory_records"] == 1
    assert receipt["counts"]["pending_items"] == 1
    assert receipt["counts"]["capability_records"] == 1
    assert receipt["counts"]["task_contract_records"] == 1
    assert receipt["counts"]["repository_scope_records"] == 1
    assert receipt["counts"]["privacy_controls_records"] == 1
    assert receipt["counts"]["runtime_controls_records"] == 1
    assert packet["sections"]["memory"]["records"][0]["canonical_ref"] == "skeleton.context:alpha"
    assert packet["sections"]["pending_work"]["items"][0]["item_kind"] == "document"
    assert packet["sections"]["capability_truth"]["records"][0]["capability_id"] == "repository_write_allowlisted"
    assert "runtime only alpha" in json.dumps(packet, sort_keys=True)
    assert "runtime only alpha" not in serialized_receipt
    assert receipt["private_payloads_included"] is False
    assert receipt["runtime_mutation_performed"] is False
    assert receipt["canonical_store_mutation_performed"] is False
    assert pending == before["pending"]
    assert capability_truth == before["capability_truth"]


def test_hydrate_from_read_models_filters_pending_and_capabilities_by_relevance() -> None:
    memory_context = _memory_context()
    pending = _pending_work()
    capability_truth = _capability_truth()

    result = hydrate_from_read_models(
        project_id="skeleton",
        task_route="runner",
        task_body="Mail operator checkpoint work needs attention.",
        requested_capabilities=("mail_operator_checkpoint",),
        memory_context=memory_context,
        pending_work=pending,
        capability_truth=capability_truth,
        now=NOW,
    )
    receipt = result.public_receipt()

    assert receipt["counts"]["memory_records"] == 1
    assert receipt["counts"]["pending_items"] == 1
    assert receipt["counts"]["capability_records"] == 1
    assert result.packet["sections"]["pending_work"]["items"][0]["item_kind"] == "mail"
    assert result.packet["sections"]["capability_truth"]["records"][0]["capability_id"] == "mail_operator_checkpoint"
    assert result.packet["runtime_mutation_performed"] is False
    assert result.packet["canonical_store_mutation_performed"] is False


def test_hydrator_empty_relevance_matches_all_injected_read_models() -> None:
    result = hydrate_from_read_models(
        project_id="skeleton",
        task_route="runner",
        task_body="",
        requested_capabilities=(),
        memory_context=_memory_context(),
        pending_work=_pending_work(),
        capability_truth=_capability_truth(),
        now=NOW,
    )

    counts = result.public_receipt()["counts"]

    assert counts["memory_records"] == 1
    assert counts["pending_items"] == 2
    assert counts["capability_records"] == 2
    assert counts["task_contract_records"] == 1
    assert counts["repository_scope_records"] == 1


def _memory_stack(tmp_path: Path) -> PrivateMemoryStack:
    stack = PrivateMemoryStack(tmp_path / "memory")
    stack.init(import_manifest=False)
    stack.put(
        namespace="skeleton.context",
        fact_id="alpha",
        value={"summary": "runtime only alpha", "tags": ["alpha", "document"]},
    )
    stack.put(
        namespace="skeleton.context",
        fact_id="beta",
        value={"summary": "runtime only beta", "tags": ["beta", "mail"]},
    )
    return stack


def _memory_context() -> dict[str, object]:
    value = {"summary": "runtime only alpha"}
    value_hash = content_hash(value)
    return {
        "receipt": {
            "schema": "skeleton.task_memory_context.v1",
            "status": "DONE",
            "profile": "private_runtime",
            "project_id": "skeleton",
            "task_route": "runner",
            "canonical_revision": 1,
            "selected_canonical_refs": ["skeleton.context:alpha"],
            "selected_records": [
                {
                    "canonical_ref": "skeleton.context:alpha",
                    "canonical_revision": 1,
                    "value_hash": value_hash,
                }
            ],
            "counts": {"selected": 1, "candidate_refs": 1, "rendered_chars": 32},
            "limits": {"records": 1, "max_chars": 100},
            "truncated": False,
            "context_hash": content_hash({"selected": ["skeleton.context:alpha"]}),
        },
        "private_values": [
            {
                "canonical_ref": "skeleton.context:alpha",
                "value": value,
                "value_hash": value_hash,
            }
        ],
    }


def _pending_work() -> dict[str, object]:
    return {
        "schema": "skeleton.shared_pending_lifecycle_work.v1",
        "pending_count": 2,
        "state_counts": {"BLOCKED": 1, "DEFERRED": 1},
        "item_kind_counts": {"document": 1, "mail": 1},
        "source_counts": {"document": 1, "mail": 1},
        "items": [
            {
                "store_ref": "document",
                "intake_id": "document-alpha",
                "item_kind": "document",
                "state": "DEFERRED",
                "blocker_reason": "BACKOFF_ACTIVE",
                "next_action": "retry_document_intake_after_backoff",
                "source_hash": "1" * 64,
                "updated_at": 1790985600,
            },
            {
                "store_ref": "mail",
                "intake_id": "mail-beta",
                "item_kind": "mail",
                "state": "BLOCKED",
                "blocker_reason": "OPERATOR_ACTION_REQUIRED",
                "next_action": "operator_review_action_required_mail",
                "source_hash": "2" * 64,
                "updated_at": 1790985600,
            },
        ],
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _capability_truth() -> dict[str, object]:
    return reconcile_capability_runtime_truth(
        {
            "version": "1.0.0",
            "capabilities": {
                "mail_operator_checkpoint": {"status": "available", "module": "core/mail.py"},
                "repository_write_allowlisted": {"status": "available", "module": "core/repo.py"},
            },
        },
        [
            RuntimeCapabilityEvidence(
                capability_id="repository_write_allowlisted",
                runtime_state="available",
                source="fixture",
                evidence_ref="repo",
                observed_at="2026-10-02T00:00:00Z",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            ),
            RuntimeCapabilityEvidence(
                capability_id="mail_operator_checkpoint",
                runtime_state="available",
                source="fixture",
                evidence_ref="mail",
                observed_at="2026-10-02T00:00:00Z",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            ),
        ],
        now=NOW,
    )

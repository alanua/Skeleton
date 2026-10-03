from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

from core.awareness_context import assemble_awareness_context
from core.capability_runtime_truth import RuntimeCapabilityEvidence, reconcile_capability_runtime_truth
from core.intake_lifecycle import IntakeLifecycleStore, shared_pending_lifecycle_work
from core.private_memory_stack import PrivateMemoryStack
from core.task_memory_context import build_task_memory_context


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "awareness_context.schema.json"
NOW = "2026-10-03T00:00:00Z"


def test_awareness_context_is_ephemeral_deterministic_and_public_receipt_safe(tmp_path: Path) -> None:
    memory = build_task_memory_context(
        _memory_stack(tmp_path),
        project_id="skeleton",
        task_route="runner",
        profile="private_runtime",
        query="alpha",
        required=True,
    )
    pending = _pending_work(tmp_path)
    capability_truth = _capability_truth()

    first = assemble_awareness_context(
        project_id="skeleton",
        task_route="runner",
        memory_context=memory,
        pending_work=pending,
        capability_truth=capability_truth,
        now=NOW,
    )
    second = assemble_awareness_context(
        project_id="skeleton",
        task_route="runner",
        memory_context=memory,
        pending_work=pending,
        capability_truth=capability_truth,
        now=NOW,
    )
    receipt_json = json.dumps(first.public_receipt(), sort_keys=True)

    assert first.packet["derivation_mode"] == "ephemeral_read_only_derived_packet"
    assert first.packet["runtime_mutation_performed"] is False
    assert first.packet["canonical_store_mutation_performed"] is False
    assert first.packet["awareness_hash"] == second.packet["awareness_hash"]
    assert first.public_receipt()["receipt_hash"] == second.public_receipt()["receipt_hash"]
    assert first.packet["sections"]["memory"]["counts"]["private_values_included"] == 2
    assert "runtime only alpha" in json.dumps(first.packet, sort_keys=True)
    assert "runtime only alpha" not in receipt_json
    assert first.public_receipt()["private_payloads_included"] is False


def test_awareness_context_bounds_sections_and_labels_conflicts(tmp_path: Path) -> None:
    memory = _conflicting_memory_context()
    pending = _pending_work(tmp_path)
    capability_truth = _capability_truth_with_conflict()

    result = assemble_awareness_context(
        project_id="skeleton",
        task_route="runner",
        memory_context=memory,
        pending_work=pending,
        capability_truth=capability_truth,
        now=NOW,
        memory_limit=1,
        pending_limit=1,
        capability_limit=1,
    )
    receipt = result.public_receipt()

    assert receipt["counts"] == {
        "memory_records": 1,
        "pending_items": 1,
        "capability_records": 1,
    }
    assert receipt["limits"] == {
        "memory_records": 1,
        "pending_items": 1,
        "capability_records": 1,
    }
    assert receipt["truncated"] == {
        "memory": True,
        "pending_work": True,
        "capability_truth": True,
    }
    assert receipt["labels"]["conflict"] == "CONFLICTS_PRESENT"
    assert receipt["labels"]["freshness"] == "MIXED"
    assert receipt["labels"]["epistemic"] == "OBSERVED"


def test_awareness_context_schema_validates_public_receipt(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    result = assemble_awareness_context(
        project_id="skeleton",
        task_route="runner",
        memory_context=build_task_memory_context(
            _memory_stack(tmp_path),
            project_id="skeleton",
            task_route="runner",
            profile="public_control",
            query="alpha",
            required=True,
        ),
        pending_work=_pending_work(tmp_path),
        capability_truth=_capability_truth(),
        now=NOW,
    )

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(result.public_receipt(), schema)


def test_awareness_context_does_not_mutate_inputs(tmp_path: Path) -> None:
    memory = build_task_memory_context(
        _memory_stack(tmp_path),
        project_id="skeleton",
        task_route="runner",
        profile="private_runtime",
        query="alpha",
        required=True,
    )
    memory_mapping = {
        "receipt": memory.public_receipt(),
        "private_values": copy.deepcopy(memory.private_values),
    }
    pending = _pending_work(tmp_path)
    capability_truth = _capability_truth()
    before = copy.deepcopy(
        {
            "memory": memory_mapping,
            "pending": pending,
            "capability_truth": capability_truth,
        }
    )

    assemble_awareness_context(
        project_id="skeleton",
        task_route="runner",
        memory_context=memory_mapping,
        pending_work=pending,
        capability_truth=capability_truth,
        now=NOW,
    )

    assert memory_mapping == before["memory"]
    assert pending == before["pending"]
    assert capability_truth == before["capability_truth"]


def _memory_stack(tmp_path: Path) -> PrivateMemoryStack:
    stack = PrivateMemoryStack(tmp_path / "memory")
    stack.init(import_manifest=False)
    stack.put(
        namespace="skeleton.context",
        fact_id="public",
        value={
            "egress_classification": "PUBLIC_SAFE_CONTROL",
            "text": "alpha public control",
            "tags": ["alpha"],
        },
    )
    stack.put(
        namespace="skeleton.context",
        fact_id="private",
        value={"summary": "runtime only alpha", "tags": ["alpha"]},
    )
    return stack


def _pending_work(tmp_path: Path) -> dict[str, object]:
    document_store = IntakeLifecycleStore(tmp_path / "document.sqlite3")
    mail_store = IntakeLifecycleStore(tmp_path / "mail.sqlite3")
    document_store.record(
        item_kind="document",
        source_ref="document:scan-a",
        source_hash="1" * 64,
        state="DEFERRED",
        blocker_reason="BACKOFF_ACTIVE",
        next_action="retry_document_intake_after_backoff",
        provenance_refs=("document:scan-a",),
        now=1,
    )
    mail_store.record(
        item_kind="mail",
        source_ref="acct:primary",
        source_hash="2" * 64,
        state="BLOCKED",
        blocker_reason="OPERATOR_ACTION_REQUIRED",
        next_action="operator_review_action_required_mail",
        provenance_refs=("mail:message-a",),
        now=1790985600,
    )
    return shared_pending_lifecycle_work({"document": document_store, "mail": mail_store}, limit=10)


def _capability_truth() -> dict[str, object]:
    return reconcile_capability_runtime_truth(
        {
            "version": "1.0.0",
            "capabilities": {
                "live_capability": {"status": "available", "module": "core/example.py"},
                "planned_capability": {"status": "planned", "module": "core/future.py"},
            },
        },
        [
            RuntimeCapabilityEvidence(
                capability_id="live_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="live",
                observed_at="2026-10-02T00:00:00Z",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            )
        ],
        now=NOW,
    )


def _capability_truth_with_conflict() -> dict[str, object]:
    return reconcile_capability_runtime_truth(
        {
            "version": "1.0.0",
            "capabilities": {
                "conflict_capability": {"status": "available", "module": "core/conflict.py"},
                "declared_capability": {"status": "planned", "module": "core/future.py"},
            },
        },
        [
            RuntimeCapabilityEvidence(
                capability_id="conflict_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="conflict-a",
                observed_at="2026-10-02T00:00:00Z",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            ),
            RuntimeCapabilityEvidence(
                capability_id="conflict_capability",
                runtime_state="degraded",
                source="fixture",
                evidence_ref="conflict-b",
                observed_at="2026-10-02T00:00:00Z",
                runtime_bound=True,
            ),
        ],
        now=NOW,
    )


def _conflicting_memory_context() -> dict[str, object]:
    return {
        "receipt": {
            "schema": "skeleton.task_memory_context.v1",
            "status": "DONE",
            "profile": "private_runtime",
            "project_id": "skeleton",
            "task_route": "runner",
            "canonical_revision": 3,
            "selected_canonical_refs": ["skeleton.context:alpha", "skeleton.context:alpha"],
            "selected_records": [
                {
                    "canonical_ref": "skeleton.context:alpha",
                    "canonical_revision": 2,
                    "value_hash": "a" * 64,
                },
                {
                    "canonical_ref": "skeleton.context:alpha",
                    "canonical_revision": 3,
                    "value_hash": "b" * 64,
                },
            ],
            "counts": {"selected": 2, "candidate_refs": 2, "rendered_chars": 0},
            "limits": {"records": 2, "max_chars": 100},
            "truncated": False,
            "context_hash": "c" * 64,
        },
        "private_values": [],
    }

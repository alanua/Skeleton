from __future__ import annotations

import pytest

from core.loop_memory_proposal import (
    LOOP_RESULT_FIELDS,
    LoopMemoryProposalError,
    propose_terminal_loop_memory,
)
from core.memory_patch_proposal import MemoryPatchProposalRegistry


def _terminal_result(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": "skeleton.loop_runner_result.v1",
        "status": "DONE",
        "action": "step",
        "task_id": "issue-1721",
        "run_id": "run-1721",
        "version": 4,
        "loop_state": "DONE",
        "event": "COMPLETE",
        "accepted": True,
        "decision": "STOP",
        "reason": "COMPLETED",
        "context_hash": "a" * 64,
        "public_safe": True,
        "external_side_effects_executed": False,
    }
    result.update(updates)
    return result


def test_terminal_loop_result_creates_registry_proposal_without_canonical_write() -> None:
    registry = MemoryPatchProposalRegistry()

    receipt = propose_terminal_loop_memory(
        _terminal_result(),
        registry=registry,
        operator_approval_ref="approval-1721",
    )

    assert receipt["canonical_write_performed"] is False
    assert receipt["operator_approval_required"] is True
    event = receipt["proposal_event"]
    assert isinstance(event, dict)
    assert event["status"] == "ACCEPTED"
    assert event["canonical_write_performed"] is False
    assert event["operator_approval_required"] is True
    proposal = receipt["proposal"]
    assert isinstance(proposal, dict)
    assert registry.lookup_by_idempotency_key(str(proposal["idempotency_key"])) == event


def test_loop_memory_proposal_requires_exact_result_field_set() -> None:
    extra = _terminal_result(extra="forbidden")
    missing = _terminal_result()
    missing.pop("context_hash")

    with pytest.raises(LoopMemoryProposalError):
        propose_terminal_loop_memory(
            extra,
            registry=MemoryPatchProposalRegistry(),
            operator_approval_ref="approval-1721",
        )
    with pytest.raises(LoopMemoryProposalError):
        propose_terminal_loop_memory(
            missing,
            registry=MemoryPatchProposalRegistry(),
            operator_approval_ref="approval-1721",
        )

    assert set(_terminal_result()) == set(LOOP_RESULT_FIELDS)


@pytest.mark.parametrize(
    "updates",
    (
        {"accepted": False},
        {"loop_state": "RUNNING", "decision": "CONTINUE", "reason": "STEP_SUCCEEDED"},
        {"decision": "CONTINUE"},
        {"reason": "RUNNING"},
        {"external_side_effects_executed": True},
    ),
)
def test_loop_memory_proposal_rejects_non_terminal_or_written_results(
    updates: dict[str, object],
) -> None:
    with pytest.raises(LoopMemoryProposalError):
        propose_terminal_loop_memory(
            _terminal_result(**updates),
            registry=MemoryPatchProposalRegistry(),
            operator_approval_ref="approval-1721",
        )


def test_loop_memory_proposal_requires_operator_approval_ref() -> None:
    with pytest.raises(LoopMemoryProposalError):
        propose_terminal_loop_memory(
            _terminal_result(),
            registry=MemoryPatchProposalRegistry(),
            operator_approval_ref="",
        )

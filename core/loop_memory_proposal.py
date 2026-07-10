from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from core.loop_runner_adapter import LOOP_RUNNER_RESULT_SCHEMA
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
    stable_hash,
)


LOOP_MEMORY_PROPOSAL_SCHEMA: Final = "skeleton.loop_memory_proposal.v1"
LOOP_TERMINAL_REASONS: Final = frozenset({"DONE", "STOP", "COMPLETED"})
LOOP_RESULT_FIELDS: Final = frozenset(
    {
        "schema",
        "status",
        "action",
        "task_id",
        "run_id",
        "version",
        "loop_state",
        "event",
        "accepted",
        "decision",
        "reason",
        "context_hash",
        "public_safe",
        "external_side_effects_executed",
    }
)


class LoopMemoryProposalError(ValueError):
    """Raised when a Loop result cannot become a memory patch proposal."""


def propose_terminal_loop_memory(
    loop_result: Mapping[str, Any],
    *,
    registry: MemoryPatchProposalRegistry,
    operator_approval_ref: str,
    project_id: str = "skeleton",
    namespace: str = "loop",
) -> dict[str, object]:
    proposal = terminal_loop_memory_proposal(
        loop_result,
        operator_approval_ref=operator_approval_ref,
        project_id=project_id,
        namespace=namespace,
    )
    event = registry.propose(proposal)
    return {
        "schema": LOOP_MEMORY_PROPOSAL_SCHEMA,
        "proposal": proposal,
        "proposal_event": event,
        "canonical_write_performed": event["canonical_write_performed"],
        "operator_approval_required": event["operator_approval_required"],
    }


def terminal_loop_memory_proposal(
    loop_result: Mapping[str, Any],
    *,
    operator_approval_ref: str,
    project_id: str = "skeleton",
    namespace: str = "loop",
) -> dict[str, object]:
    if not isinstance(loop_result, Mapping) or frozenset(loop_result) != LOOP_RESULT_FIELDS:
        raise LoopMemoryProposalError("loop result must match exact schema")
    if loop_result["schema"] != LOOP_RUNNER_RESULT_SCHEMA:
        raise LoopMemoryProposalError("invalid loop result schema")
    if loop_result["accepted"] is not True:
        raise LoopMemoryProposalError("loop result must be accepted")
    if loop_result["public_safe"] is not True:
        raise LoopMemoryProposalError("loop result must be public-safe")
    if loop_result["external_side_effects_executed"] is not False:
        raise LoopMemoryProposalError("canonical write must not have been performed")
    if loop_result["loop_state"] != "DONE":
        raise LoopMemoryProposalError("loop result must be terminal DONE")
    if loop_result["decision"] != "STOP":
        raise LoopMemoryProposalError("loop terminal decision must be STOP")
    if loop_result["reason"] not in LOOP_TERMINAL_REASONS:
        raise LoopMemoryProposalError("loop terminal reason is not accepted")
    version = loop_result["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise LoopMemoryProposalError("loop result version must be non-negative integer")
    task_id = _token(loop_result["task_id"], "task_id")
    run_id = _token(loop_result["run_id"], "run_id")
    context_hash = _hash(loop_result["context_hash"], "context_hash")
    approval_ref = _token(operator_approval_ref, "operator_approval_ref")

    evidence = stable_hash(
        {
            "schema": LOOP_RUNNER_RESULT_SCHEMA,
            "task_id": task_id,
            "run_id": run_id,
            "version": version,
            "context_hash": context_hash,
            "reason": loop_result["reason"],
        }
    )
    proposal: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": _token(namespace, "namespace"),
        "project_id": _token(project_id, "project_id"),
        "object_id": run_id,
        "entity_scope": "loop_run",
        "fact_type": "terminal_result",
        "normalized_target": task_id,
        "source_evidence_hash": evidence,
        "proposed_value": {
            "loop_state": "DONE",
            "decision": "STOP",
            "reason": loop_result["reason"],
            "version": version,
            "context_hash": context_hash,
        },
        "provenance_refs": [
            {
                "ref": f"loop-result-{run_id}-{version}",
                "kind": "exact_source",
                "evidence_hash": evidence,
            }
        ],
        "actor_ref": "loop_runner",
        "reason_code": "operator-approved-loop-terminal-result",
        "approval_tier": "operator",
        "approval_ref": approval_ref,
        "confirmed_via_exact_ref": f"loop-result-{run_id}-{version}",
        "confirmed_canonical_revision": version,
    }
    proposal["dedupe_key"] = canonical_dedupe_key(proposal)
    proposal["idempotency_key"] = canonical_idempotency_key(proposal)
    return proposal


def _token(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise LoopMemoryProposalError(f"invalid {name}")
    if not value[0].isalnum() or any(
        not (character.isalnum() or character in "_.:-") for character in value
    ):
        raise LoopMemoryProposalError(f"invalid {name}")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise LoopMemoryProposalError(f"invalid {name}")
    try:
        int(value, 16)
    except ValueError as exc:
        raise LoopMemoryProposalError(f"invalid {name}") from exc
    return value

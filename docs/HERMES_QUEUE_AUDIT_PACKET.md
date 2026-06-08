# Hermes Queue Audit Packet v0

This document defines the public-safe Hermes queue-audit packet format. Version
0 is a static documentation contract only. It does not add a runtime service,
timer, queue mutation, GitHub write, issue creation, labeling, requeue, close,
merge, supersede, host maintenance, or canon promotion path.

## Purpose

A Hermes queue-audit packet is a read-only review artifact. It records a
snapshot of provided queue state, highlights review risks, and separates
advisory recommendations from operator decisions.

The packet must be model-neutral and public-safe. It must not include secrets,
private raw content, private URLs, credentials, hidden prompts, or unbounded
transcripts.

## Source Inputs

Queue-audit packets may be prepared only from read-only source inputs, such as:

- public-safe queue snapshots or summaries;
- public-safe issue, task, or runner metadata;
- reviewed repository documentation;
- sanitized operator-provided context.

The packet must not require or imply write access to any queue, issue tracker,
repository, host, credential store, or external service.

## Authority Boundary

The packet is review and evidence only.

Hermes must not:

- create, edit, label, close, requeue, merge, supersede, or delete issues or
  queue items;
- write to GitHub or any other tracker;
- mutate runtime state, queue state, host state, repository state, or canon;
- perform host maintenance;
- include secrets or private data;
- command another actor to mutate state.

Any action listed in the packet is a recommendation for human or authorized
system review only.

## Packet Fields

`snapshot` records the public-safe audit context, including when the snapshot
was prepared, what read-only sources were used, and the explicit no-mutation and
no-secret guarantees.

`active_items` lists queue items currently believed to be active, using
public-safe identifiers, summaries, status, source references, and review notes.

`blocked_items` lists active or pending items that appear blocked, including
public-safe blocker descriptions and the review-only reason they need attention.

`done_open_items` lists items that appear done but still open in the queue or
tracker, with evidence summaries and review notes.

`duplicate_candidates` lists groups of items that may represent the same work,
including public-safe evidence and the recommended review-only disposition.

`carrier_defects` lists defects in the carrier system or metadata that may
distort queue interpretation, such as missing status, conflicting labels, stale
assignment metadata, or malformed public-safe fields.

`state_drift` lists mismatches between queue state and the available
read-only evidence, such as an item marked active after evidence indicates it is
done.

`recommended_review_only_actions` lists advisory actions for an operator or
authorized process to review. These are not commands and must not be executed by
Hermes.

`operator_decisions_needed` lists decisions that require operator judgment or
authorization before any mutation can occur.

## Minimal Packet Example

```json
{
  "schema": "hermes.queue_audit_packet.v0",
  "snapshot": {
    "generated_at": "2026-06-08T00:00:00Z",
    "queue_name": "runner-review",
    "source_inputs": [
      {
        "source_type": "queue_snapshot",
        "reference": "public-safe runner queue summary",
        "read_only": true
      }
    ],
    "read_only_sources_only": true,
    "no_mutations_performed": true,
    "no_secrets_included": true,
    "notes": "Minimal public-safe audit example."
  },
  "active_items": [
    {
      "item_id": "TASK-1",
      "title": "Review duplicate task report",
      "status": "active",
      "source_refs": ["public-safe runner queue summary"],
      "notes": "Active according to the provided snapshot."
    }
  ],
  "blocked_items": [],
  "done_open_items": [],
  "duplicate_candidates": [
    {
      "candidate_ids": ["TASK-1", "TASK-2"],
      "evidence": ["Both summaries describe the same affected component."],
      "recommended_review_only_disposition": "Review whether TASK-2 should be treated as duplicate evidence."
    }
  ],
  "carrier_defects": [],
  "state_drift": [],
  "recommended_review_only_actions": [
    {
      "action": "Review duplicate candidate group.",
      "rationale": "The packet cannot merge, close, label, or requeue items.",
      "mutation_allowed": false
    }
  ],
  "operator_decisions_needed": [
    {
      "decision": "Decide whether TASK-2 is a duplicate of TASK-1.",
      "reason": "Only an authorized operator or reviewed process may change queue or tracker state."
    }
  ]
}
```

# Execution Fabric — model evidence slice

This document records the non-protected discovery/measured-model layer used by Execution Fabric.

Executors and models remain separate authority dimensions. The model roster answers only whether a registered model has evidence for a required capability class. It does not grant repository, merge, deploy, device, finance, legal, or governance authority.

## Discovery before routing

Candidate discovery is deliberately outside routing authority. Public model catalogs/rankings such as OpenRouter, official provider metadata, and external leaderboards can create `DiscoverySignal` records and order a bounded canary shortlist. Advertised capability/tool support, context, privacy, provider policy, availability, and bounded cost are filters before canary spend. No discovery source can directly create an eligible production route.

Promotion is per capability:

`DISCOVERED -> CANARY_ONLY -> ELIGIBLE -> LIVE`

A Skeleton canary PASS produces `ELIGIBLE`, not `LIVE`. `LIVE` is reserved for a later explicit production activation through the integrated Execution Fabric gates. Hard failures are not averaged away.

The model selector therefore has two concepts:
- evaluation/task-fit eligibility: `ELIGIBLE` or `LIVE` capability evidence may be ranked;
- production eligibility: `production_only` requires explicit `LIVE` promotion for every required capability.

A future `ExecutionBinding` may reference one model record only after executor compatibility, task capability, privacy, policy, credential, health, budget, and production-promotion gates all pass together. Final evidence/health identity is the tuple `(executor_id, model_id, capability_id)`; this slice keeps model evidence isolated by `(model_id, capability_id)` until executor compatibility is integrated.

Response-only success does not imply tool-use or repository-edit eligibility. A required artifact missing or tool-use failure is hard failure evidence for that capability.

## Terminal Success Authority

For codegen that publishes or updates a PR, terminal success is not inferred
from a single Runner/Codex report, return code, labels, model text, or local
tests. The source task queues an exact-head `validate_pr_branch` continuation
and remains non-DONE while that validation is pending. The continuation receipt
is authoritative only when it is typed as `validate_pr_branch`, reports
`success_criteria=met`, and carries the exact PR number, validation head SHA,
base SHA, and clean final worktree status.

Canonical finalization reads the current PR metadata again and compares that
live PR/head/base identity with the validation receipt before emitting `DONE`
or protected `NEEDS_OPERATOR`. A validated head A with current head B is
`stale_validation_head` and cannot become `DONE`. Protected or high-risk
accepted work remains `NEEDS_OPERATOR` after the exact receipt matches, and
that state is not projected as `runner:done` or `runner:blocked`.

The deterministic selector consumes a code-owned `TaskFitRequest` containing required capability thresholds and privacy class. It has no field for caller-provided provider/model/endpoint authority. Given the same request and registry snapshot it returns the same ranking.

This slice does not create or dispatch bindings and does not modify the live Runner route. Next phase under #2809 integrates the roster into atomic executor+model `ExecutionBinding`, immutable `RouteLease`, periodic bounded discovery/canary scheduling, and explicit LIVE activation. Any protected Runner integration requires exact-head operator approval.

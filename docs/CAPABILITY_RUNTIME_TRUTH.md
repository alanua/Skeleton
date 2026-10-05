# Capability Runtime Truth

CAPABILITY_REGISTRY.yaml is a static source declaration. It records checked-in
source contracts, declared entrypoints, supporting files, tests, and source
maturity. available and planned remain compatibility statuses.
status: source_implemented is a static source-maturity status only. It does
not declare LIVE runtime state and it does not declare FRESH evidence state.

Runtime truth is a derived public-safe read model produced by
core/capability_runtime_truth.py. The reconciler compares static registry
declarations with explicit RuntimeCapabilityEvidence records. It does not
probe runtime state and it does not mutate runtime state.

Only typed runtime evidence can produce LIVE. Only typed runtime evidence can
produce FRESH. A capability with status: source_implemented has a checked-in
source contract, but remains outside the compatibility available and planned
lists. Without qualifying runtime evidence, source maturity alone does not
establish runtime binding, freshness, or source/runtime parity.

## Boundary Rules

- Static registry status may declare source maturity with status: source_implemented.
- There is no separate boolean source_implemented registry field.
- Static registry declarations must not claim effective runtime status,
  freshness, runtime binding, source/runtime parity, observed evidence
  timestamps, or runtime drift.
- Runtime evidence must name a known registry capability and satisfy the typed
  RuntimeCapabilityEvidence contract.
- Fresh evidence can still fail to produce LIVE when runtime binding,
  source/runtime parity, usable interfaces, or evidence consistency are
  missing.
- Stale, future-dated, contradictory, unbound, or source-divergent evidence
  fails closed according to the reconciler rules.

## Current Static Source Primitives

The following capabilities use the source_implemented status because their
source contracts exist, while their runtime state must still be established by
runtime evidence:

- capability_runtime_truth
- awareness_context
- awareness_hydrator
- intake_lifecycle
- knowledge_intake_review_queue
- action_gate
- memory_gateway

These are source-maturity declarations only. LIVE and FRESH remain conclusions
from typed runtime evidence.

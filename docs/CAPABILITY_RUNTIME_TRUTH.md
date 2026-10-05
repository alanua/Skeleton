# Capability Runtime Truth

CAPABILITY_REGISTRY.yaml is a static source declaration. It records whether a
capability contract has checked-in source, declared entrypoints, required support
files, tests, and source implementation maturity. Registry
available/planned remain compatibility statuses, while status:
source_implemented is a static source maturity status and source_implemented is
a static source maturity field. It does not declare LIVE runtime state and does
not declare FRESH evidence state.

Runtime truth is a derived public-safe read model produced by
`core/capability_runtime_truth.py`. The reconciler compares static registry
declarations with explicit `RuntimeCapabilityEvidence` records and returns a
bounded report with `runtime_probe_performed: false` and
`runtime_mutation_performed: false`.

Only typed runtime evidence can produce LIVE. Only typed runtime evidence can
produce FRESH. A registry entry with `status: available` preserves compatibility
with available-capability readers. A registry entry with
`status: source_implemented` and `source_implemented: true` means the source
contract is present and mature enough to be used as declared, without adding it
to available/planned compatibility lists. Without fresh runtime evidence, the
effective runtime state remains `CONTRACT_ONLY` and freshness remains `UNKNOWN`.

## Boundary Rules

- Static registry fields may declare source maturity through
  `source_implemented`, module paths, entrypoints, supporting files, tests,
  stage labels, and whether the capability itself is a live-runtime executor.
- Static registry `available` and `planned` status values are compatibility
  declarations. Static `source_implemented` status is a source maturity
  declaration and must not be treated as available or planned compatibility.
- Static registry fields must not declare effective runtime status, freshness,
  runtime binding, source/runtime parity, runtime evidence, observed evidence
  timestamps, or drift.
- Runtime evidence must name a known registry capability, include a typed
  runtime state, carry an observed UTC timestamp, and pass the freshness rules in
  `RuntimeCapabilityEvidence`.
- Fresh evidence can still fail to produce LIVE when runtime binding,
  source/runtime parity, usable interfaces, or evidence consistency are missing.
- Stale, future-dated, contradictory, unbound, or source-divergent evidence fails
  closed to `CONTRACT_ONLY`, `PARTIAL`, or `BLOCKED` according to the reconciler
  rules.

## Current Static Source Primitives

The registry declares the current source-backed primitives as
`source_implemented` because public source and tests exist for them, but they
must remain outside available/planned compatibility lists:

- `capability_runtime_truth`: reconciles declarations with typed runtime
  evidence without probing or mutating runtime state.
- `awareness_context`: assembles deterministic ephemeral awareness packets and
  public-safe receipts from already supplied read models.
- `awareness_hydrator`: filters injected read models and memory context providers
  into awareness context without mutating pending work or stores.
- `intake_lifecycle`: records stable intake identity, blocker metadata, pending
  work, orphan recovery, and public-safe pending summaries.
- `knowledge_intake_review_queue`: stores public-safe review/backlog/rejected
  knowledge intake entries that are not canon and do not activate runtime work.
- `action_gate`: validates bounded pull-request action requests without live
  execution.
- `memory_gateway`: provides the versioned synthetic gateway contract for
  namespace-isolated memory access without live runtime execution.

These declarations are source maturity claims only. `source_implemented` is not
runtime evidence, and LIVE and FRESH remain runtime-evidence conclusions.

# Production Engineering Quality Pipeline

This Phase 1 foundation is a pure pre-codegen quality evaluator. It validates a
structured `TaskSpec`, derives declared scope and predicted risk, and evaluates
deterministic evidence readiness against exact repository, base, and head
bindings.

Phase 1 deliberately does not extract observed diff impact, touched files,
capability reality, PR facts, merge state, deployed runtime state, or canary
results from the repository or external systems. Actual diff and capability
extraction is deferred to Phase 2 (#3151), after a real PR/head exists.

## Contracts

- `core/task_quality_gate.py` validates `skeleton.task_spec.v1` and fails closed
  with stable reason codes for malformed, contradictory, or materially
  incomplete task formulations.
- `allowed_files` is represented only as declared scope. It is not exposed as
  `touched_files`, `observed_impact`, or any equivalent reality claim.
- `core/architecture_invariants.py` evaluates supplied architecture evidence
  without reading policy files, creating invariant files, calling subprocesses,
  or mutating the filesystem.
- `core/quality_evidence.py` evaluates readiness monotonically:
  `NOT_READY`, `TASK_SPEC_ACCEPTED`, `TESTS_GREEN`, `ARCHITECTURE_GREEN`,
  `PRODUCTION_READY`, then `RUNTIME_PROVEN`.
- `PRODUCTION_READY` is the maximum pre-merge state.
- `RUNTIME_PROVEN` requires structured post-merge runtime evidence proving
  merged-main identity, runtime/deployed identity, successful canary/probe
  status, and exact evidence binding. A string, enum, or boolean claim is never
  sufficient.

## Evidence Boundaries

Evidence must be supplied by callers as structured values. These evaluators do
not perform network, subprocess, filesystem mutation, GitHub, LLM/provider,
merge, deployment, runtime, or canary actions.

Head-bound evidence is invalidated when the reviewed head changes. Mock-only
evidence cannot satisfy a configured real production-contract requirement.
Sandbox or staging production-contract proof may contribute to
`PRODUCTION_READY` without production mutation when it is structured and bound
to the exact repository/base/head.

Receipts are public-safe metadata only: enums, booleans, counts, hashes, and
stable public identifiers.

Next action: `EXACT_HEAD_REVIEW_THEN_PHASE1B_INVARIANTS_3150`.

# Production Engineering Quality Pipeline

Phase 1 defines only the task-formulation and readiness foundation. It normalizes
a `TaskSpec`, validates semantic consistency, and evaluates deterministic evidence
that this phase can honestly verify.

`allowed_files`, requested capabilities, expected output, dependencies, and
evidence expectations are claim-side declarations. Phase 1 does not synthesize
`touched_files`, observed diff impact, runtime receipts, merge receipts, canary
receipts, or authenticated production-contract proof.

## Phase 1 Readiness

Readiness is monotonic and stops at the last verified state:

- `TASK_SPEC_VALIDATED`: the normalized task formulation is internally consistent.
- `TESTS_GREEN`: exact base/head-bound deterministic test evidence is green.
- `PRODUCTION_READY`: allowed only when all configured proof gates are genuinely
  satisfied by Phase 1-verifiable evidence and no protected/critical gate remains.

If architecture evidence is configured as required, Phase 1 returns
`ARCHITECTURE_EVALUATOR_REQUIRED`. Caller-provided booleans, reviewer IDs,
invariant ID lists, strings, or enums cannot produce `ARCHITECTURE_GREEN`.
Architecture invariant proof belongs to #3150.

If production-contract proof is configured as required, Phase 1 accepts only the
typed `ProductionContractPreMergePlaceholder`, which is explicitly unauthenticated.
That placeholder cannot by itself raise protected or critical readiness.
Evidence authenticity belongs to #3153. If production-contract proof is optional,
absence of that evidence does not block Phase 1 readiness.

`RUNTIME_PROVEN` is forbidden in Phase 1 for all inputs. Any runtime, merge,
revision, success enum, or canary-shaped input returns
`POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1`. Post-merge runtime proof belongs
to #3160.

Observed diff impact and touched-file synthesis belong to #3151.

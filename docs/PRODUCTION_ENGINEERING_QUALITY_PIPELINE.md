# Production Engineering Quality Pipeline

This repository now has a reusable, deterministic QA core for pre-merge task
quality and architecture evidence. The core is intentionally separate from
Runner integration: it does not call GitHub, run subprocesses, mutate the file
system, call models, merge code, or perform runtime actions.

## Modules

- `core/quality_evidence.py` defines immutable evidence models for tests,
  dependencies, review, and runtime state.
- `core/architecture_invariants.py` evaluates protected targets, yellow/high
  risk impact, immutable invariant evidence, independent review, adversarial
  review, exact head SHA binding, and self-modifying policy diffs.
- `core/task_quality_gate.py` composes task contract completeness,
  dependency-existence evidence, file/capability impact, test evidence,
  architecture evidence, review evidence, and runtime evidence into stable
  readiness states.

## Readiness States

- `TESTS_GREEN` means deterministic test evidence is present and green, but it
  does not imply architecture readiness.
- `ARCHITECTURE_GREEN` means architecture invariants are satisfied, but it does
  not imply production readiness without tests and dependency evidence.
- `PRODUCTION_READY` is the pre-merge state for a complete contract with green
  tests, dependency evidence, and any required architecture and review evidence.
- `RUNTIME_PROVEN` is only emitted after post-merge runtime canary evidence.

Malformed or missing required evidence fails closed with reason codes. Yellow
and protected profiles require architecture invariant evidence plus independent
and adversarial review evidence. Protected targets also require explicit
protected-review metadata.

## Public Receipts

Receipts contain public-safe policy metadata only: enums, booleans, counts, and
stable hashes. They intentionally exclude raw paths, dependency names, task
payload text, private data, and secret values.

## Runner Integration

This slice is a pure core only. The next action is:

`REVIEW_PHASE1_THEN_CREATE_PROTECTED_PHASE2_RUNNER_INTEGRATION`

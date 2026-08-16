# Execution Fabric — atomic binding core

Execution Fabric separates execution authority from model capability. Executors and models live in separate registries; routing combines them only into already-compatible immutable `ExecutionBinding` candidates.

Canonical flow:

`typed task contract -> TaskProfile -> policy/privacy/capability gates -> ExecutionBindings -> deterministic order -> RouteLease -> attempt -> DeliverableValidation -> DONE | REJECTED | NEEDS_OPERATOR`

## TaskProfile authority

`TaskProfile` is derived only from typed task fields plus code-owned policy. Free-form issue prose, prompt text, model/provider/executor names, endpoint strings and caller-supplied budget language are not routing authority.

The profile carries the task class and operation, required executor capabilities, required model capability thresholds, privacy/data/risk/side-effect classes, deliverable and validation contracts, and bounded budget/token/timeout/retry policy references.

## Separate registries, atomic bindings

`EXECUTOR_REGISTRY.yaml` describes executor facts: supported task/capability classes, locality, privacy and side-effect classes, credential aliases, health, timeout/concurrency, completion evidence and allowed model binding kinds.

`MODEL_REGISTRY.yaml` remains the measured model evidence roster. External discovery/rankings can propose candidates but never enter production binding authority. Production external-model bindings require explicit `LIVE` promotion for every required model capability; `ELIGIBLE` challengers remain evaluation-only.

Binding kinds are explicit:

- `NO_MODEL`: deterministic maintenance/validation work without an LLM.
- `EMBEDDED_MODEL`: a harness such as Codex whose model is embedded in the executor contract; no fake external model identity is invented.
- `EXTERNAL_MODEL`: an executor such as OpenHands paired with one exact compatible registered model. Provider compatibility, privacy and capability gates must already pass.

An executor and model are never chosen independently and stitched together after routing. The atomic binding is the route candidate.

## RouteLease

`RouteLease` is a minimal immutable execution authorization record. It carries the exact TaskProfile hash, binding id, permissions, validation id, bounded cost/token/timeout/attempt limits and expiry. It does not contain prompt text, raw private context or secret material. Same profile, binding and fixed expiry produce the same lease hash.

## DeliverableValidation owns DONE

Executor return code is attempt evidence only. It never owns completion.

Examples:

- `rc=0` plus a code-generation contract requiring changed files, but zero changed files -> `DELIVERABLE_MISSING`, `REJECTED`, never DONE.
- required tests or validation missing/failing -> `VALIDATION_FAILED`.
- required artifact absent -> `DELIVERABLE_MISSING`.
- all deterministic deliverable evidence satisfied -> DONE, independent of executor rc classification.
- protected/high-risk final action with otherwise valid evidence -> `NEEDS_OPERATOR` until exact operator approval.

Stable failure classes live in `core/failure_taxonomy.py`; transient provider/executor failures must not be averaged into successful deliverable evidence.

## Current phase boundary

This core does not change the live Runner route and does not resolve credentials or call model providers. The protected next phase under #2809 wires `scripts/runner_poll_github_tasks.py` to these contracts, replaces the hidden Codex-to-OpenHands switch with explicit bindings, persists route/deliverable receipts, and makes zero-deliverable false-DONE impossible in the live conveyor. That protected integration requires exact-head operator approval before merge.

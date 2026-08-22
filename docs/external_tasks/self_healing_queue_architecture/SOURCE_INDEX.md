# SOURCE INDEX — inspect these before proposing the design

All links are public repository state. Prefer exact main SHA `60dda6337da32c9f80d1f3a5ac19ae2b9008e507` where possible so your review is reproducible.

## Architecture / self-healing docs

- `docs/CONTROL_PLANE_SELF_HEALING.md`
- `docs/EXECUTION_FABRIC.md`
- `docs/MODEL_ROUTING.md`

## Execution Fabric core on main

- `core/execution_fabric.py`
- `core/executor_registry.py`
- `core/model_registry.py`
- `core/model_selector.py`
- `core/failure_taxonomy.py`
- `EXECUTOR_REGISTRY.yaml`
- `MODEL_REGISTRY.yaml`
- schemas under `schemas/task_profile.schema.json`, `schemas/execution_binding.schema.json`, `schemas/route_lease.schema.json`, `schemas/deliverable_validation.schema.json`

## Canonical Runner / queue / recovery

- `scripts/runner_poll_github_tasks.py`
- `core/runner_child_environment.py`
- `core/runner_executor.py`
- `core/runner_executor_registry.py`
- `core/runner_repository_maintenance_executor.py`
- `core/runner_gate.py`
- `core/control_recovery.py`
- `core/scheduler_engine.py`
- `core/scheduler_store.py`

## Relevant tests

Search especially for:

- queue idle recovery;
- scheduler leases / heartbeat / passive reconciliation;
- terminal label reconciliation;
- validate_pr_branch exact-head behavior;
- publication contract checks;
- codegen child environment / fallback behavior;
- execution fabric / deliverable validation;
- model capability and privacy eligibility.

Likely files include:

- `tests/test_runner_poll_github_tasks.py`
- `tests/test_runner_child_environment.py`
- `tests/test_execution_fabric.py`
- `tests/test_executor_registry.py`
- `tests/test_model_selector.py`
- scheduler/control-recovery tests under `tests/`.

## GitHub issues / PRs to read

### #2809 — Execution Fabric architecture authority

`https://github.com/alanua/Skeleton/issues/2809`

Defines executor/model separation, atomic ExecutionBinding routing, RouteLease, DeliverableValidation, failure taxonomy and protected Phase B migration direction.

### #2885 — protected Runner Phase B target

`https://github.com/alanua/Skeleton/issues/2885`

Defines integration of the merged Execution Fabric into the protected canonical Runner. Its own execution demonstrates the false-DONE defect: zero file changes after OpenHands fallback, yet terminal `runner:done`.

### #2890 — protected safety PR disabling implicit OpenHands fallback

`https://github.com/alanua/Skeleton/pull/2890`

Current proposed safety slice at package creation. Review this as an emergency containment measure, not necessarily the final architecture.

### #2777 — representative P0 codegen task

`https://github.com/alanua/Skeleton/issues/2777`

A concrete nontrivial task that was historically labeled done despite zero requested changes through the fallback path.

### #2887 — minimal protected registration task reproducing the defect

`https://github.com/alanua/Skeleton/issues/2887`

A deliberately small protected task still ended with OpenHands `RESULT: OK` and zero edits, proving the problem is systemic rather than task complexity.

## Historical queue/self-heal references worth searching

Search repository issues/PR history around:

- queue self-healing;
- `QUEUE_IDLE_WITH_ELIGIBLE_WORK`;
- `runner:waiting-dependency`;
- `replenish_runner_queue`;
- `validate_pr_branch`;
- `runtime_sync_main`;
- scheduler lease / heartbeat;
- stale poller recovery;
- terminal label pollution;
- `AMBIGUOUS_MUTATING_RECEIPT`;
- codegen runtime recovery.

## What not to infer

Do not infer private runtime state, secret values, device paths or private customer/document data from this package. The review concerns architecture and public-safe control state only.

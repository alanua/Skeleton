# S2 Universal Runner Loop Gap Audit

Audit target: current `main` at base SHA `4caa45717764b8ae380985d1366fce7f42ee338c`.

## Repository References

The test in `tests/test_s2_universal_runner_loop_gap_audit.py` parses this table and verifies every documented path and symbol still exists.

| Path | Symbol |
| --- | --- |
| `core/loop_controller.py` | `LoopState` |
| `core/loop_controller.py` | `LoopEvent` |
| `core/loop_controller.py` | `LoopContext` |
| `core/loop_controller.py` | `LoopPolicy` |
| `core/loop_controller.py` | `advance_loop` |
| `core/loop_engine.py` | `LoopEngine` |
| `core/loop_engine.py` | `LoopEngine.create` |
| `core/loop_engine.py` | `LoopEngine.step` |
| `core/loop_state_store.py` | `LoopStateStore` |
| `core/loop_state_store.py` | `LoopStateStore.initialize` |
| `core/loop_state_store.py` | `LoopStateStore.create_run` |
| `core/loop_state_store.py` | `LoopStateStore.append_result` |
| `core/loop_state_store.py` | `LoopStateStore.list_events` |
| `core/loop_runner_adapter.py` | `run_loop_task_packet` |
| `core/loop_runner_adapter.py` | `LOOP_RUNNER_PACKET_SCHEMA` |
| `core/loop_runner_adapter.py` | `_EXPECTED_AUTHORITY_BOUNDARY` |
| `core/runner_loop_control_executor.py` | `LOOP_ENGINE_PACKET` |
| `core/runner_loop_control_executor.py` | `LOOP_STATE_DB_ENV` |
| `core/runner_loop_control_executor.py` | `loop_state_db_path` |
| `core/runner_loop_control_executor.py` | `loop_receipt_report` |
| `core/runner_loop_control_executor.py` | `execute_loop_engine_packet` |
| `core/loop_recovery_packet.py` | `LoopRecoveryPacket` |
| `core/loop_recovery_packet.py` | `LoopRecoveryPacket.from_mapping` |
| `core/loop_recovery_packet.py` | `LoopRecoveryPacket.event` |
| `core/loop_recovery_packet.py` | `LOOP_RECOVERY_PACKET_SCHEMA` |
| `core/runner_lease_store.py` | `RunnerLeaseStore` |
| `core/runner_lease_store.py` | `RunnerLeaseStore.acquire` |
| `core/runner_lease_store.py` | `RunnerLeaseStore.heartbeat` |
| `core/runner_lease_store.py` | `RunnerLeaseStore.save_checkpoint` |
| `core/runner_lease_store.py` | `RunnerLeaseStore.complete` |
| `core/runner_lease_store.py` | `RunnerLeaseStore.fail` |
| `core/runner_lease_store.py` | `RunnerLeaseStore.reconcile` |
| `scripts/runner_poll_github_tasks.py` | `trusted_runner_comment_authors` |
| `scripts/runner_poll_github_tasks.py` | `_body_field` |
| `scripts/runner_poll_github_tasks.py` | `get_issue_comments` |
| `scripts/runner_poll_github_tasks.py` | `loop_engine_packet` |
| `scripts/runner_poll_github_tasks.py` | `_validation_command_receipt_lines` |
| `scripts/runner_poll_github_tasks.py` | `_validation_checkout_metadata_lines` |
| `scripts/runner_poll_github_tasks.py` | `validate_pr_branch` |
| `scripts/runner_poll_github_tasks.py` | `telegram_approve_digest_is_signed` |
| `scripts/runner_poll_github_tasks.py` | `telegram_approve_audit_matches_request` |
| `scripts/runner_poll_github_tasks.py` | `process_issue` |
| `tests/test_runner_lease_store.py` | `test_completed_key_cannot_replay` |
| `tests/test_loop_engine.py` | `test_checkpoint_resume_across_engine_instances` |
| `tests/test_loop_runner_adapter.py` | `test_stale_expected_version_fails_closed` |
| `tests/test_runner_loop_control_executor.py` | `test_execute_loop_packet_uses_injected_route_dependencies` |
| `tests/test_loop_recovery_packet.py` | `test_missing_approval_is_rejected` |

## Findings

1. Checkpoint and resume state exists as a deterministic Loop primitive, but it is not yet the universal execution wrapper for normal Runner issue work.
   `core/loop_controller.py` defines `LoopState.CHECKPOINTED`, `LoopEvent.CHECKPOINT`, `LoopEvent.LEASE_EXPIRED`, and `advance_loop`; `core/loop_engine.py` persists those transitions through `LoopEngine.create` and `LoopEngine.step`; `core/loop_state_store.py` stores versioned `loop_runs` and `loop_events` through `LoopStateStore.initialize`, `LoopStateStore.create_run`, `LoopStateStore.append_result`, and `LoopStateStore.list_events`. `tests/test_loop_engine.py::test_checkpoint_resume_across_engine_instances` proves checkpoint/resume survives a new engine instance. The gap is wiring: `scripts/runner_poll_github_tasks.py::loop_engine_packet` is an allowlisted maintenance route to `core/runner_loop_control_executor.py::execute_loop_engine_packet`, while `scripts/runner_poll_github_tasks.py::process_issue` still dispatches ordinary code-generation and maintenance work outside a mandatory Loop run envelope.

2. Lease ownership and expiry exist in a separate local ledger, but that ledger is not acquired by the poller before executing a task.
   `core/runner_lease_store.py::RunnerLeaseStore.acquire` records task identity, attempt, token, heartbeat, and expiry. `RunnerLeaseStore.heartbeat`, `RunnerLeaseStore.save_checkpoint`, `RunnerLeaseStore.complete`, `RunnerLeaseStore.fail`, and `RunnerLeaseStore.reconcile` are token-bound and expiry-aware. `tests/test_runner_lease_store.py::test_completed_key_cannot_replay` and neighboring lease tests cover conflict, expiry, metadata mismatch, and replay blocking. Current `scripts/runner_poll_github_tasks.py` references the Loop packet route but does not import or call `RunnerLeaseStore`, so a GitHub label claim is still the live issue-level ownership mechanism for ordinary execution.

3. Replay and idempotency protections are partially present, with strongest coverage in the lease store and Loop version checks.
   `core/runner_lease_store.py::RunnerLeaseStore.acquire` blocks `COMPLETED_REPLAY_BLOCKED` and `IDEMPOTENCY_METADATA_MISMATCH`; `core/loop_engine.py::LoopEngine.step` rejects stale `expected_version`; `core/loop_state_store.py::LoopStateStore.append_result` enforces version and previous-context hash matching; `core/loop_runner_adapter.py::run_loop_task_packet` converts version conflicts into blocked public receipts. The gap is a single repository-wide idempotency key that binds GitHub issue number, base SHA, target branch, lease token, Loop run id, and final receipt before any executor side effect.

4. Recovery-comment authority is allowlisted by comment author, but not yet actor-matched to a signed recovery authority object.
   `scripts/runner_poll_github_tasks.py::trusted_runner_comment_authors` trusts the repository owner, `github-actions[bot]`, and optional `SKELETON_RUNNER_GITHUB_ACTOR`; `scripts/runner_poll_github_tasks.py::get_issue_comments` obtains prior comments; `scripts/runner_poll_github_tasks.py::process_issue` feeds those comments into retry-history parsing. `core/loop_recovery_packet.py::LoopRecoveryPacket.from_mapping` requires `approval_reference`, `idempotency_key`, explicit action, expected version, expected state, and public-safety boundary fields, but the poller does not yet bind a recovery comment's actor to that packet or prove the actor matches the original approval authority.

5. Issue-body approval is not accepted as execution authority for the Telegram merge path, and this should remain the model for S2 execution authority.
   `scripts/runner_poll_github_tasks.py::_body_field` reads fields from the issue body, but `telegram_approve_digest_is_signed`, `telegram_approve_audit_matches_request`, and the merge block checks require a signed callback digest, matching PR metadata, matching head SHA, and a recorded audit comment. A bare issue-body line is therefore not sufficient for merge execution. The S2 gap is that ordinary Runner issue execution still needs an equivalent signed approval or explicit recovery packet boundary instead of relying on body text plus labels as authority.

6. Receipt completeness is strongest in PR validation and Loop packet receipts, but failure recovery is not universal.
   `scripts/runner_poll_github_tasks.py::_validation_command_receipt_lines` captures command text, exit code, status, output tail, pytest totals, failing nodes, dependency hints, and failure phase. `_validation_checkout_metadata_lines` records base/head metadata and changed files, while `validate_pr_branch` records final clean status. `core/runner_loop_control_executor.py::loop_receipt_report` requires the Loop receipt schema, and `core/loop_runner_adapter.py` marks receipts public-safe with `external_side_effects_executed=False`. The gap is that ordinary executor failures are not always paired with a persisted checkpoint, lease terminal status, receipt hash, and recovery packet that can deterministically resume or stop the same run.

7. PR #1722 assumptions that are stale against this main:
   The repository now has `core/loop_controller.py`, `core/loop_engine.py`, `core/loop_state_store.py`, `core/loop_runner_adapter.py`, `core/runner_loop_control_executor.py`, `core/loop_recovery_packet.py`, and `core/runner_lease_store.py`, so any assumption that S2 lacks primitives for checkpoint/resume, expected-version replay checks, explicit recovery packets, or local lease persistence is stale. The still-valid assumption is that those primitives are not yet the universal Runner execution path: `scripts/runner_poll_github_tasks.py::process_issue` remains the live dispatcher and does not acquire `RunnerLeaseStore` or require a Loop packet for every task.

## Smallest Ordered Implementation Slices

1. Add an S2 run-envelope adapter around `scripts/runner_poll_github_tasks.py::process_issue` that derives one idempotency key from issue number, repository, branch, and base SHA, then creates or loads a `LoopStateStore` run before execution.
2. Acquire `core/runner_lease_store.py::RunnerLeaseStore.acquire` before any executor side effect, heartbeat while work is active, save checkpoints at deterministic boundaries, and call `complete` or `fail` with the final receipt hash.
3. Promote `core/loop_recovery_packet.py::LoopRecoveryPacket.from_mapping` into the poller recovery path and require actor matching between the recovery comment, original approval reference, and configured trusted actor.
4. Reject issue-body approval as execution authority for all S2 routes unless it is only metadata pointing to a signed approval record or explicit recovery packet.
5. Standardize final receipts so ordinary code-generation, maintenance, validation, and recovery all emit changed files, base/head SHA, command totals, clean status, lease attempt/token hash, Loop run id/version/context hash, and terminal recovery action.
6. Add replay tests that run the same issue twice, stale-version tests that race two packets, lease-expiry tests that resume from checkpoint, and actor-mismatch tests that prove allowlisted comments alone cannot authorize recovery.

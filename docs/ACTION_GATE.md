# Action Gate

`core/action_gate.py` is a stage 1 dry-run validation contract for repository
actions that require user approval. It accepts an `ActionGateRequest` and
returns an `ActionGateDecision`. It does not read pull requests, change a
repository, call network APIs, run shell commands or subprocesses, deploy, or
promote a decision into a live action.

Stage 1 only allowlists `merge_pull_request` requests for `alanua/Skeleton`.
Each request must be bound to:

- a positive `pr_number`;
- the exact `expected_head_sha` the operator reviewed;
- a non-empty `expected_files` list of repository-relative paths with no
  traversal or duplicates;
- `user_approved` set to true.

The decision is `allowed` only when every request field validates. A blocked
decision records validation reasons so an operator console can explain why no
future action would proceed.

## Future Operator Console

A future Telegram button flow can use this contract as the validation step
behind an operator console. The console should show the repository, pull
request number, reviewed head SHA, expected files, action type, and approval
state before any button can request a live repository action. Telegram button
input must not bypass the SHA or file-list checks: a pull request that changed
after review should require a fresh approval bound to its new head.

Stage 1 does not implement Telegram buttons or a live repository action route.
Any live stage must land separately with explicit operator review of network
calls, repository side effects, stale-head handling, and audit logging.

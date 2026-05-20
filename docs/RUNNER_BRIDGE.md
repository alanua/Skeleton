# Runner Bridge

`runner_bridge` stage 1 is a local dry-run contract for packaging work that may later be handed to the Runner task queue.

The current live execution route remains:

1. GitHub issue queue with `runner:ready`
2. Hetzner Runner poller
3. Codex execution
4. Draft PR
5. GitHub report
6. Telegram notification

Stage 1 does not create GitHub issues, call GitHub APIs, invoke Codex, execute shell commands, interact with systemd, or send Telegram notifications.

## Contract

`RunnerBridgeRequest` includes:

- `repo`
- `base_ref`
- `task_title`
- `task_body`
- `allowed_files`
- `protected_files`
- `validation_commands`
- `approval_marker`

`RunnerBridgeResult` includes:

- `status`
- `issue_number`
- `dry_run_summary`
- `blocked_reason`

## Dry-Run Validation

The bridge blocks when:

- `approval_marker` is missing or blank.
- Any protected file overlaps with `allowed_files`.
- Any requested validation command is not conservative and deterministic.
- Required text fields are blank.

Validation commands are parsed locally and never executed. The command tool must be one of `python`, `python3`, `pytest`, or `git`, and shell metacharacters are rejected.

## Rendered Output

For valid requests, the bridge renders the GitHub issue body that would be submitted to the existing issue queue route. The task payload is placed inside a fenced `task` block so it matches the current Runner poller extraction contract.

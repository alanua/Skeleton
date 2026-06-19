# Runner Queue Status

Status: GitHub issue queue is working.

Parser repair smoke status: 2026-06-12 docs-only Runner task completed normally.

This document records the current operating status and runtime checklist for the
Skeleton GitHub task queue Runner after the successful Telegram notification
smoke test.

## Validated flow

1. Operator approves a bounded task.
2. ChatGPT creates a GitHub issue with the `runner:ready` label.
3. The systemd timer picks up the issue.
4. The Runner changes the label from `runner:ready` to `runner:running`.
5. Codex runs in the checkout using the `workspace-write` sandbox.
6. The Runner posts a `DONE` or `BLOCKED` report to the issue.
7. The Runner changes the label to `runner:done` or `runner:blocked`.
8. Telegram notification is sent after completion.

Final report classification keeps explicit blockers authoritative. A `DONE`
report that also contains an unfenced `BLOCKED:` line or `NEEDS_OPERATOR`
action remains blocked, even when changed files or pytest output are present.
The only narrow exception is a target-project local-worktree `DONE` report with
`Local worktree bounded finalization: success`; that report may include an older
blocked status inside its quoted Codex-output fence without changing the final
Runner result.

## Labels

- `runner:ready`
- `runner:running`
- `runner:done`
- `runner:blocked`
- `runner:lane:default`
- `runner:lane:lane-1`
- `runner:lane:lane-2`

The `runner:lane:*` labels are allowlisted visibility markers. Runner applies
one when an issue body contains `Runner Lane:` metadata and repeats that lane
in its final `DONE` or `BLOCKED` issue report. The lifecycle labels still drive
pickup and completion; lane labels do not route or parallelize Runner work.

## Operator rule

Do not touch the repo while an issue is `runner:running`.

Do not run `git checkout`, `git pull`, `git reset`, `pytest`, cleanup with `rm`,
or `systemctl restart` while a task is running.

## Post-merge runtime sync checklist

1. Stop the timer and service before syncing runtime files manually.
2. Update the repo as the `agent` user, not as `root`.
3. Avoid using a `git safe.directory` workaround for `root`.
4. If local `main` diverged, create a backup branch before any hard reset.
5. Reset `main` to `origin/main` only after the backup branch exists.
6. Copy the updated systemd service file when the repo service file changes.
7. Run `systemctl daemon-reload`.
8. Start the timer.
9. Verify the service still has the expected `EnvironmentFile` line.
10. Verify the timer is active.

## Telegram notification status

- Telegram configuration lives only in the local Runner environment file.
- Do not put credentials in the repo, issues, or comments.
- Smoke test issue #31 confirmed the `DONE` notification.
- 2026-05-22: Worktree prompt fix merged; Telegram approve -> Runner merge pilot ready.
- Pilot-ready: Telegram approve can request the bounded Runner merge path.
- Signed pilot: callback HMAC config is active for Telegram approve.
- Telegram PR card approve writes the signed head-bound approval record Runner
  verifies before its bounded squash merge path; routine approval does not need
  an operator GitHub comment or merge retry.

## Cross-repo merge status

- 2026-06-07: BauClock PR 52 in `alanua/bauclock` was validated by Skeleton
  issue 823 and manually merged at
  `66c72948acc252ef8310d3c686ab73f840b64cd8`. No deploy was performed, and
  the production DB was not touched.

## Smoke test procedure

1. Create a no-op Runner issue.
2. Expect a Telegram message with the repository, issue number, and status.
3. Verify the GitHub issue comment and final label.

## Recovery notes

- Codex delivery status is taken from the final report heading or explicit
  `RESULT: DONE` / `RESULT: NEEDS_OPERATOR` contract line, not from ordinary
  status words inside docs, task text, logs, test names, or quoted output.
- To recover an already completed issue worktree, create an operator-approved
  runtime maintenance issue using
  `Maintenance Task ID: publish_existing_issue_worktree` with explicit
  `Target Repository`, `Source Issue`, `Base Branch`, `Output Branch`,
  `Draft PR: true`, and `Allowed Files` metadata.
- If an issue is stuck at `runner:running`, check `systemctl status` and
  `journalctl` for the Runner service.
- If a stale notification points to a closed issue or pull request, recreate the
  task as a new open issue with `runner:ready`; the poller silently ignores
  closed items and pull requests.
- Repeated stale Telegram notifications for closed issues or pull requests mean
  the notification guard or an old Runner process should be checked before
  creating more tasks.
- If an issue is blocked due to no commits, check whether the repo was touched
  while the Runner was active.
- If local `main` diverges after a squash merge, create a backup branch and reset
  `main` to `origin/main`.

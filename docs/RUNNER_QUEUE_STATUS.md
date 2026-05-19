# Runner Queue Status

## Current Status

The GitHub issue queue is working.

## Validated Flow

The current working task flow is:

1. Operator approves the task.
2. ChatGPT creates a GitHub issue with `runner:ready`.
3. The systemd timer picks up the issue.
4. The issue label changes from `runner:ready` to `runner:running`.
5. Codex runs in a workspace-write sandbox.
6. Runner posts a `DONE` or `BLOCKED` report.
7. The issue label changes to `runner:done` or `runner:blocked`.
8. A Telegram notification is sent after completion.

## Labels

Runner queue labels:

- `runner:ready`
- `runner:running`
- `runner:done`
- `runner:blocked`

## Operator Rule

Do not touch the repo while an issue is labeled `runner:running`.

While a task is running, do not run:

- `git checkout`
- `git pull`
- `git reset`
- `pytest`
- `rm` cleanup
- `systemctl restart`

## Post-Merge Runtime Sync Checklist

When syncing runtime files manually after a merge:

1. Stop the timer and service before syncing runtime files.
2. Update the repo as user `agent`, not as `root`.
3. Avoid using the `git safe.directory` workaround for `root`.
4. If local `main` diverged, create a backup branch before any hard reset.
5. Reset `main` to `origin/main` only after creating the backup branch.
6. If the systemd service file changed, copy the updated service file into place.
7. Run `systemctl daemon-reload`.
8. Start the timer.
9. Verify the service has the expected `EnvironmentFile` line.
10. Verify the timer is active.

## Telegram Notification Status

Telegram notification configuration is stored only in the local environment file on the Runner host.

Do not put credentials in this repo, GitHub issues, or GitHub comments.

Smoke test issue #31 confirmed the `DONE` notification.

## Smoke Test Procedure

To confirm the queue and notification path:

1. Create a no-op Runner issue.
2. Expect a Telegram message containing the repository, issue number, and status.
3. Verify the GitHub comment and final Runner label.

## Recovery Notes

If an issue is stuck at `runner:running`, check `systemctl status` and `journalctl` for the Runner service.

If an issue is blocked because there are no commits, check whether the repo was touched while Runner was active.

If local `main` diverges after a squash merge, create a backup branch and reset `main` to `origin/main`.

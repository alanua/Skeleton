# Runner Maintenance Tasks

Runtime maintenance tasks are host Runner actions. They are not Codex tasks:
Codex stays inside its workspace sandbox and must not be asked to reach systemd
or host runtime paths.

The Runner accepts a runtime maintenance issue only when the issue is explicitly
operator-approved and declares the maintenance mode and allowlisted task id:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: sync_telegram_callback_poller_runtime
```

Issue text is not a shell script. The host Runner dispatches only task ids that
exist in its code allowlist and ignores any command-looking text in the issue.
Missing or unknown maintenance task ids are reported as `BLOCKED`.

## Current allowlist

`sync_telegram_callback_poller_runtime` may only:

1. Stop `skeleton-telegram-callback-poll.timer` and
   `skeleton-telegram-callback-poll.service`.
2. Update the Runner checkout from `origin/main`.
3. Verify the callback poller script and callback poller systemd unit files
   exist.
4. Copy only `skeleton-telegram-callback-poll.service` and
   `skeleton-telegram-callback-poll.timer` into `/etc/systemd/system`.
5. Reload systemd, enable and start the callback timer, and run the callback
   service once.

The allowlist does not permit rebooting the host, package upgrades, arbitrary
commands from issue text, or unrelated services.

## Reporting

Each maintenance report must state `DONE` or `BLOCKED` accurately with safe
status lines only. A failed maintenance step is `BLOCKED`, and a report that
contains `BLOCKED` or `success_criteria=not_met` must not receive the
`runner:done` label.

Reports must not print token values or raw command output.

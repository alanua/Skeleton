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
5. Set root ownership and `0644` permissions on those copied unit files.
6. Reload systemd, enable and start the callback timer, and run the callback
   service once.
7. Verify the callback timer is active and the one-shot callback service result
   is successful before reporting `DONE`.

Every privileged host command uses non-interactive `sudo -n`; the Runner must
block instead of waiting for operator input.

`ensure_telegram_callback_local_config` may only:

1. Create `/etc/skeleton-runner.env` if it is missing without reading config
   from issue text.
2. Set root ownership and `0600` permissions on that local environment file.
3. Add a generated `SKELETON_TG_CALLBACK_HMAC_SECRET` when that setting is
   missing or blank, or leave an existing nonblank setting unchanged.
4. Verify the callback HMAC setting exists before reporting `DONE`.

`check_project_checkout` is read-only and must include target project metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: check_project_checkout
Target Project: skeleton
```

It may only:

1. Resolve `Target Project` through `PROJECT_TREE.yaml`.
2. Verify the registered `checkout_path` has no `..` components and resolves
   under `/home/agent/agent-dev/`.
3. Check whether the checkout path exists.
4. Check whether `checkout_path/.git` exists.
5. If the checkout exists, run only
   `git -C {checkout_path} remote get-url origin`.
6. Compare that origin URL with the repository registered in
   `PROJECT_TREE.yaml`.

It reports `DONE` only when the checkout exists, `.git` exists, and origin
matches the registered repository. Missing target metadata, unknown projects,
unsafe paths, missing checkouts, missing `.git`, failed origin reads, and remote
mismatches are reported as `BLOCKED`.

`ensure_project_checkout` prepares only a missing registered project checkout and
must include target project metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: ensure_project_checkout
Target Project: skeleton
```

It may only:

1. Resolve `Target Project` through `PROJECT_TREE.yaml`.
2. Use only the registered repository and registered `checkout_path`.
3. Verify the registered `checkout_path` has no `..` components and resolves
   under `/home/agent/agent-dev/`.
4. If the checkout already exists, run the same `.git` and origin checks as
   `check_project_checkout` without preparing anything.
5. If the checkout is missing, run only
   `git clone https://github.com/{registered_repo}.git {registered_checkout_path}`.
6. After preparation, verify the checkout exists, `.git` exists, and origin
   matches the repository registered in `PROJECT_TREE.yaml`.

It reports `DONE` only when the checkout exists, `.git` exists, and origin
matches the registered repository. It reports `BLOCKED` for missing target
metadata, unknown projects, unsafe paths, path traversal, existing checkouts
without `.git`, wrong remotes, clone failures, failed origin reads, and remote
mismatches after preparation.

The allowlist does not permit rebooting the host, package upgrades, arbitrary
commands or config values from issue text, or unrelated services.

## Reporting

Each maintenance report must state `DONE` or `BLOCKED` accurately with safe
status lines only. A failed maintenance step or failed runtime verification is
`BLOCKED`, and a report that contains `BLOCKED` or `success_criteria=not_met`
must not receive the `runner:done` label.

Reports must not print token values or raw command output.

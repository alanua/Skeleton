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

`validate_pr_branch` validates an open Skeleton PR branch and must include pull
request metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: validate_pr_branch
Pull Request: 123
Expected Head SHA: 0123456789abcdef0123456789abcdef01234567
Validation Profile: full_pytest
```

`Pull Request` is required. `Expected Head SHA` is optional but, when present,
must match the PR head reported by GitHub. `Validation Profile` is optional and
defaults to `full_pytest`; the only allowed values are `full_pytest` and
`knowledge_intake`.

It may only:

1. Query PR metadata with `gh pr view --repo alanua/Skeleton`.
2. Continue only when the PR is open and targets base branch `main`.
3. Use the PR head SHA from GitHub metadata, not branch names or commands from
   issue text.
4. Prepare a dedicated validation worktree under the configured Runner
   worktree root at `validate-pr-branch/pr-{number}`.
5. Fetch only the GitHub PR head ref for the requested PR and verify it matches
   the exact PR head SHA.
6. Check out the validation worktree detached at the exact PR head SHA and
   verify `HEAD` before tests run.
7. Run only the selected allowlisted validation profile:
   `full_pytest` runs `python3 -m pytest -q`; `knowledge_intake` runs
   `python3 -m pytest -q tests/test_knowledge_intake.py` followed by
   `python3 -m pytest -q`.

It reports `DONE` only when PR metadata, safe workspace preparation, exact head
verification, and every profile command succeeds. Missing or invalid PR numbers,
unsupported profiles, closed PRs, non-`main` base branches, expected head SHA
mismatches, unsafe validation paths, fetch or checkout failures, head mismatches,
and test failures are reported as `BLOCKED`. Failed validation profile commands
include the allowlisted command and a bounded, sanitized output block between
`failed_output_start` and `failed_output_end`; long output is truncated with an
explicit marker.

`check_skeleton_freshness` is a short status-only check before Skeleton project
work starts or after recent merges. It requires no target metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: check_skeleton_freshness
```

It may only:

1. Use the registered Skeleton checkout from `PROJECT_TREE.yaml`.
2. Verify the checkout path is safe using the same path rules as
   `check_project_checkout`.
3. Run only bounded Git and GitHub status queries:
   `git -C {checkout_path} remote get-url origin`,
   `git -C {checkout_path} fetch --prune origin main`,
   `git -C {checkout_path} rev-parse HEAD`,
   `git -C {checkout_path} rev-parse origin/main`,
   `git -C {checkout_path} ls-remote origin refs/heads/main`,
   `git -C {checkout_path} merge-base --is-ancestor`,
   `gh pr list --repo alanua/Skeleton --state open`, and
   `gh issue list --repo alanua/Skeleton --state open`.
4. Report whether GitHub `main` is the source of truth.
5. Report whether the live Runner checkout is equal to, ahead of, behind, or
   diverged from the current GitHub `main` SHA.
6. Report whether `docs/NOTEBOOKLM_SOURCEPACK.md` may need refresh when
   sourcepack or NotebookLM context is relevant.
7. Flag open PRs or issues that may need rebase, retest, or scope review against
   current `main`.
8. Remind that old chats and old branches are not canon.

It reports `DONE` when the freshness report was produced. It reports `BLOCKED`
for unsafe paths, missing checkouts, missing `.git`, failed origin reads, failed
GitHub `main` SHA reads, GitHub query failures, or any unclassified sync state.
The report must be short, human-readable, and must not include raw command
output.

The allowlist does not permit rebooting the host, package upgrades, arbitrary
commands or config values from issue text, or unrelated services.

## Reporting

Each maintenance report must state `DONE` or `BLOCKED` accurately with safe
status lines only. A failed maintenance step or failed runtime verification is
`BLOCKED`, and a report that contains `BLOCKED` or `success_criteria=not_met`
must not receive the `runner:done` label.

Reports must not print token values or raw command output.

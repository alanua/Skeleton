# Runner Queue Status

Status authority: GitHub issues, pull requests, labels, and merge state are
authoritative. This file is a public-safe operator mirror only. If this file and
GitHub disagree, re-check GitHub before acting.

Last public-safe refresh: 2026-07-09 from GitHub connector state for
`alanua/Skeleton`.

Privacy boundary: this document records only repository-safe issue and pull
request metadata. It excludes private Aufmass/customer data, local paths,
quantities, source documents, secrets, runtime artifacts, and connector-private
outputs.

## Current Queue Snapshot

| Status | Item | Public-safe summary | Current action |
| --- | --- | --- | --- |
| `RUNNING` | Issue #1668 | Refresh queue/status documentation and deterministic NotebookLM sourcepack after the recovery merge batch. | Complete this docs/state/sourcepack task only; close or merge nothing from this task. |
| `READY` | Issue #1669 | Publish the retained Loop recovery worktree produced by issue #1665. | Publish-only maintenance route for the completed two-file Loop recovery worktree. |
| `READY` | Issue #1666 | Add the bounded retained-worktree update route for existing draft PRs. | Required before updating Home Edge PR #1638 through issue #1640. |
| `READY` | Issue #1667 | Rebuild the read-only container-package validation workflow on current main. | Queued validation-only workflow task for public-safe package PRs. |
| `BLOCKED_VALIDATION` | PR #1632 | Draft n8n SQLite package PR. | Hold for the container-validation workflow from issue #1667 and exact-head validation; no deploy or merge. |
| `BLOCKED_VALIDATION` | PR #1635 | Draft Control Board replacement PR. | Hold for the container-validation workflow from issue #1667 and exact-head validation; no deploy or merge. |
| `DRAFT_REVIEW` | PR #1638 / issue #1640 | Home Edge visual-capture repair remains an open draft PR with a protected update task. | Wait until issue #1666 provides the existing-PR recovery route, then update PR #1638 only through issue #1640. |
| `SUPERSEDED` | Issue #1645 | Earlier retained queue/status worktree is superseded by issue #1668. | Retain as history; do not reuse stale text blindly. |
| `SUPERSEDED` | Issue #1643 / PR #1560 | Stale Loop recovery path is replaced by issue #1665 after successful replacement. | Retain as reviewed historical context only. |
| `BACKLOG` | Long-lived open planning/control issues | Items such as future DIOS, memory, Home Edge, and security cleanup work remain outside the immediate Runner chain. | Do not promote to executable Runner work without a fresh approved issue. |

## Completed Merge Batch

The following pull requests are recorded as closed and merged on 2026-07-09:

| PR | Title | Source issue/branch | Merged at |
| --- | --- | --- | --- |
| #1659 | Harden Aufmass room-to-contour matching on current main | Issue #1616 / `runner/issue-1616` | 2026-07-09T05:57:48Z |
| #1661 | Inventory and order Home Edge v1 modules on current main | Issue #1650 / `runner/issue-1650` | 2026-07-09T05:58:05Z |
| #1660 | Sanitize code-task finalizer validation environment | Issue #1634 / `runner/issue-1634` | 2026-07-09T05:59:16Z |
| #1654 | Rebuild Loop policy registry on current main | Issue #1642 / `runner/issue-1642` | 2026-07-09T05:59:52Z |
| #1647 | Complete Universal Runner Slice 6C on current main | Issue #1626 / `runner/issue-1626` | 2026-07-09T06:01:31Z |

Public-safe completion note: the Aufmass matcher entry records only the merged
repository-safe matcher work. It intentionally omits customer data, drawing
source references, quantities, private paths, and local artifacts.

## Active Chain

1. `RUNNING`: issue #1668 refreshes the queue/status docs, Skeleton handoff
   summary, and generated NotebookLM sourcepack.
2. `READY`: issue #1669 publishes the retained completed work from issue #1665
   into a draft PR. Issue #1665 itself is the Loop recovery implementation
   source and is not the publication route.
3. `READY`: issue #1666 adds the bounded route for updating an existing open
   same-repository draft PR without creating a duplicate PR.
4. `DRAFT_REVIEW`: issue #1640 then updates only existing Home Edge PR #1638
   after #1666 exists and the exact protected-route preconditions are met.
5. `READY`: issue #1667 rebuilds the read-only container-validation workflow.
6. `BLOCKED_VALIDATION`: PRs #1632 and #1635 remain draft validation-blocked
   until #1667 lands and exact-head validation completes.

No issue or pull request should be closed, merged, marked ready, or otherwise
mutated by this queue-status refresh task.

## Status Definitions

- `RUNNING`: GitHub currently labels the issue as active Runner work.
- `READY`: GitHub currently labels the issue as ready for Runner pickup or
  publish-only maintenance pickup.
- `BLOCKED_VALIDATION`: an open draft PR is waiting on a required validation
  capability or exact-head validation before review can proceed.
- `DRAFT_REVIEW`: an open draft PR exists and is waiting for targeted review or
  an approved repair route.
- `SUPERSEDED`: an issue or PR is retained as history but replaced by a newer
  approved route.
- `BACKLOG`: public-safe planning or control material with no current Runner
  execution authorization.

## Validated Runner Flow

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

## Operator Rule

Do not touch the repo while an issue is `runner:running`.

Do not run `git checkout`, `git pull`, `git reset`, `pytest`, cleanup with `rm`,
or `systemctl restart` while a task is running.

## Post-Merge Runtime Sync Checklist

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

## Recovery Notes

- Codex delivery status is taken from the final report heading or explicit
  `RESULT: DONE` / `RESULT: NEEDS_OPERATOR` contract line, not from ordinary
  status words inside docs, task text, logs, test names, or quoted output.
- To recover an already completed issue worktree, create an operator-approved
  runtime maintenance issue using
  `Maintenance Task ID: publish_existing_issue_worktree` with explicit
  `Target Repository`, `Source Issue`, `Base Branch`, `Output Branch`,
  `Draft PR: true`, and `Allowed Files` metadata.
- To update an existing draft PR from a retained worktree, use only the bounded
  route added by the approved existing-PR recovery task after it is merged.
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
- If local `main` diverges after a squash merge, create a backup branch and
  reset `main` to `origin/main`.

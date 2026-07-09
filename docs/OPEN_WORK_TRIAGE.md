# Open Work Triage

Status authority: GitHub issues, pull requests, labels, and merge state are
authoritative. This file is a public-safe triage mirror for issue #1668 and does
not close, merge, approve, or mutate any GitHub item.

Last public-safe refresh: 2026-07-09 from GitHub connector state for
`alanua/Skeleton`.

Privacy boundary: no private Aufmass/customer data, local paths, quantities,
source documents, credentials, runtime artifacts, or connector-private output is
recorded here.

## Immediate Chain

| Status | Item | Triage |
| --- | --- | --- |
| `RUNNING` | #1668 | Current queue/status, Skeleton handoff summary, and generated NotebookLM sourcepack refresh. |
| `READY` | #1669 | Publish retained Loop recovery output from #1665; this is publish-only and should not rerun Codex. |
| `READY` | #1666 | Add the bounded route for updating existing draft PR branches from retained issue worktrees. |
| `DRAFT_REVIEW` | PR #1638 / issue #1640 | Home Edge protected repair remains draft review until #1666 enables the approved existing-PR update route. |
| `READY` | #1667 | Rebuild the read-only container-validation workflow for public-safe package PRs. |
| `BLOCKED_VALIDATION` | PR #1632 | n8n SQLite package draft waits for #1667 and exact-head validation. |
| `BLOCKED_VALIDATION` | PR #1635 | Control Board replacement draft waits for #1667 and exact-head validation. |

## Completed Work

| Status | Item | Result |
| --- | --- | --- |
| `MERGED` | PR #1659 | Aufmass matcher hardening merged on 2026-07-09. |
| `MERGED` | PR #1661 | Home Edge v1 module inventory and ordering merged on 2026-07-09. |
| `MERGED` | PR #1660 | Code-task finalizer validation-environment sanitizer merged on 2026-07-09. |
| `MERGED` | PR #1654 | Loop policy registry rebuild merged on 2026-07-09. |
| `MERGED` | PR #1647 | Universal Runner Slice 6C merged on 2026-07-09. |

## Retained Or Superseded

| Status | Item | Triage |
| --- | --- | --- |
| `SUPERSEDED` | #1645 | Stale retained queue/status worktree superseded by #1668. |
| `SUPERSEDED` | #1643 / PR #1560 | Stale Loop recovery path replaced by #1665 and publish-only #1669. |
| `SUPERSEDED` | Older package PRs #1597 and #1596 | Replaced by current-main drafts #1632 and #1635. |

## Backlog Boundary

`BACKLOG` items are public-safe planning or control records without immediate
Runner authorization. They require a fresh approved issue before execution.
Examples include future DIOS follow-up work, memory write tasks, Home Edge
runtime tasks, and security cleanup planning.

## Triage Rules

- Use GitHub as the source of truth for labels, open/closed state, draft state,
  runner state, and merge history.
- Use this file and `projects/skeleton/STATE.yaml` only as handoff summaries.
- Do not infer private project facts from public queue entries.
- Do not expose Aufmass/customer source data, paths, quantities, or local
  artifacts in public docs.
- Do not close, merge, mark ready, or otherwise mutate any issue or PR from this
  documentation refresh task.

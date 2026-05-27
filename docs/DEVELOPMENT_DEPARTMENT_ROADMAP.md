# Development Department Roadmap

Status: public-safe planning artifact
Scope: managed development department built on Skeleton

## Purpose

The development department is the controlled human-led system for turning
operator intent into reviewed changes. Skeleton provides the rules, issue
queue, worker boundaries, review gates, and source-of-truth discipline. The
department uses automation, but it does not hand merge, deploy, secrets, or
runtime authority to autonomous agents.

The previous phone-first-only task (#583 / PR #584) was the wrong scope and was
closed without merge. Phone-first operation remains important because Oleksii
mostly operates from a phone, but it is one constraint inside the larger managed department plan.

## Roles

- Oleksii: operator, product owner, approval authority, and final human gate for
  risky actions.
- ChatGPT: planner, scope controller, issue drafter, reviewer of proposed
  changes, and human-readable status layer.
- Runner: controlled execution service that picks approved GitHub issues,
  manages bounded worktrees, runs workers, posts results, and reports status.
- Codex: bounded code/documentation executor for narrow tasks in the assigned
  worktree.
- OpenHands: future larger coding worker only after its bridge and approval
  rules are connected.
- Antigravity: later controlled development cockpit or worker candidate; it is
  not a source of truth and must not bypass GitHub, tests, PR review, or human
  approval.
- Gemini: audit and review helper unless explicitly assigned a bounded
  different role.
- Claude: helper or adapter participant with the same source-of-truth and
  approval boundaries as other helpers.
- Telegram: phone-first notification and approval surface, not canon and not a
  replacement for GitHub records.
- GitHub Issues: task intake, queue, scope, labels, Runner status, and durable
  task record.
- GitHub PRs: reviewed change record, test evidence, approval discussion, and
  merge boundary.

## Controlled Chain

1. Intake starts with an operator request or a documented project need.
2. ChatGPT converts the request into a bounded plan or GitHub issue.
3. Oleksii approves the safe next step or explicitly approves a risky route.
4. GitHub Issues carry the task scope and queue labels.
5. Runner picks an approved issue and assigns a bounded worker such as Codex.
6. The worker changes only the allowed files in the assigned worktree.
7. Tests and `git diff --check` validate the result.
8. Runner posts `DONE` or `BLOCKED` with changed files and validation.
9. A GitHub PR carries the diff, test evidence, and review discussion.
10. ChatGPT, Gemini, Claude, or a human reviewer can inspect the PR inside their
    assigned role.
11. Oleksii gives small but real approval for merge, deploy, secrets, runtime,
    or fallback actions.
12. Merge happens only through the approved route and only after the reviewed head SHA is known.

## Phone-First Constraint

Phone-first means the control surface must be short, readable, and safe from a
phone. Telegram messages should summarize status, required decision, risk, and
next safe action.

Phone-first does not mean the whole system is Telegram. GitHub `main` remains
source of truth, GitHub Issues remain the durable queue, GitHub PRs remain the
review boundary, and tests remain required evidence.

## Antigravity Use Later

Antigravity should be introduced later as a controlled development cockpit or a
bounded worker candidate. It can help inspect work, coordinate tasks, or execute
bounded changes after adapters and approval rules exist.

Antigravity must not become canon. It must not merge, deploy, handle secrets, change runtime services, rewrite source-of-truth rules, or replace GitHub
Issues and PRs. Any Antigravity automation needs a separate reviewed task.

## Old PR Backlog

Old PRs must be audited against current `main`.

For each old PR:

- close it if it is superseded;
- recreate it from current `main` if the idea is still useful but the branch is
  stale;
- rebase only when the scope is small and the reviewed result remains clear;
- never merge stale code just because it already exists.

The stale phone-first-only PR #584 stays closed because it covered only one
constraint instead of the full Skeleton build and department roadmap.

## Publisher And Delivery Route

The publisher/delivery route must be rebuilt cleanly from current `main`.
Superseded publisher paths from old branches are not trusted.

The rebuilt route must start as documentation and testable boundaries before any
implementation. It must preserve small human approval gates and must not add
autonomous deploy, merge, secrets, runtime, Telegram callback, dashboard, or
service restart behavior as a side effect.

## Approval Gates

Human approval gates should be small but real:

- approve task scope before work starts;
- approve risky actions separately;
- approve merge based on PR, tests, and exact head SHA;
- approve deploy/runtime/secrets/service actions separately;
- require manual fallback merge documentation with exact head SHA validation.

## Phased Roadmap

### Phase 0: Source Of Truth And Boot Route Stabilization

Keep GitHub `main` authoritative and keep `BOOT_MANIFEST.yaml` as the boot
entrypoint. Make stale handoff state visibly non-canon.

### Phase 1: Rules And Operating Standard Visibility

Keep the project operating standard, command meanings, memory routing, and
`OPERATOR_RULES.yaml` registry visible. The registry remains a registry, not a
runtime gate.

### Phase 2: Reliable Task Queue And Delivery Route

Stabilize issue intake, Runner pickup, worker execution, tests, PR creation,
review, and result reporting. Rebuild the publisher/delivery route from current
`main`.

### Phase 3: PR Backlog Cleanup And Review Discipline

Audit stale PRs, close superseded work, recreate useful work cleanly, and keep
review focused on current code.

### Phase 4: Managed Roles And Worker Selection

Define which helper is planning, executing, auditing, reviewing, notifying, or
approving. Select workers per task by risk, scope, cost, and available adapter
boundaries.

### Phase 5: Antigravity, OpenHands, Gemini, And Claude Adapters

Discover and document adapters before expanding automation. Keep every helper
inside GitHub issue and PR boundaries.

### Phase 6: Dashboard And State Reporting

Add a development department status report or dashboard later. It should show
queue status, PR status, stale backlog, validation state, and next approvals.
It should report state from canon sources, not become canon.

### Phase 7: Future Jeeves Bridge

Bridge to future Jeeves only after Skeleton control routes are stable. Jeeves
remains a separate future assistant product and runtime.

## Current Next Steps

1. Audit open stale PRs.
2. Rebuild the clean publisher/delivery route from current `main`.
3. Document the manual fallback route with exact head SHA validation.
4. Add a development department status report or dashboard later.
5. Only then expand worker automation.

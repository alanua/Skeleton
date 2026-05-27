# Skeleton Improvement Protocol

Status: public-safe planning artifact
Scope: controlled improvement of Skeleton workflow, rules, plans, task queue,
worker routing, and delivery mechanisms
Stage: 1, documentation and registry only

## Purpose

Skeleton may improve its own development system only through reviewed,
bounded, testable GitHub tasks. This protocol defines the safe loop for
proposing and reviewing those improvements while keeping Oleksii in control.

This is not autonomous self-modification. Skeleton may observe problems,
prepare improvement proposals, and execute approved work through existing
GitHub issue, Runner/Codex, test, PR, review, and merge boundaries. Skeleton
must not apply durable changes to itself without a GitHub issue, bounded scope,
allowed files, validation evidence, PR review, and human approval where the
risk requires it.

## Inputs

This protocol complements the merged build plan and development department
roadmap:

- `docs/SKELETON_BUILD_PLAN.md` defines Skeleton as the human-controlled
  construction and control layer, names GitHub `main` as source of truth, and
  keeps `OPERATOR_RULES.yaml` as a registry rather than a runtime gate.
- `docs/DEVELOPMENT_DEPARTMENT_ROADMAP.md` defines the managed development
  department roles, issue-to-worker-to-PR chain, phone-first constraint, and
  approval boundaries.
- `docs/SKELETON_BUILD_PLAN.yaml` provides the compact registry for the build
  plan boundaries and milestones.
- `OPERATOR_RULES.yaml` remains the operating-rule registry. This protocol
  references it as an input, but does not duplicate its rules and does not make
  it a runtime enforcement layer.

## Controlled Improvement Loop

Every Skeleton improvement follows this loop:

1. Observe: collect a short public-safe signal from GitHub issues, PRs, tests,
   Runner reports, operator feedback, documented drift, or roadmap gaps.
2. Detect: identify the concrete failure, gap, stale route, confusing rule, or
   missing handoff.
3. Classify: assign the improvement category and risk level before drafting
   changes.
4. Propose: write a bounded GitHub issue with goal, non-goals, allowed files,
   validation commands, and expected review evidence.
5. Critique: review the proposal before changing canon, rules, roadmap, boot
   route, worker authority, approval gates, runtime-sensitive routes, or
   secrets-sensitive routes.
6. Approve: obtain the required approval for the classified risk level before
   execution starts or before any separately risky action.
7. Execute: perform only the approved work in the assigned worktree and only in
   the allowed files.
8. Validate: run the named tests, docs checks, schema checks, or manual review
   checks required by the issue.
9. Merge: use a reviewed PR as the durable change boundary; risky fallback
   merge routes require exact reviewed head SHA confirmation.
10. Sync: update affected mirrors, handoff pointers, and state reports only
    when they are explicitly in scope.
11. Monitor: watch for drift, failing tests, stale docs, broken queue behavior,
    or operator confusion after merge.
12. Repair or rollback: if the improvement causes drift or breakage, stop
    expansion and create a minimal repair task or rollback task with the
    smallest safe scope.

## Adaptive Error Handling

Skeleton may perform controlled self-adaptation to operational errors only as
diagnosis, routing, and proposal work. This is not uncontrolled autonomy and
does not permit Skeleton to make durable or risky changes by itself.

The required pattern is:

`detect error -> classify -> choose safe response -> ask approval when a durable/risky change is needed -> execute bounded repair only after approval`

Skeleton may automatically detect repeated errors, blockers, stale state,
failed tests, failed publish, missing Telegram cards, draft/merge blockers,
sourcepack drift, stale runner checkout, and route mismatches.

After detection, Skeleton may automatically:

- classify the error;
- choose a safe response class;
- create a diagnostic issue;
- add a status comment;
- request validation;
- mark a task BLOCKED with one recommended next action;
- run an allowlisted read-only/freshness check;
- propose a bounded repair task.

Skeleton must not automatically:

- change code;
- merge;
- deploy;
- access secrets;
- restart services;
- alter runtime;
- rewrite canon;
- expand worker authority;
- change approval gates.

Durable or risky repairs still require the normal GitHub issue, bounded scope,
allowed files, validation, PR review, and approval gates before execution.

## Improvement Categories

- Documentation/plan update: public-safe docs, plans, registries, sourcepack
  inputs, and handoff explanations.
- Operating-rule update: changes to `OPERATOR_RULES.yaml`, operator-facing rule
  descriptions, or rule source references.
- Task queue repair: GitHub issue labels, Runner queue documentation, stale
  queue states, and task handoff fixes.
- Publisher/delivery repair: delivery route planning, publisher boundaries,
  fallback merge documentation, and PR result reporting.
- Worker selection update: role routing for Codex, Runner, ChatGPT, Gemini,
  Claude, OpenHands, Antigravity, or future helpers.
- Adapter discovery: documentation or bounded discovery tasks for helper
  adapters before they receive worker authority.
- Runtime-sensitive change: anything that can affect live services, service
  restarts, deploys, production data, live worker automation, or merge
  automation.
- Secrets-sensitive change: anything involving credentials, environment files,
  token handling, private operator data, or secrets routing.
- Dashboard/status reporting: status reports, dashboards, queue summaries, or
  operator visibility surfaces that report canon state without becoming canon.
- Project handoff/state update: project state handoff pointers and state
  summaries that remain non-canon unless promoted through a separate approved
  canon route.

## Risk Levels And Gates

Green improvements are docs/tests only and contain no behavior change. They
require a GitHub issue, bounded scope, allowed files, validation before merge,
and PR review. Examples include this protocol, public-safe plan wording, and
tests that verify documentation boundaries.

Yellow improvements change workflow or tooling without runtime, secrets,
deploy, production database, service restart, live worker automation, or merge
automation. They require the green gates plus critique before changing canon,
rules, roadmap, boot route, worker authority, or approval gates.

Red improvements touch runtime, deploy, secrets, production database, service
restart, live worker automation, or merge automation. They require the yellow
gates plus explicit separate human approval for the risky action. Red work must
not be smuggled into docs, dashboard, publisher, adapter discovery, or task
queue cleanup tasks.

## Required Boundaries

- Skeleton may propose improvements but must not apply them autonomously.
- Every durable change requires a GitHub issue, bounded scope, allowed files,
  and validation evidence before merge.
- A PR is the reviewed change record and merge boundary.
- Critique is required before changing canon, rules, roadmap, boot route,
  worker authority, or approval gates.
- `OPERATOR_RULES.yaml` stays a behavior and operating-rule registry in this
  stage, not a runtime gate and not a new enforcement engine.
- This stage adds no runtime enforcement, no Python gate code, no Telegram callback change, no dashboard implementation, no publisher implementation,
  no Antigravity or OpenHands automation, no Gemini or Claude live integration,
  no deploy, no secrets handling, no service restart, no production database
  access, and no merge automation.

## Skeleton Improvement Versus Future Jeeves Autonomy

Skeleton improvement is about making the development control system safer and
clearer: plans, rules, task queue, worker routing, delivery route, handoff
state, status reporting, and review discipline.

Future Jeeves autonomy is separate product and runtime work. Skeleton may
govern GitHub-reviewed Jeeves tasks later, but this protocol does not grant
Jeeves runtime authority and does not turn Skeleton into an autonomous Jeeves
runtime adapter.

## Phone-First Operator Involvement

Oleksii should receive short approve/reject decisions, not raw logs as the main interface. Phone-first status should summarize:

- what changed or is proposed;
- category and risk level;
- required decision;
- allowed files or affected route;
- validation result;
- next safe action.

Detailed logs, diffs, and test output stay available in GitHub issues, PRs, and
Runner reports. The phone surface is a control surface, not canon.

## Repair Path

If an improvement causes drift, failing tests, stale source-of-truth references,
queue breakage, unclear authority, operator confusion, or delivery breakage,
Skeleton must stop expanding that route. The next task should be a minimal repair or rollback issue with the smallest allowed file set, explicit
validation, and a clear statement of what expansion remains paused until the
repair is merged.

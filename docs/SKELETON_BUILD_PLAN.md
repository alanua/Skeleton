# Skeleton Build Plan

Status: public-safe planning artifact
Scope: Skeleton itself, not product runtime implementation

Historical/foundation notice: this document remains retained history and
foundation context. This document is repository canon only while present on
reviewed GitHub `main`; the active target architecture is
`docs/SKELETON_ARCHITECTURE_VNEXT.md` when that file is present on reviewed
GitHub `main`.

## Purpose

Skeleton is the human-controlled construction and control layer for project
work. It keeps boot rules, source priority, project manifests, operating
standards, issue routing, Runner/Codex execution boundaries, review gates, and
handoff records in one GitHub-governed system.

Skeleton is not the product being built. It is not Jeeves, not a deployment
runtime, not a secrets store, not an autonomous merge system, and not a live
assistant runtime. It provides the controlled development loop around products
and projects.

Jeeves is a separate future assistant product and runtime. Skeleton may build
and govern tasks for future Jeeves work, but Skeleton must not become the
Jeeves runtime and Jeeves must not be treated as a Skeleton runtime adapter.

## Source Of Truth And Boot Route

GitHub `main` in `alanua/Skeleton` is the source of truth for Skeleton code,
documentation, rules, manifests, reviewed state, issues, pull requests, labels,
runner state, and merge history.

The boot entrypoint is `BOOT_MANIFEST.yaml`. The current boot read order starts
there and then reads `COMMANDS.yaml`, `OPERATOR_RULES.yaml`, `MODES.yaml`,
`SOURCE_REGISTRY.yaml`, `MEMORY_ROUTING.yaml`, `PROJECT_INDEX.yaml`, and
`STATUS_CODES.yaml`.

`projects/skeleton/PROJECT_OPERATING_STANDARD.md` is the current human operating
standard. `projects/skeleton/STATE.yaml` is handoff state only, not canon truth.
NotebookLM is a read-only mirror. Model memory and old chats are weak cache.

`OPERATOR_RULES.yaml` is a stage 1 registry of operator-facing rules. It is
not a runtime gate, not a second canon store, and not an autonomous enforcement layer. Runtime enforcement can only be added later through a separate approved
task.

## Completed Foundation

- PR #573 repaired Codex/Runner result status detection.
- PR #579 added the stage 1 `OPERATOR_RULES.yaml` registry, updated
  `BOOT_MANIFEST.yaml` read order, refreshed docs and tests, and refreshed the
  NotebookLM sourcepack.
- Issue #580 validated PR #579 with `full_pytest` before manual fallback merge.
- Issue #581 synced the live Runner checkout and Telegram callback poller
  runtime to `origin/main`.
- Issues #558 and #561 stale publisher paths were closed as superseded.
- The project operating standard exists at
  `projects/skeleton/PROJECT_OPERATING_STANDARD.md`.
- Runner/Codex task routing exists, but must stay bounded by GitHub issues,
  tests, PR review, explicit approval, and merge boundaries.

## Current Blockers And Risks

- Old PRs and branches may be stale relative to current `main`.
- The publisher/delivery route must be rebuilt from current `main`; old
  publisher plans are not trusted.
- Manual fallback merge procedures need an exact head SHA validation note so the
  operator can confirm the reviewed commit before any fallback merge.
- Phone-first operation matters because Oleksii mostly operates from a phone,
  but it is only a constraint on the control surface. It is not the whole
  Skeleton plan.
- Antigravity, OpenHands, Gemini, Claude, and other helpers need clear adapter
  boundaries before they are allowed to act as workers or reviewers.
- No task may silently add autonomous merge, deploy, secrets, live runtime,
  Telegram callback, publisher, dashboard, or service restart behavior.

## Planned Phases

### Phase 0: Source Of Truth And Boot Route Stabilization

Keep GitHub `main` as canon. Keep `BOOT_MANIFEST.yaml` as the entrypoint. Keep
the operating standard and source priority visible. Treat `STATE.yaml` as
handoff state only.

### Phase 1: Rules And Operating Standard Visibility

Make the operating standard, commands, memory routing, and operator rules easy
to find and verify. Keep `OPERATOR_RULES.yaml` as a registry, not a runtime
gate, until a separate approved implementation exists.

### Phase 2: Reliable Task Queue And Delivery Route

Keep the GitHub issue to Runner to Codex to tests to PR chain reliable. Rebuild
the publisher/delivery route from current `main` instead of reviving stale
publisher branches.

### Phase 3: PR Backlog Cleanup And Review Discipline

Audit open stale PRs against current `main`. Close superseded work, rebase or
recreate still-useful work in small branches, and keep every PR tied to tests
and explicit review.

### Phase 4: Managed Development Department Roles

Document how Oleksii, ChatGPT, Runner, Codex, OpenHands, Antigravity, Gemini,
Claude, Telegram, GitHub Issues, and GitHub PRs cooperate without creating
unclear authority or autonomous action.

### Phase 5: Adapter Discovery

Evaluate Antigravity, OpenHands, Gemini, and Claude through controlled adapter
or discovery tasks. Antigravity may later become a controlled development
cockpit or worker candidate, but not a source of truth.

### Phase 6: Dashboard And State Reporting

Add a development department status report or dashboard later, after the source
of truth, queue, delivery route, and approval gates are stable. The dashboard
must report state; it must not become canon by itself.

### Phase 7: Future Jeeves Bridge

Keep future Jeeves bridge work separate from Skeleton. Skeleton can govern
Jeeves tasks and handoff policy, but Jeeves product/runtime code and runtime
decisions remain separate.

## Next Practical Milestones

1. Audit open stale PRs and decide close, recreate, or rebase from current
   `main`.
2. Rebuild a clean publisher/delivery route from current `main`.
3. Document the manual fallback route with exact head SHA validation before any
   fallback merge.
4. Add a development department status report or dashboard later, after the
   queue and delivery route are stable.
5. Only then expand worker automation.

## Explicit Non-Goals For This Plan

- No runtime integration.
- No Telegram callback code change.
- No Antigravity automation.
- No publisher implementation.
- No dashboard implementation.
- No deploy.
- No service restart.
- No secrets or environment handling.
- No merge automation.
- No autonomous agent behavior.

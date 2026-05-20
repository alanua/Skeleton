# Jeeves Bridge

Status: DRAFT_HANDOFF_READY
Scope: Skeleton control-layer bridge rules for Jeeves handoff planning

## Boundary

Skeleton is the control and construction layer. It owns Skeleton boot rules,
task routing, write gates, project handoff records, runner governance, and
bridge policy.

Jeeves is a separate future assistant product and runtime. It remains outside
Skeleton as a product/runtime project, with its own repository and future
implementation decisions.

Skeleton may govern tasks for Jeeves by recording public-safe handoff rules,
review routes, and approval gates. Skeleton must not become the Jeeves runtime,
and Jeeves must not be treated as a Skeleton runtime adapter.

This bridge document makes no code migration, no deployment, no secret access,
no live runtime access, and no changes to `alanua/jeeves`.

## Source Of Truth

`alanua/Skeleton` is canonical for Skeleton control rules, construction rules,
project handoff records, and Skeleton-to-Jeeves bridge policy.

`alanua/jeeves` remains canonical for Jeeves runtime code, product code,
runtime architecture, deployment behavior, and product decisions.

NotebookLM is advisory only. NotebookLM may summarize mirrored source material,
but it is not canonical for source files, issues, pull requests, labels, runner
state, merge history, runtime behavior, or deployment decisions.

## Approval Boundaries

Any write to the Jeeves repository requires explicit operator approval.

Any Jeeves runtime, deployment, secrets, server, credential, or live product
access requires explicit operator approval.

Any future bridge automation requires a separate pull request and review. This
includes automation that reads from or writes to `alanua/jeeves`, opens Jeeves
issues or pull requests, touches a live runtime, or promotes a documentation
route into an executable route.

## Allowed Skeleton Work

Skeleton may keep public-safe documentation about:

- The Skeleton/Jeeves boundary.
- Handoff and approval rules.
- Future review steps.
- Task governance for Jeeves work.
- Non-runtime source routing decisions.

Skeleton may not use this bridge to migrate Jeeves runtime code, deploy Jeeves,
access Jeeves secrets, operate Jeeves servers, or edit `alanua/jeeves` without
explicit operator approval.

## Current Handoff Route

The current handoff is documentation-only:

1. Keep `projects/jeeves/PROJECT_MANIFEST.yaml` as the Skeleton project route
   for Jeeves handoff context.
2. Keep `projects/jeeves/STATE.yaml` as handoff/status information, not full
   canon truth for the Jeeves product.
3. Use this document for bridge boundary and approval rules.
4. Review `alanua/jeeves` only after explicit operator approval when exact
   runtime source routing is needed.

## Future Safe Steps

Future Jeeves bridge work should proceed in separate reviewed steps:

1. Review `alanua/jeeves` source after explicit operator approval.
2. Add the exact Jeeves runtime source route once confirmed.
3. Prepare a stage 1 documentation-only bridge in `alanua/jeeves` only after
   explicit operator approval.
4. Keep any runtime, deployment, secrets, or server changes out of Skeleton
   bridge documentation work.

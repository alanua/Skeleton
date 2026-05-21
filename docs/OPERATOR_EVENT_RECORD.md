# Operator Event Record

`core/operator_event.py` is the stage 1 dry-run record model for public-safe
operator-console events. It captures enough review metadata for a later audit
trail without posting an issue comment, calling GitHub, reading chat memory,
running subprocesses, deploying, or writing repository files.

An event is bound to:

- the `alanua/Skeleton` repository;
- the GitHub issue number that owns the operator interaction;
- the pull request number and reviewed head SHA;
- one bounded action name and operator-console event type;
- the dry-run validation result, source, public actor reference, UTC timestamp,
  and bounded public-safe summary.

The model renders a deterministic dictionary shape documented by
`schemas/operator_event.schema.json` and deterministic public-safe issue
comment text. Stage 1 returns that text only. A later operator-approved stage
may decide where to publish it after live GitHub write behavior is reviewed.

Operator-console events must be recorded to a public-safe audit trail. Future
ChatGPT sessions should reconstruct what happened from GitHub issues, pull
requests, and the recorded audit trail rather than relying on chat memory.
Event summaries must describe public workflow state only; they must not carry
credentials, private runner state, source contents, customer data, or private
project details.

Validation rejects malformed repository bindings, non-positive issue and pull
request numbers, malformed SHAs, unsupported event types/results/sources,
unbounded action or actor identifiers, non-UTC timestamps, empty summaries,
and summaries above the stage 1 size bound. The rendering path has no network,
subprocess, deploy, or repository-write side effect.

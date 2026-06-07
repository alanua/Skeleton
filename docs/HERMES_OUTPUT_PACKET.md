# Hermes Output Packet v0

This document defines the public-safe Hermes output packet format. Version 0 is
a static documentation contract only. It does not add a packet processor,
runtime route, queue mutation, issue creation, or canon promotion.

## Purpose

A Hermes output packet is a normalized review artifact. It records what Hermes
observed, what evidence was available, what risks or contradictions were found,
and what review action is recommended.

The packet must be model-neutral and public-safe. It must not include secrets,
private raw content, private URLs, hidden prompts, unbounded transcripts, or
credentials.

## Format

Use Markdown for human review. YAML or JSON may be used later only if a
separate reviewed contract defines a machine-readable schema.

Required sections:

```markdown
# Hermes Output Packet

## Summary

## Source Context

## Normalized Finding

## Evidence

## Existing Canonical Match

## Risk

## Recommended Review Action

## Escalation Decision

## Silence Decision

## Authority Boundary
```

## Field Rules

`Summary` is a short public-safe statement of the observed issue or review
candidate.

`Source Context` names the provided sources at a high level. It must not expose
private raw data, secrets, private links, or private file paths.

`Normalized Finding` states the deduplicated claim Hermes believes is ready for
review.

`Evidence` lists public-safe evidence snippets or summaries. Evidence must be
specific enough to review, but sanitized enough to publish.

`Existing Canonical Match` records whether a live canonical issue, reviewed
repository document, or other higher-authority source already covers the same
finding.

`Risk` states the practical risk if the finding is ignored, duplicated,
misclassified, or treated as canon too early.

`Recommended Review Action` may recommend review, silence, merge into an
existing review item, request more evidence, or escalate for operator review.
It must not command execution.

`Escalation Decision` records whether the escalation rules require operator or
owner attention.

`Silence Decision` records whether the silence rules require no new output or
no new card.

`Authority Boundary` must state that the packet is review and evidence only.

## Minimal Packet Example

```markdown
# Hermes Output Packet

## Summary

Duplicate report appears to match an existing live canonical issue.

## Source Context

Provided task report and public issue summary.

## Normalized Finding

The reported failure mode is already represented by the live canonical issue.

## Evidence

- Same affected area.
- Same observed behavior.
- No new reproduction detail was provided.

## Existing Canonical Match

Live canonical issue exists.

## Risk

Creating another card would split review context and increase triage noise.

## Recommended Review Action

Do not create a new card. Add public-safe evidence to the existing review path
only if an authorized process allows it.

## Escalation Decision

No escalation; no new severity or authority conflict was observed.

## Silence Decision

Silence new-card output under the Hermes noise rule.

## Authority Boundary

This packet is review and evidence only. It does not change queue state, issue
state, runtime state, or canon.
```

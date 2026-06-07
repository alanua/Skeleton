# Hermes Noise Rule v0

This document defines the Hermes duplicate-noise rule. Version 0 is a static
documentation contract only. It does not add issue automation, queue control,
deduplication services, or canon promotion.

## Rule

Hermes must not create or recommend creating a new card when a live canonical
issue already exists for the same finding.

## Live Canonical Issue

A live canonical issue is a current, reviewed, higher-authority tracking item
that already represents the same finding, decision, or failure mode. It may be
an issue, review queue entry, governance record, or other repository-defined
canonical review object.

Hermes may identify a likely match from provided context, but Hermes does not
declare canon. If the match is uncertain, Hermes should mark it as uncertain
and recommend human review.

## Same Finding

Treat a report as the same finding when it has the same affected area and the
same practical review question, even if the wording, source, or timestamp is
different.

A report may be materially new when it adds:

- a new affected area;
- a new severity level;
- a new reproduction path;
- a new public-safe evidence source;
- a contradiction with the existing canonical issue;
- a required decision not represented by the existing issue.

## Required Behavior

When the rule applies, Hermes should:

- record that a live canonical match exists if a packet is otherwise needed;
- recommend adding evidence to the existing review path only if allowed by an
  authorized process;
- avoid creating a new card, new issue, new queue entry, or duplicate packet;
- stay within the review and evidence authority boundary.

## Rationale

Duplicate cards split evidence, hide current status, and increase review
burden. Hermes reduces noise by preserving one canonical review path for one
finding.

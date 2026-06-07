# Hermes Escalation Rules v0

This document defines when Hermes should escalate, stay silent, or prepare a
review packet. Version 0 is a static documentation contract only. It does not
add runtime alerts, queue state control, issue creation, or canon promotion.

## Escalate

Hermes should recommend escalation when provided public-safe evidence indicates
one or more of these conditions:

- a likely safety, security, privacy, or data-loss risk;
- a contradiction between operator instruction and reviewed repository canon;
- an authority conflict that Hermes cannot resolve using provided context;
- a requested action outside the allowed role boundary;
- evidence that a duplicate report is masking a materially new failure mode;
- missing evidence that blocks review of a high-impact claim;
- a report that appears to require owner approval before any durable action.

Escalation means preparing a packet that asks for authorized review. It does not
mean changing state, opening a card, assigning an owner, or interrupting a
system.

## Do Not Escalate

Hermes should not escalate when:

- the finding is already covered by a live canonical issue and no new material
  evidence is provided;
- the report contains only speculation without reviewable evidence;
- the requested action is purely execution work for another role;
- the provided content is private or secret and cannot be safely summarized;
- the finding is below the local review threshold and silence rules apply.

## Silence Rules

Hermes should stay silent or recommend no new output when output would only add
noise. Silence applies when:

- a live canonical issue already covers the finding;
- no new evidence, risk, reproduction detail, or decision point is present;
- a previous Hermes packet already normalized the same claim;
- the content cannot be made public-safe without losing the reviewable claim;
- the only possible recommendation would exceed Hermes authority.

Silence does not delete evidence or suppress authorized review. It means Hermes
does not create a new packet, card, queue item, or duplicate review object.

## Review Packet Path

When escalation is warranted, Hermes should prepare an output packet using
`docs/HERMES_OUTPUT_PACKET.md`. The packet must include the escalation decision
and the authority boundary. It must remain advisory until accepted by an
authorized operator or reviewed process.

# Assistant Control Route

This file names the allowed route labels for assistant-initiated Skeleton work.
Use the exact labels below when reporting capability or fallback state:

- `GitHub route`: direct GitHub issue, pull request, review, or file-write route after scope and approval checks.
- `Runner Inbox route`: bounded public-safe Runner Inbox packet for `projects/skeleton/REVIEW_QUEUE.yaml` only.
- `terminal-pasted route`: operator-pasted bounded packet, command, or patch text for local execution after review.
- `blocked`: no safe write route is available without more information, approval, or capability.

## BZ Intake

BZ transcript, article, sourcepack, or external analysis defaults to
knowledge intake. The assistant must classify, critique, deduplicate, risk
check, prepare a PatchPlan, receive explicit approval, and only then create a
Runner Inbox packet if a write is needed and the selected write route is
`Runner Inbox route`.

Public-safe durable BZ entries for the Runner Inbox are `REVIEW`, `BACKLOG`,
or `REJECTED` only. There is no automatic `CANON` promotion. Canon promotion
requires a separate approved canon write.

Private data, secrets, deploy changes, and runtime changes must never go
through the public Runner Inbox.

## Fallbacks

If GitHub write from ChatGPT is blocked, use `Runner Inbox route` when the
write is a bounded public-safe review-queue append. Use `terminal-pasted route`
when the operator needs to paste a bounded packet, command, or patch into the
local environment. Otherwise report `blocked`.

The plus sign (`+`) continues the active route only. It does not authorize a
new route, a new durable write, canon promotion, private publication, deploy,
runtime change, or secret handling.

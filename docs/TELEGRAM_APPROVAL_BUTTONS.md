# Telegram Approval Buttons

`core/telegram_approval_buttons.py` is the stage 1 dry-run operator-console
layer for completed pull request review cards. It builds a deterministic,
bounded Telegram card payload from public pull request review metadata:
repository, pull request number, reviewed head SHA, changed file paths, test
summary, risk summary, and pull request URL.

The intended operator UX is:

1. Runner finishes a pull request and posts a Telegram card.
2. Oleksii chooses `approve`, `reject`, `details`, or `open_pr`.
3. Stage 1 validates the callback payload only.

The card includes button entries for all four choices. `approve` and `reject`
callbacks are bound to the reviewed repository, pull request number, and head
SHA. `details` and `open_pr` are dry-run console events only; `open_pr` also
carries the pull request URL for the future UI button route.

Stage 1 callback validation blocks malformed payloads and callbacks whose head
SHA does not match the current SHA supplied by the caller. Only a validated
`approve` callback is passed to `core/action_gate.py`, where it becomes a
dry-run `merge_pull_request` validation request. Rejecting a pull request,
requesting details, or opening its URL does not ask `action_gate` for a live
repository action.

This stage does not send Telegram messages, read or write repositories, merge,
deploy, access secrets, execute subprocesses, or perform any network call.
Summaries, changed-file lists, and URLs are bounded before they enter the card
payload. The payload records public PR review metadata only and does not carry
source contents, credentials, or private runner state.

A future live stage may perform a repository action only after re-checking pull
request state, head SHA, changed files, and tests. That future stage must keep
the stale-head and action-gate checks in front of repository side effects.

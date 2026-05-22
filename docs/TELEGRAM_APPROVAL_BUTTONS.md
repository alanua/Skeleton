# Telegram Approval Buttons

`core/telegram_approval_buttons.py` is the stage 1 dry-run operator-console
layer for completed pull request review cards. It builds a deterministic,
bounded Telegram card payload from public pull request review metadata:
repository, pull request number, reviewed head SHA, changed file paths, test
summary, risk summary, and pull request URL. Runner `DONE` notifications can
now emit that card as a Telegram message with inline buttons when a draft pull
request URL is present.

The intended operator UX is:

1. Runner finishes a pull request and posts a Telegram card.
2. Oleksii chooses `approve`, `reject`, `details`, or `open_pr`.
3. A future live stage handles Telegram callbacks.

Operator-facing Telegram text is Ukrainian and uses simple human wording. The
main card is a short decision summary; technical review data belongs in
`details` or the opened pull request, not in that card.

The card includes button entries for all four choices. `approve` and `reject`
callbacks are bound to the reviewed repository, pull request number, and head
SHA. Runner only sends `approve` and `reject` when its `DONE` report has
reliable PR binding data: a reviewed SHA and the changed-file list. For the
current Runner report format, the `Commit:` SHA is the commit pushed
immediately before the draft PR is created, so the notification treats it as
the reviewed PR head SHA. If that SHA or changed-file list is unavailable, the
inline keyboard contains `details` and `open_pr` only.

`details` and `open_pr` are button payloads only in this stage. The live
Telegram sender uses Telegram `sendMessage` `reply_markup` with
`inline_keyboard`; `open_pr` is emitted as a Telegram URL button. Callback
data is deterministic, bounded, and derived only from public-safe card
metadata. When `SKELETON_TG_CALLBACK_HMAC_SECRET` is configured for the sender,
the bounded callback digest is a truncated HMAC over the callback fields; the
live callback poller accepts only that signed form.

If Runner cannot build the `DONE` card, or Telegram rejects the card send with
`reply_markup`, Runner retries the public-safe plain `DONE` notification. The
fallback text keeps the existing repository, issue, status, and PR link fields;
it does not include the card failure or any bot token.

Stage 1 callback validation blocks malformed payloads and callbacks whose head
SHA does not match the current SHA supplied by the caller. Only a validated
`approve` callback is passed to `core/action_gate.py`, where it becomes a
dry-run `merge_pull_request` validation request. Rejecting a pull request,
requesting details, or opening its URL does not ask `action_gate` for a live
repository action.

Stage 1 Runner integration sends button payloads only. It does not handle
callbacks and does not perform a live merge or reject action. Outside the
existing Runner issue comment flow it does not write GitHub state. Summaries,
changed-file lists, and URLs are bounded before they enter the card payload.
The payload records public PR review metadata only and does not carry source
contents, credentials, or private runner state.

A future live stage may perform a repository action only after re-checking pull
request state, head SHA, changed files, and tests. That future stage must keep
the stale-head and action-gate checks in front of repository side effects.

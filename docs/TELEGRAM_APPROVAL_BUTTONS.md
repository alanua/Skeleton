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
3. The live callback poller records the signed callback.
4. Only `approve` may request a bounded Runner squash merge.

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

`details` and `open_pr` are button payloads only in the card. The live callback
poller uses a signed `details` button to show a bounded PR summary in Telegram
after it records the audit comment; `details` does not request a merge. The live
Telegram sender uses Telegram `sendMessage` `reply_markup` with
`inline_keyboard`; `open_pr` is emitted as a Telegram URL button. Callback
data is deterministic, bounded, and derived only from public-safe card
metadata. When `SKELETON_TG_CALLBACK_HMAC_SECRET` is configured for the sender,
the bounded callback digest is a truncated HMAC over the callback fields; the
live callback poller accepts only that signed form and suppresses repeat signed
callback data locally.

If Runner cannot build the `DONE` card, or Telegram rejects the card send with
`reply_markup`, Runner retries the public-safe plain `DONE` notification. The
fallback text keeps the existing repository, issue, status, and PR link fields;
it does not include the card failure or any bot token.

Stage 1 callback validation blocks malformed payloads and callbacks whose head
SHA does not match the current SHA supplied by the caller. Only a validated
`approve` callback is passed to `core/action_gate.py`, where it becomes a
dry-run `merge_pull_request` validation request. The live callback poller also
accepts only signed bounded callback data from the Telegram sender. It
suppresses local callback-data replay and creates a fixed Runner merge request
issue only after the signed `approve` audit comment has been posted. Rejecting a
pull request, requesting details, or opening its URL does not request a live
repository action.

Runner accepts that merge request only through allowlisted issue mode
`TELEGRAM_APPROVED_PR_MERGE`. Before its only merge action it rechecks the
Telegram approve HMAC digest, verifies PR state and approved head SHA, and
requires the matching signed Telegram approve audit record. The callback poller
writes that bounded approval record after it verifies the signed callback
against GitHub PR state:

```text
Operator event record (Telegram callback stage 1)
Pull request: #123
Action: telegram_approve
Callback digest: <signed digest>
Result: recorded
Verified approval record: signed_telegram_callback
Verified head SHA: <40-character head SHA>
```

The PR must be open, not draft, mergeable, and still at the button head. Runner
then invokes only a squash merge for that approved PR with the approved head SHA
as the merge head match. The merge mode does not execute Codex, arbitrary
issue-body commands, deploys, server work, systemd work, or secret handling.
Runner continues to leave `reject`, `details`, and `open_pr` without a live PR
action.

The routine operator path is one Telegram action: press `approve` on the PR
card. The signed callback poller writes the GitHub-readable approval record and
creates the bounded Runner merge request; the operator does not add a separate
GitHub review marker or retry the merge issue.

Smoke test note: a docs-only PR can verify the Telegram card approval flow.

Summaries, changed-file lists, and URLs are bounded before they enter the card
payload. The payload records public PR review metadata only and does not carry
source contents, credentials, or private runner state. Future stages must keep
the stale-head, signed-callback, and review-marker checks in front of any
broader repository side effects.

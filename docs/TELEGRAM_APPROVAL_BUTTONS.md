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
2. ChatGPT reviews the pull request and records `ChatGPT review decision:
   CONTENT APPROVED` for the current PR head in the PR conversation.
3. Oleksii chooses `approve`, `reject`, `details`, or `open_pr`.
4. The live callback poller verifies the review marker before queueing an
   approved merge action for Runner.

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
metadata.

If Runner cannot build the `DONE` card, or Telegram rejects the card send with
`reply_markup`, Runner retries the public-safe plain `DONE` notification. The
fallback text keeps the existing repository, issue, status, and PR link fields;
it does not include the card failure or any bot token.

The live callback poller blocks malformed payloads and callbacks whose head
marker does not match current GitHub pull request state. Before a Telegram
`approve` can create a `GITHUB_ACTION_REQUEST`, the PR conversation must also
contain the explicit public-safe review decision marker for the same PR and
current head SHA or head marker. Rejecting a pull request, requesting details,
or opening its URL stays audit-only and does not ask Runner for a live
repository action.

Runner consumes the queued approved action separately. It re-checks PR state,
the current head, and the ChatGPT review marker before it performs its
allowlisted `merge_pr_squash` GitHub action. Telegram callbacks do not merge
directly. Summaries, changed-file lists, URLs, and queued action fields remain
bounded public-safe metadata; they do not carry source contents, credentials,
or private runner state.

Simple merge workflow:

1. ChatGPT reviews the PR and records `CONTENT APPROVED`.
2. The operator presses Telegram approve.
3. Runner verifies review and PR state.
4. Runner performs only the allowlisted GitHub action.
5. ChatGPT verifies the result.

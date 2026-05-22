# Telegram Approval Buttons

`core/telegram_approval_buttons.py` builds the operator-console layer for
completed pull request review cards. It builds a deterministic,
bounded Telegram card payload from public pull request review metadata:
repository, pull request number, reviewed head SHA, changed file paths, test
summary, risk summary, and pull request URL. Runner `DONE` notifications can
now emit that card as a Telegram message with inline buttons when a draft pull
request URL is present.

The intended operator UX is:

1. ChatGPT reviews a Runner pull request and the bounded action.
2. Telegram records the operator `approve`, `reject`, or `details` button.
3. Runner executes only an approved allowlisted GitHub action after verification.
4. ChatGPT verifies the result.

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

`details` and `open_pr` are button payloads only. The live
Telegram sender uses Telegram `sendMessage` `reply_markup` with
`inline_keyboard`; `open_pr` is emitted as a Telegram URL button. Callback
data is deterministic, bounded, and derived only from public-safe card
metadata.

If Runner cannot build the `DONE` card, or Telegram rejects the card send with
`reply_markup`, Runner retries the public-safe plain `DONE` notification. The
fallback text keeps the existing repository, issue, status, and PR link fields;
it does not include the card failure or any bot token.

Stage 2 callback validation blocks malformed payloads and stale `approve` or
`reject` bindings. A validated reliable `approve` callback does not merge from
Telegram. The callback poller creates a fixed public-safe Runner
`GITHUB_ACTION_REQUEST` issue for `merge_pr_squash`, including repository, PR
number, action id, expected head SHA, operator event digest, and a bounded
changed-file summary when available. Runner then re-checks the repository, PR
state, draft state, mergeability, and head SHA before the GitHub merge.

The Runner action lane allowlists only `merge_pr_squash`,
`close_pr_as_superseded`, and `close_issue_completed`. The current Telegram
`reject` callback stays audit-only unless a future explicit stale/superseded
callback binding is added. `details` stays audit-only. `close_issue_completed`
is for an already `runner:done` task issue linked to a merged PR.

This stage does not add deploys, server commands, systemd actions, secrets
handling, or arbitrary commands from action-request text. It does not act
automatically without Telegram/operator approval. Summaries, changed-file lists,
and URLs are bounded before they enter the card payload; public GitHub records
do not carry source contents, credentials, or private runner state.

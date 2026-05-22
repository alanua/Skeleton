# Telegram Callback Poller

`scripts/telegram_callback_poller.py` is the stage 1 live callback poller for
the inline Telegram review buttons sent by PR #120. Its default CLI runtime and
explicit `--once` mode read one bounded Telegram `getUpdates` batch and exit.
It accepts bounded
`callback_data` values shaped as
`tpr1:<action>:p<pr_number>:<sha8>:<digest12>` for `approve`, `reject`, and
`details`.

When `GITHUB_TOKEN` exists, the handler fetches pull request state from
`alanua/Skeleton` and verifies the PR number and head marker before recording
an `approve` or `reject` callback. `reject` and `details` stay audit-only:
they post one public-safe GitHub PR conversation comment that starts with
`Operator event record`. The comment is a GitHub-readable OperatorEvent audit
comment for the button click. It does not contain tokens or private Telegram
state.

`approve` is queue-only. Before it can create a Runner
`GITHUB_ACTION_REQUEST`, the poller also checks the target PR conversation for
a public-safe ChatGPT review marker bound to that PR and the current head:

```text
ChatGPT review decision: CONTENT APPROVED
Pull request: #120
Head SHA: <current 40-character PR head SHA>
```

`Head marker: <sha8>` is accepted when it matches the callback marker and the
current PR head. Without a matching ChatGPT marker, approve returns a blocked
result and does not create an action-request issue. With a matching marker,
approve creates a bounded `runner:ready` GitHub issue for Runner action
`merge_pr_squash` and posts the audit comment. The callback poller never merges
the PR itself.

When `GITHUB_TOKEN` is missing, the handler returns a skipped no-post result.
When `SKELETON_TG_BOT` exists and the callback query has a bounded callback ID,
the handler calls Telegram `answerCallbackQuery`. Telegram callback answers are
best-effort: Telegram can reject an old callback after the GitHub audit comment
has been posted, so an answer failure is recorded as an error result without
crashing the poll pass. The GitHub audit record remains the durable operator
event. `dry_run=True` makes a hard no-HTTP path for validation and tests.

The one-shot poll pass reads and writes its Telegram offset state as JSON. Set
`SKELETON_TG_CALLBACK_STATE` to choose the local state file. Without that
variable it uses
`/home/agent/agent-dev/state/telegram_callback_poller.json`. The poller advances
the stored offset to the next update after the highest bounded batch update it
processed, including non-callback updates returned by Telegram. The same local
state file keeps a bounded callback ID history so a callback seen again does not
post a duplicate GitHub audit comment; the duplicate still receives a
best-effort Telegram callback answer.

`scripts/skeleton-telegram-callback-poll.service` runs one poll pass with the
optional `/etc/skeleton-runner.env` environment file. The matching timer starts
the one-shot service frequently with timer jitter. Unit files do not carry
credentials.

Simple merge workflow:

1. ChatGPT reviews the PR and records `CONTENT APPROVED` on the current head.
2. The operator presses Telegram approve.
3. Runner verifies the recorded review marker and PR state again.
4. Runner performs only the allowlisted GitHub action.
5. ChatGPT verifies the result.

The poller does not merge, reject, close a PR, mutate PR labels, deploy, touch
runtime/server/systemd state, access secrets beyond its configured GitHub and
Telegram tokens, or execute arbitrary commands.

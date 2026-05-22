# Telegram Callback Poller

`scripts/telegram_callback_poller.py` is the stage 1 live callback poller for
the inline Telegram review buttons sent by PR #120. Its default CLI runtime and
explicit `--once` mode read one bounded Telegram `getUpdates` batch and exit.
It accepts bounded
`callback_data` values shaped as
`tpr1:<action>:p<pr_number>:<sha8>:<digest12>` for `approve`, `reject`, and
`details`.

This stage records button clicks only. When `GITHUB_TOKEN` exists, the handler
fetches pull request state from `alanua/Skeleton`, verifies the PR number and
head marker before recording an `approve` or `reject` callback, and posts one
public-safe GitHub PR conversation comment that starts with `Operator event
record`. The comment is a GitHub-readable OperatorEvent audit comment for the
button click. It does not contain tokens or private Telegram state.

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

Stage 1 does not merge, reject, close a PR, mutate labels, deploy, or perform
any repository action other than posting the audit comment. Live merge or
reject behavior is future work after an HMAC or one-time-token binding is
designed and reviewed.

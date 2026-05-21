# Telegram Callback Poller

`scripts/telegram_callback_poller.py` is the stage 1 live callback handler for
the inline Telegram review buttons sent by PR #120. It accepts bounded
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
the handler calls Telegram `answerCallbackQuery`. `dry_run=True` makes a hard
no-HTTP path for validation and tests.

Stage 1 does not merge, close a PR, mutate labels, deploy, execute
subprocesses, install a daemon or service, or perform any repository action
other than posting the audit comment. Live merge or reject behavior is future
work after an HMAC or one-time-token binding is designed and reviewed.

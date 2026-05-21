# Telegram Callback Poller

`scripts/telegram_callback_poller.py` is the stage 1 bounded callback handler
for the Telegram inline buttons introduced in PR #120. It accepts the compact
button callback data emitted by the Runner:

```text
tpr1:<action>:p<pr_number>:<sha8>:<digest12>
```

Only `approve`, `reject`, and `details` callbacks are accepted. The handler
allows only `alanua/Skeleton`, validates the public compact binding before any
write, and verifies the current pull request number and head marker before
recording an `approve` or `reject` callback.

This stage only records button clicks as GitHub-readable `OperatorEvent` audit
comments. Posted comment text starts with `Operator event record`, states that
the result is audit-comment-only, and does not echo GitHub or Telegram tokens.
When `GITHUB_TOKEN` is absent the GitHub post is skipped. When
`SKELETON_TG_BOT` is present the Telegram callback query is answered. `dry_run`
performs no HTTP calls.

The stage 1 poller does not merge, close pull requests, mutate labels, deploy,
install a daemon or service, or execute subprocesses. Live merge and reject
behavior is future work after an HMAC or one-time-token callback binding is in
place.

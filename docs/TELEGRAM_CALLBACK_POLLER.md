# Telegram Callback Poller

`scripts/telegram_callback_poller.py` is the live callback poller for the
inline Telegram review buttons sent for Runner pull requests. Its default CLI
runtime and explicit `--once` mode read one bounded Telegram `getUpdates` batch
and exit. It accepts bounded `callback_data` values shaped as
`tpr1:<action>:p<pr_number>:<sha8>:<digest12>` for `approve`, `reject`, and
`details`.

The simple stage 2 workflow is:

1. ChatGPT reviews the PR and says whether the bounded action is safe.
2. The Telegram button records operator approval.
3. Runner executes only a bounded allowlisted GitHub action after verification.
4. ChatGPT verifies the result.

When `GITHUB_TOKEN` exists, the handler fetches pull request state from
`alanua/Skeleton` and verifies the PR number and current head marker before it
accepts an `approve` or `reject` callback. A reliable `approve` callback creates
a public-safe GitHub issue labeled `runner:ready` with fixed action-request
metadata for `merge_pr_squash`: repository, pull request number, action id,
expected head SHA, operator event digest, and a bounded changed-file summary
when GitHub returns one. The callback poller does not merge the PR.

`reject` and `details` callbacks post only the public-safe PR conversation audit
comment that starts with `Operator event record`. The current Telegram callback
format has no explicit stale/superseded close instruction, so `reject` does not
queue `close_pr_as_superseded` and does not close the PR. The GitHub text does
not contain tokens or private Telegram state.

When `GITHUB_TOKEN` is missing, the handler returns a skipped no-post result.
When `SKELETON_TG_BOT` exists and the callback query has a bounded callback ID,
the handler calls Telegram `answerCallbackQuery`. Telegram callback answers are
best-effort: Telegram can reject an old callback after GitHub recording has
completed, so an answer failure is recorded as an error result without crashing
the poll pass. `dry_run=True` makes a hard no-HTTP path for validation and
tests.

The one-shot poll pass reads and writes its Telegram offset state as JSON. Set
`SKELETON_TG_CALLBACK_STATE` to choose the local state file. Without that
variable it uses
`/home/agent/agent-dev/state/telegram_callback_poller.json`. The poller advances
the stored offset to the next update after the highest bounded batch update it
processed, including non-callback updates returned by Telegram. The same local
state file keeps a bounded callback ID history so a callback seen again does not
post duplicate GitHub recording; the duplicate still receives a best-effort
Telegram callback answer.

`scripts/skeleton-telegram-callback-poll.service` runs one poll pass with the
optional `/etc/skeleton-runner.env` environment file. The matching timer starts
the one-shot service frequently with timer jitter. Unit files do not carry
credentials.

The callback poller may create only public-safe GitHub comments or Runner
action-request issues. It does not merge, close, deploy, run server commands,
touch secrets, run arbitrary commands, or act without a Telegram operator
button. Runner is the executor for the bounded GitHub action after it re-checks
the action request.

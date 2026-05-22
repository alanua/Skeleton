# Telegram Callback Poller

`scripts/telegram_callback_poller.py` is the stage 1 live callback poller for
inline Telegram PR review buttons. Its default CLI runtime and explicit
`--once` mode read one bounded Telegram `getUpdates` batch and exit.
It accepts bounded
`callback_data` values shaped as
`tpr1:<action>:p<pr_number>:<sha8>:<digest12>` for `approve`, `reject`, and
`details`. The digest is a truncated HMAC-SHA256 signature over the bounded
callback fields. Unsigned legacy digests and forged digests do not authorize a
live callback.

This stage records a signed button click before any Runner handoff. When
`GITHUB_TOKEN` exists, the handler first requires
`SKELETON_TG_CALLBACK_HMAC_SECRET` and verifies the HMAC digest. Only then does
it fetch pull request state from `alanua/Skeleton`, verify the PR number and
head marker before recording an `approve` or `reject` callback, and post one
public-safe GitHub PR conversation comment that starts with `Operator event
record`. The comment is a GitHub-readable OperatorEvent audit comment for the
button click. For `approve`, the comment also carries the bounded verified
approval record and full GitHub head SHA that Runner requires before merge. It
does not contain tokens or private Telegram state.

After that audit comment is posted, only a signed `approve` callback creates a
bounded GitHub issue labeled `runner:ready`. That issue uses the fixed
`TELEGRAM_APPROVED_PR_MERGE` mode and carries only the repository, approved PR
number, full approved head SHA fetched from GitHub, the fixed `squash` action,
and the callback digest. Runner rechecks that digest with the callback HMAC
secret before it verifies the callback-written approval record, performs the PR
checks, and makes the merge decision. The routine operator approval ends at the
Telegram `approve` button; it does not require a separate GitHub comment or
manual Runner merge retry. `reject` and `details` remain audit-comment-only
callbacks. A signed `details` callback also answers the Telegram button with a
bounded summary from the already-read GitHub PR state: title, lifecycle state,
changed-file count, diff counts, and the button head marker.

When `SKELETON_TG_CALLBACK_HMAC_SECRET` is absent, live `approve`, `reject`,
and `details` callbacks are blocked before a GitHub read or comment write. When
`GITHUB_TOKEN` is missing after signature verification, the handler returns a
skipped no-post result. When `SKELETON_TG_BOT` exists and the callback query has
a bounded callback ID, the handler calls Telegram `answerCallbackQuery`.
Telegram callback answers are best-effort: Telegram can reject an old callback
after the GitHub audit comment has been posted, so an answer failure is recorded
as an error result without crashing the poll pass. The GitHub audit record
remains the durable operator event. `dry_run=True` makes a hard no-HTTP path for
validation and tests.

The one-shot poll pass reads and writes its Telegram offset state as JSON. Set
`SKELETON_TG_CALLBACK_STATE` to choose the local state file. Without that
variable it uses
`/home/agent/agent-dev/state/telegram_callback_poller.json`. The poller advances
the stored offset to the next update after the highest bounded batch update it
processed, including non-callback updates returned by Telegram. The same local
state file keeps bounded callback ID and callback data histories. A repeat
signed callback value does not post another GitHub audit comment or create
another Runner merge request even when Telegram assigns a new callback ID; the
duplicate still receives a best-effort Telegram callback answer.

`scripts/skeleton-telegram-callback-poll.service` runs one poll pass with the
optional `/etc/skeleton-runner.env` environment file. The matching timer starts
the one-shot service frequently with timer jitter. Unit files do not carry
credentials.

The callback poller itself does not merge, reject, close a PR, deploy, execute
commands, or read secrets beyond its configured GitHub, Telegram, and callback
HMAC credentials. Its only live GitHub writes are the signed-callback PR audit
comment and the bounded `runner:ready` issue for an approved merge request.

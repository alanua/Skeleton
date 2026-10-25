# Family Document Intake

The family-document intake worker watches a configured private inbox, waits for
a stable local file observation, converts it to bounded OCR text, archives a
public-safe canonical record, then enqueues exactly two Telegram receipts:
`intake` and `terminal`.

Telegram is handled through `core.telegram_notifications`, which uses the
existing Skeleton bot environment variables. Missing credentials or transport
errors leave outbox rows in `RETRY`; archive and MemoryGateway commits are not
rolled back by notification delivery.

The outbox stores durable `DONE` rows after successful sends. Restarting the
worker replays only `PENDING` or `RETRY` rows, so successful receipts are not
sent twice.

Each existing worker scan reconciles the configured inbox, including files whose
watcher event was lost. Two identical size, mtime and inode observations are
required in the current process; restart discards observations. Regular-file
reads check inode, size, mtime and ctime before/after readback and hashing.
OCR and archive receive verified private copies; original inbox bytes are never
modified. Mutation is rejected and can be observed again on later scans.

The existing SQLite work table atomically claims each source/version under
`BEGIN IMMEDIATE`. Owner tokens fence archive permission and completion. Claims
lease for 300 seconds. Expiration before archival enters RETRY with exponential
30-second backoff capped at one hour, reaching REVIEW after five failures.
DONE stays terminal. Immediately before invoking the existing archive sink, the
owner durably marks archive intent. Failure or lease expiry after that boundary
retains REVIEW for operator reconciliation, since a commit may have happened;
a replay never invokes archival again. The existing sink still owns original
archive/readback before MemoryGateway commit ordering. No new worker, watcher,
database, queue, scheduler, archive service or sender is introduced.
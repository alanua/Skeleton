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

Production installation is disabled by default: the installer reloads systemd
and prints `INSTALLED_NOT_STARTED`, but does not enable or start the unit. The
live worker runs as `agent:agent`; installation may require root only to copy
runtime files and create private runtime directories. The inbox, immutable
archive, outbox, scheduler state, and canonical private-memory root are created
with restrictive `0700` ownership for `agent`.

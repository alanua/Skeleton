# Family Document Intake

The family-document intake worker watches a configured private inbox, waits for
a stable local file observation, converts it to bounded OCR text, archives the
immutable original with local readback verification, commits the public-safe
canonical record through `MemoryGateway`, performs exact readback against the
same canonical ref and value hash, then enqueues one or more package-report
Telegram receipts.

Telegram is handled through `core.telegram_notifications`, which uses the
existing Skeleton bot environment variables. Missing credentials or transport
errors leave outbox rows in `RETRY`; original archive and MemoryGateway commits
are not rolled back by notification delivery.

The outbox stores durable `DONE` rows after successful sends. Restarting the
worker replays only `PENDING` or `RETRY` rows, so successful receipts are not
sent twice.

`scripts/install_family_document_worker.sh` installs the service unit, runtime
directories, and a private env-file template, but intentionally does not enable
or start the service. Live Home Edge activation remains a protected action after
the canonical private memory stack, scheduler database, Telegram environment,
and local OCR dependencies are present on the node.

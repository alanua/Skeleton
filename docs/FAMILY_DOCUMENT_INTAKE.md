# Family Document Intake

The MFP family-document path uses one reusable private notification hook.

Stable-file acceptance calls `accept_stable_scan` after the stable gate has
accepted a scan. Pending or unstable files return `NOT_ENQUEUED`, so no receipt
is produced before the gate.

Canonical processing calls `complete_canonical_processing` only after the
archive and canonical MemoryGateway commit have succeeded. It then enqueues one
private Telegram notification record for `DONE`, `REVIEW`, `RETRY`, `FAILED`,
or `QUARANTINED`.

Notification idempotency is deterministic:

- intake: canonical document identity plus the intake receipt phase
- terminal: canonical document identity, canonical task identity, and terminal receipt type

The stored notification record exposes only hashes and public-safe status text.
It must not include private filenames, OCR text, person identifiers, Telegram
tokens, or chat ids.

Telegram activation uses `SecretReference` values at runtime. Repository files
store only secret reference names, never secret values. If Telegram delivery is
unavailable, document processing remains committed and the notification record
remains retryable through the private canonical projection/outbox path.

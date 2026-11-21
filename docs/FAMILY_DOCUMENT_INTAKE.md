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

### Explicit duplex scan sessions

`core.family_document_scan_session` provides immutable typed `ScanEvent`,
`PageSlot`, and `ScanSession` values and the pure `apply_event` reducer.
An explicit open event establishes the session and profile identity. Passes
require evidence of the physical sheet/side mapping and preserve acquisition
order. A slot represents a scanned page, including a scanned blank. An absent
back is declared separately by sheet number on explicit completion; it does
not create a physical slot. Unequal pass counts are valid when every contiguous
front sheet has exactly one scanned or explicitly absent back. Page identifiers
and sheet/side positions must be unique across passes.

Call `FamilyDocumentReceiptOutbox.record_scan_event` to persist events and their
OPEN/REVIEW/FINALIZED transitions in `family_document_scan_events` in the existing
outbox SQLite database. `scan_session` reconstructs the immutable session after
restart. Writers serialize with `BEGIN IMMEDIATE`; event identity conflicts
raise, exact replay is a no-op, and identical pass replay is a no-op. Explicit
session/profile identity is required regardless of elapsed time. There is no
clock-based association or OCR comparison. Evidence references must identify
actual externally established mapping/completion evidence; a nonempty string
is not independent verification of that evidence.

Missing evidence, conflicting mappings, incomplete completion, and explicit
stale events route to sticky REVIEW. Timeout cannot finalize. Only explicit
completion with full mapping can finalize an OPEN session, and finalization
rejects all new mutations while accepting exact event replays. REVIEW resolution
is left to a later reviewed workflow. This API does not add a worker or perform
source reconciliation, PDF assembly/intake wiring, bank taxonomy, truthful
reports/notifications, or bounded backlog reevaluation; those remain subsequent
pipeline stages. Public tests contain synthetic identifiers only.
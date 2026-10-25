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

The local taxonomy recognizes MBS / Mittelbrandenburgische Sparkasse only with
an explicit Girovertrag, Kontovertrag, or Girokonto marker. This context selects
banking even when the text incidentally mentions Aufenthalt. Migration keywords
use specific residence terms (Aufenthaltstitel / Aufenthaltserlaubnis), alongside
the existing authority, permit, and benefit markers; Aufenthalt alone is insufficient.
Explicit migration or competing issuer evidence in a bank document requires REVIEW.

The optional classification `bank_contract` object contains the canonical bank,
`form_type`, `product_type`, `account_owner_alias`, `fees`, and `conditions`.
Owner evidence must be a `Kontoinhaber:` or `Kontoinhaberin:` value matching one
configured subject alias in full. Merely mentioning an alias does not establish
account ownership. Monthly fees require a complete labelled
`Kontoführungsgebühr: 4,50 EUR monatlich` (or `pro Monat`) statement. Zero must be
explicit; missing, unsupported, or conflicting fees yield an empty list.
Conditions are copied only from a `Bedingungen:` or `Konditionen:` line; absent
conditions yield an empty list, and conflicting lines are suppressed.

Weak evidence yields REVIEW with `BANK_OWNER_AMBIGUOUS`, `BANK_FEES_AMBIGUOUS`,
`BANK_FORM_AMBIGUOUS`, or `BANK_CONDITIONS_AMBIGUOUS`. Context conflicts use
`BANK_MIGRATION_CONTEXT_CONFLICT` or `BANK_ISSUER_CONFLICT`. Existing generic
reason codes still apply. Unknown owners and forms are null; no prices, owners,
or contractual conditions are supplied from bank knowledge. The schema keeps
all new fields optional for compatibility with existing records.

The intake builder retains the structured bank_contract object in each document record, subject to the record schema.

## Explicit duplex producer

`core.family_document_scan_session.ScanSessionAssembler` is the canonical
producer. `scripts/brother-duplex-scan` and the compatibility conveyor entry point
submit events to it; neither controls a scanner. Use the existing outbox SQLite
path with `--state-db`, the existing intake inbox with `--output-directory`, and
an explicit JSON event file with `--event`. Production assembly requires the
existing optional `pypdf` dependency. Missing PDF support routes REVIEW.

Every event requires opaque `session_id`, `scanner_id`, `profile_id`, and
`event_id` identifiers (ASCII letters, digits, underscores or hyphens). `start`
also requires `sheet_count`. A `pass` requires a unique `pass_id`, a `side`
(`front` or `back`), `orientation: "physical_slots"`, and `pages`. Each page
requires a one-based physical `sheet`, `kind` (`page` or `blank`), component
`path`, captured `sha256`, and zero-based component `page_index`. A blank is a
real captured page and is retained. An absent back is exactly
`{"sheet": 7, "kind": "absent"}`. It occupies a physical slot but emits no PDF
page. There is no automatic blank deletion, sheet renumbering, or inferred back
mapping. For reverse feeds, list each captured page with its actual physical
sheet, for example back component indices 0..5 mapped to sheets 6..1. Eight
fronts and those six backs plus explicitly absent backs 7 and 8 produce exactly
14 pages in physical sheet/front/back order.

Only `complete` finalizes. Every physical front/back slot must be accounted for.
A `timeout` persists recoverable REVIEW and never produces a completed output.
The same explicit session/scanner/profile may continue after any time gap;
timestamps and OCR never associate passes. Mismatches, conflicting replays,
and ambiguous orientation require operator review. Exact event replay survives
restart; finalized sessions reject new or conflicting events without changing
their receipt. Correct incomplete sessions with additional explicit passes and
a new completion event; mapping conflicts require a new reviewed session.

The producer reads and hashes component snapshots, validates page indices,
assembles in memory, verifies page count/readback, checks components again, and
atomically publishes a private `scan-<opaque digest>.pdf`. Finalized receipts
live in `family_document_scan_sessions` in the existing operational database.
They contain session/scanner/profile, completion event, pass/event, physical
sheet/side, input/output page indices, explicit blank/absent markers, component
hashes, final hash and page count. Local component paths remain only in private
event state, never in the safe receipt.

`FamilyDocumentRuntime` automatically attaches the session verifier to its
existing source when using `FamilyDocumentReceiptOutbox`. Reserved `scan-*.pdf`
outputs remain hidden until their SQLite completion commits, including a crash
between file publication and transaction commit. The existing stable-file gate
then checks the final hash, PDF readback and count before intake/OCR. Ordinary
local-file intake continues through the same runtime. Explicit producer-only
sources can pass `scan_sessions=assembler` to `LocalDirectoryDocumentSource`.
Original component bytes are never modified; the archive-before-memory sink
order is unchanged. Package receipts retain safe scan provenance.

Optional `--handoff-root` writes to the existing inference handoff's `pending`
directory only after verification and OCR. The existing consumer's strict packet
schema is unchanged: its `idempotency_key` links to the durable local scan
receipt containing safe provenance, rather than placing provenance in model
output. Accepted/processing/review/pending packet replays are recognized; the
consumer's existing queue idempotency handles crash/retry. No additional worker,
queue, archive or sender is introduced. Component events and OCR packets are
private local artifacts and must never be printed in public logs.

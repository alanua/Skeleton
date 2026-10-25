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

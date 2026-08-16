# Family Document Intake

The family-document intake worker is the single canonical MFP/family-document
pipeline. It watches a configured private inbox and waits for two identical
size/mtime observations before processing a file.

Production order:

```text
stable private source
→ bounded local OCR/text extraction
→ immutable original binary archive + SHA-256 readback
→ structured family-document record
→ MemoryGateway private mutation
→ authoritative MemoryGateway exact readback
→ durable Telegram report outbox
→ private Telegram package report
```

## Local OCR

OCR is local-only. PDF files use fixed absolute `pdfinfo` and `pdftotext`
executables first. A PDF without a usable text layer falls back to the fixed
`ocrmypdf` path and then `pdftotext`. PNG/JPEG/TIFF use the fixed local
`tesseract` path. Office formats may use the fixed local LibreOffice converter.
Subprocesses are argv-only, shell-free, time-bounded and output-bounded. There
is no cloud OCR fallback for private documents.

Actual PDF page count is read from `pdfinfo`; scans are no longer treated as
one page by default.

## Archive and canonical memory

The worker requires `SKELETON_PRIVATE_MEMORY_ROOT`. The original scanned binary
is copied into the private archive and its SHA-256 is read back before any
canonical mutation. The structured record is then committed through the
existing private `MemoryGateway` capability and immediately read back through
`memory.private_read_exact`. A mismatch fails closed.

The worker never writes canonical SQLite directly and does not create a second
document database.

## Telegram report

Telegram uses the existing `core.telegram_notifications` sender only. A
completed cycle produces one concise Ukrainian package summary plus one compact
section for each newly processed logical document. Available classification
fields are rendered for the operator: title, issuer, owner/recipient alias,
document type, topic, page count, short summary, confidence, review warning and
a private human-readable archive label. Missing fields are omitted instead of
fabricated.

Messages are deterministically split to remain within Telegram's 4096-character
limit. Technical record IDs and hashes are not the primary user-facing report.

Telegram delivery is downstream of archive and MemoryGateway commit. Missing
credentials or provider errors leave the durable report row in `RETRY`; they do
not roll back the document and do not rerun OCR/classification. Successful
report parts become durable `DONE` rows.

## Restart and replay

The outbox database also records successfully processed source SHA-256 values.
After restart, a stable file whose source hash is already committed is skipped
before OCR/classification, while any pending/retry Telegram report is drained.
This prevents duplicate successful messages and unnecessary reprocessing.

Routine worker stdout contains aggregate counts/states only. Private filenames,
OCR text, people, source paths, Telegram messages and private links stay in the
trusted runtime.

# Family document intake

The family-document service is a private, local-only intake pipeline for Brother MFP scans and approved local, Windows-mounted, or reconciled Drive roots. GitHub receives code, schemas, synthetic fixtures, aggregate counts, and stable reason codes only.

## Canonical order

```text
approved source
→ stable-file gate
→ local OCR/layout extraction
→ evidence-based classification
→ deterministic repository plan
→ atomic archive promotion and SHA-256 readback
→ current private MemoryGateway mutation envelope
→ exact authoritative MemoryGateway readback from canonical SQLite
→ typed calendar upserts
→ durable projection outbox
→ DONE / REVIEW / RETRY / QUARANTINED
```

The document modules never import SQLite and never create a second canonical database. Cognee, MemPalace, Graphify, and other semantic indexes are projections. Their failure must not roll back a verified canonical MemoryGateway commit.

## Repository layout

Accepted records use:

```text
person_alias / topic_alias / jurisdiction_country / document_year / normalized_filename
```

The only root service folders are:

```text
00 intake
98 duplicates_versions
99 review
```

Visible names use the best proven date precision:

```text
YYYY-MM-DD — document type — issuer.ext
YYYY-MM — document type — issuer.ext
YYYY — document type — issuer.ext
Без дати — document type — issuer.ext
```

Archive writes are non-overwriting and atomic. Exact binary replay reuses the existing verified object. A different binary targeting the same visible path is routed under `98 duplicates_versions` with a deterministic cluster and digest suffix. Source files are never deleted, moved, or renamed.

## Fixed taxonomy

The service has exactly nine topic aliases:

1. identity and civil status;
2. migration and residence;
3. health and insurance;
4. work, tax, and business;
5. education and qualification;
6. finance, banking, and contracts;
7. legal, courts, and official correspondence;
8. housing and utilities;
9. transport and travel.

A document is accepted only when principal subject, topic, jurisdiction, document type, and issuer meet the confidence boundary. Ambiguity goes to one private review queue and does not mutate canonical memory.

## OCR boundary

Supported source formats are PDF, TIFF, PNG, JPEG, TXT, DOC, DOCX, ODT, RTF, XLS, XLSX, and ODS.

- TXT is read as bounded UTF-8.
- PDF first uses fixed `pdftotext`; an empty text layer falls back to fixed local `ocrmypdf`, then `pdftotext` again.
- Images use fixed local Tesseract.
- Office and spreadsheet files are converted to a private temporary PDF through fixed headless LibreOffice before PDF extraction.

Executable identities are absolute private configuration values. Commands use argv-only `shell=False`, fixed arguments, bounded timeout/output, a fixed environment, and no cloud provider.

## Private record

The canonical private value contains immutable document and source identities; source provenance; source and archive hashes; raw and corrected OCR with provider chain; principal and all subjects; topic, jurisdiction, date precision, document type and issuer; identifiers, amounts, deadlines and per-field evidence/confidence; duplicate/version relations; typed events; review reasons and projection state.

Memory writes use the current `skeleton.memory_gateway.request.v1` envelope and `skeleton.private_memory_gateway.mutation.v1` payload for dataset and fact namespace `family_documents`. A mutation is successful only after `skeleton.memory.private_read_exact` returns an authoritative canonical result.

## Calendar contract

Calendar events are created only for appointment, deadline, expiration, renewal, hearing, or booked travel. The event date is extracted from the keyword-local evidence window; the generic document date is never reused. Event IDs are deterministic. Source-specific `document_id` stays in the canonical private document record and is removed from the calendar upsert payload, so binary duplicates submit an identical event payload. The reverse document relation is retained through the shared event ID in each canonical document record.

## Runtime durability

The worker uses one non-stale `flock` lock, a durable atomic JSON journal, persisted size/mtime settling observations, leases and heartbeat support, expired-lease recovery, exponential retry, per-file isolation, review/quarantine states and aggregate health. The projection outbox has its own durable retry/quarantine state machine.

## Reconciliation and approval

Dry-run reconciliation changes no originals, archive, MemoryGateway, calendar or projection. The private packet contains planned archive paths, complete records, duplicate/version groups, fact IDs, idempotency keys, event IDs, review reasons and a deterministic packet hash. Public output contains aggregate counts and a stable reason code only. Live apply/service activation remains blocked until the exact private packet hash receives operator approval.

## Deployment boundary

The installer copies the reviewed user service but deliberately does not enable or start it. Runtime issue #1905 controls exact-SHA preparation, dry-run packet approval, bounded apply, live proof, service activation and rollback.

# Family document intake

This service processes private MFP and approved local document roots without exposing private document content to GitHub or public logs.

## Authority and ordering

The verified private archive is written and read back before structured mutation. Structured records are committed only through `skeleton.memory.private_mutate` to dataset `family_documents`; document modules do not import or open SQLite. Canonical success is retained when a derived projection is unavailable. A durable private outbox records the pending projection.

## Repository layout

Accepted documents use:

`person_alias / topic_alias / jurisdiction_country / document_year / normalized_filename`

The only root service folders are `00 intake`, `98 duplicates_versions`, and `99 review`. Ambiguous owner, topic, jurisdiction, document type, or issuer routes to `99 review` and is not promoted to canonical memory.

Visible names are `YYYY-MM-DD — document type — issuer.ext`, month/year variants, or `Без дати — document type — issuer.ext`.

## Privacy

Private records contain OCR, hashes, provenance, subjects, confidence evidence, duplicate/version relations and typed event candidates. Public receipts contain only status, allowlisted reason code and aggregate counts.

## Runtime

`scripts/family_document_worker.py` uses a single-instance lock, persistent size/mtime observations, atomic private JSON state, polling, and restart-safe idempotency keys. Local OCR uses fixed argv providers (`pdftotext`, `tesseract`); adapters receive bounded JSON over stdin. The installer copies the systemd user unit but never enables or starts it.

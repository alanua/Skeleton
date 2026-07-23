# Family Document Intake

This package implements the synthetic, local-only family-document intake boundary. It is inactive unless a caller imports `core.family_document_intake` or runs `scripts/family_document_intake.py` with a synthetic request.

## State Machine

Each source path is processed through explicit stages:

1. Reject unsafe roots and partial files before reading bytes.
2. Require a stable size and mtime window.
3. Hash the binary and form a canonical cluster from `sha256:<digest>`.
4. Run local OCR only. Non-text scanned documents require an injected local recognizer.
5. Route with the fixed taxonomy. Owner ambiguity routes to review.
6. Copy to `person/topic/country/year/file`, then verify the archive hash.
7. Commit only through an injected MemoryGateway-compatible sink.
8. Optionally project derived state. Projection failure leaves the canonical commit intact.
9. Upsert semantic calendar events only, without attendees, Meet data, or external notifications.

## Privacy

Public receipts contain status, reason codes, years, topics, countries, and deterministic tokens. They never include source paths, names, OCR text, raw identifiers, archive paths, Drive IDs, or calendar IDs. The operational journal is restart-safe and stores only hashed cluster ids, tokens, and stage status.

## Safety

Dry-run returns the planned public receipt and performs no archive, memory, projection, or calendar side effects. Desktop, system, secret, source, and code roots are rejected before file reads. There is no cloud OCR, LLM fallback, telemetry, production service start, source deletion, source rename, Drive migration, calendar write, or MemoryGateway mutation in this module.

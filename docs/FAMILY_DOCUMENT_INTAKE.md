# Family Document Intake

This module implements local-only intake for synthetic family/MFP document fixtures.

The runtime watches approved local inbox roots, waits for a stable-file gate, extracts through a fixed allowlist, writes an archive record with readback verification, then mutates canonical memory through `MemoryGateway.private_mutate`. It does not call cloud OCR, LLM APIs, Drive, calendars, or direct SQLite.

Public receipts are aggregate-only. Ambiguous or textless records go to review/quarantine and are not committed.

Supported source extensions are PDF, TIFF, PNG, JPEG, TXT, DOC, DOCX, ODT, RTF, XLS, XLSX, and ODS. Office formats are read through local zip/XML extraction when applicable; images are local layout-only unless an approved local OCR engine is added later.

Durability:

- `family_document_intake_journal.jsonl` records claims, archive commits, failures, quarantine, and canonical completion.
- Archive files are content-addressed by SHA-256-derived deterministic names and verified after write.
- Duplicate SHA-256 replay returns an aggregate duplicate receipt and creates no extra archive or canonical fact.
- Projection work is queued only after canonical mutation returns a receipt.

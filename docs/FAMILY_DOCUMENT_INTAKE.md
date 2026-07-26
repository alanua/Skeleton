# Family Document Intake

The family document intake worker groups consecutive artifacts from a configured
MFP source/profile into a persisted scan session before OCR or classification.
The default inactivity window is 60 seconds and can be overridden per profile.

Grouping is intentionally strict:

- pages are appended in persisted discovery sequence order;
- PDF pages and image components are copied into the logical page stream;
- only the exact local marker `SKELETON_MFP_PHYSICAL_SEPARATOR_V1` on a page
  splits a logical document;
- separator pages are excluded from logical output while component provenance is
  retained;
- blank pages, filenames, OCR content, topic changes, issuer/date/name changes
  and page-number guesses never split a document.

The runtime stores open sessions, component claims, records, aggregate receipts
and assembled PDFs under one configured runtime root. Original source files are
read only; they are never deleted, renamed or mutated. Duplicate filesystem
events replay to the same component claim and do not append pages twice.

Before any OCR-facing handoff, assembled PDFs are written atomically and read
back. The read-back page count and SHA-256 hash are persisted on the logical
document record.

Private repair handoff is runtime-only. For an already split duplex scan, create
one merged logical document and component/supersession relations with:
`repair_id`, `component_record_ids`, `supersedes_document_ids`,
`merged_document_id`, `relations`, and `delete_original_records=false`. The
handoff does not delete existing records and is not included in public receipts.

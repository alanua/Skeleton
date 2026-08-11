# Mail Operations Contour

This slice defines a provider-neutral, read-only mail operations contract under
`core.mail_operations`. It accepts private mail payloads only inside the local
process, hashes those payloads, and persists or returns only public-safe
references, provider message identifiers, service identities, retention classes,
GitHub CI status metadata, local draft references, deadline links, and aggregate
receipt counts.

Privacy boundary: `PRIVATE_EMAIL_CONTENT_LOCAL_ONLY`.

Implemented production slice:

- `MailEnvelope` is the local ingest interface. `private_payload` may contain
  mail body, subject, addresses, and attachment material, but those values are
  only used to compute `content_hash`.
- `MailOperationsStore` creates idempotent case, index, and deadline projection
  records in SQLite. Re-scanning the same provider/account/message tuple replays
  the same case and cannot duplicate case, index, or deadline rows.
- Classification is read-only and based on public signals such as service
  identity, labels, attachment kind classes, retention years, and deadline epoch.
- Invoice retention classes are bounded to `invoice_retention_7y` and
  `invoice_retention_10y`.
- GitHub CI correlation emits only service identity, repo, workflow, run id,
  commit SHA, status, and a hashed evidence reference.
- Local-first model routing is exposed through
  `build_local_inference_request`, which passes only `content_ref_hash` plus
  public signals to the local inference queue.
- Telegram reply-draft interaction is represented as a local draft reference and
  allowed actions. It does not include recipients or message text and executes no
  send.
- Scheduler deadline linkage is represented by `deadline_to_schedule_spec`, which
  creates a single notify-only schedule from the deadline reference and relies on
  the existing Scheduler store for occurrence idempotency.

Generated follow-up tasks:

1. `mail-cleanup-action-guard`: cleanup/actions side effects require explicit
   action gate coverage before any live mutation.
2. `mail-provider-live-integration`: live provider connectors must prove
   read-only dry-run behavior before polling real mail.
3. `mail-index-backfill-metrics`: add aggregate observability for local mail
   index freshness and scan coverage.

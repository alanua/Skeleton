# Finance/Gewerbe BauClock Public Projection

This boundary is derived, read-only, and public-safe. It accepts only already-aggregated Gewerbe/BauClock source records with canonical reference, canonical revision, freshness timestamps, and sha256 source hashes.

Allowed public output:

- period buckets in `YYYY-MM`
- category and status counts
- record and document-link counts
- freshness metadata
- canonical revision/ref and source hashes

Rejected private input:

- raw amounts, balances, account identifiers, tax identifiers, VAT IDs, IBAN/BIC values
- names, emails, addresses, document text, and person-level BauClock identities
- any unsupported field that is not part of the aggregate source contract

The helper in `core/gewerbe_bauclock_public_projection.py` does not create a finance authority, persistence store, outbox, MemoryGateway route, provider client, or mutation path. It only normalizes aggregate source data into `schemas/gewerbe_bauclock_public_projection.schema.json`.

# Skeleton Reading Gateway Contract

Phase 0 defines reusable, typed, versioned data contracts for local reading
progress. The contract is synthetic-only in this repository: it models public
safe receipts and validation rules, not live Android integration.

## Scope

- Schema version: `skeleton.reading_gateway.receipt.v1`
- Python package: `core.reading`
- Public aggregate: `ReadingReceipt` (`Receipt` alias)
- Core models: `WorkIdentity`, `Edition`, `ReadingSession`,
  `ProgressCheckpoint`
- Supported synthetic frontends: `MOON_READER`, `SMART_AUDIOBOOK_PLAYER`,
  `SYNTHETIC`, `UNKNOWN`, `UNAVAILABLE`

Moon+ Reader and Smart AudioBook Player remain Android frontends. This phase
does not inspect packages, read app storage, control playback, use
MediaSession, index a user library, access the network, or create a new SQLite
store.

## Identity

`WorkIdentity` and `Edition` provide stable references derived from hashed
synthetic keys. They do not carry real book titles, reading history, private
Android paths, or raw adapter identifiers. Unknown and unavailable states are
explicit and must not be represented as empty strings or invented values.

Stable references and unsalted SHA256 values derived from `source_work_key` or
`edition_key` are private local state. They are useful for idempotent local
reconciliation, but they are reusable deterministic links to a person's book
list and must not appear in public projections.

## Progress Shapes

Ebook and audiobook progress are separate:

- Ebook progress uses `EBOOK_PAGE_PERCENT` with page and/or percent fields.
- Audiobook progress uses `AUDIO_TIME_CHAPTER` with time and/or chapter fields.

The contract rejects mixed shapes. Ebook checkpoints cannot carry audio time or
chapter fields. Audiobook checkpoints cannot carry page or percent fields.
Unknown or unavailable progress carries no position fields.

## Reconciliation

Checkpoint references are deterministic. Replaying the same checkpoint is
idempotent and collapses to one public checkpoint. Reusing a checkpoint
reference with a different payload is a contract error. Receipt construction
normalizes checkpoints into monotonic timestamp order and rejects checkpoints
observed before their session start.

## Privacy Boundary

Public receipt serialization includes:

- aggregate counts for works, editions, sessions, and reconciled checkpoints
- coarse enum count buckets for reading format, frontend, session status,
  identity state, edition state, and progress kind
- explicit privacy flags showing that private identifiers, Android storage
  paths, and live device interactions are absent

Public serialization must never include real titles, reading history, user
library paths, package-private paths, credentials, device-derived live state,
work references, edition references, session references, checkpoint references,
exact timestamps, page numbers, percentages, time positions, duration values, or
chapter identifiers. Public serialization also must not include a private value
simply because it is hashed. Unsalted SHA256 values for source work keys or
edition keys remain private identifiers.

Private/local projection is explicit: `to_private_mapping()` may include stable
work, edition, session, and checkpoint references; source and edition hashes;
timestamps; and exact progress values. Its storage semantics are
`PRIVATE_LOCAL_ONLY`, and downstream consumers must keep that projection inside
the private reading-state boundary. `to_public_mapping()` is aggregate-only and
must not be used as a transport for local reconciliation identifiers.

## Future Adapter Boundary

Future Moon+ Reader and Smart AudioBook Player adapters should translate local
frontend observations into these contracts at the edge. They should remain
bounded adapters and must not add a second media database, search index,
scheduler, or library crawler to Skeleton Core. Any live Android work must have
its own approval and audit boundary outside this phase-0 synthetic contract.

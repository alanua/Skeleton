# MemPalace Idea Review

Classification: `IDEA_REVIEW`

Status: public-safe read-only evaluation only. This is not active Skeleton
architecture, not canon, not an implementation plan, and not approval to install
or run MemPalace.

Evaluation date: 2026-06-21

Sources reviewed:

- Public repository: <https://github.com/MemPalace/mempalace>
- Latest GitHub release visible during review: `v3.4.1`, published 2026-06-15
- PyPI package metadata: `mempalace 3.4.1`, released 2026-06-15
- Public docs: <https://mempalaceofficial.com/>

## Summary

MemPalace is a local-first conversation-memory/indexing tool. Its strongest fit
for Skeleton/Jeeves is as a derived, rebuildable retrieval cache over approved
memory records or synthetic fixtures. It should not become a source of truth, a
private-corpus collector, an autonomous ingestion layer, or a write-capable MCP
surface in stage 1.

Future isolated synthetic-data pilot: `CAUTION`, justified only if it is
local-only, read-only at query time, and fed by explicit Runner-gated ingestion
from synthetic or already-approved public-safe inputs.

## What It Stores And Retrieves

MemPalace stores verbatim text chunks called drawers, with metadata such as wing,
room, source file, agent, timestamps, and chunk linkage. Wings usually represent
people or projects; rooms represent topics; closets are described as summary
pointers, but current retrieval is primarily drawer text plus metadata. It also
includes a local SQLite temporal knowledge graph for entity triples with validity
windows.

Retrieval is semantic search over the vector backend, optionally filtered by wing
and room metadata. The public docs are explicit that the spatial metaphor is
operational scoping over standard vector-store metadata filtering, not a novel
retrieval primitive. Wake-up and recall layers provide bounded context: identity,
essential story, scoped recall, and full semantic search.

## Backends, Dependencies, And Downloads

Default backend: ChromaDB, local persistent storage under the palace path.

Other backend options visible in public docs and release notes:

- `sqlite_exact`: local no-service backend for exact-vector correctness checks.
- `qdrant`: opt-in REST backend, defaulting to localhost but able to point at a
  remote service with URL/API key/namespace settings.
- `pgvector`: opt-in Postgres backend, requiring the `pgvector` extra and a
  server with the vector extension.

Dependency footprint is materially larger than Skeleton's canonical SQLite
memory: Python 3.9+, ChromaDB, PyYAML, NumPy, Hugging Face Hub, tokenizers,
python-dateutil, and optional extras for pgvector, ONNX acceleration, spellcheck,
and office-document extraction. The current package metadata says onboarding can
use `embeddinggemma-300m` as a recommended multilingual model and that the model
is lazy-downloaded on first use; MiniLM remains an English-only smaller option.

Risk implication: even local-only use must treat first-run model download,
package transitive dependencies, local disk growth, and ChromaDB maintenance as
pilot criteria. No model credentials are needed for the core benchmark path, but
external reranking, cloud vector services, or remote model providers would create
a separate credential and data-egress surface.

## MCP And Auto-Save Hook Risks

The MCP surface is explicitly read/write. Public docs list tools for search,
drawer add/update/delete, directory mining, sync deletion, knowledge-graph add
and invalidation, tunnel create/delete, diary write/read, hook settings, and
reconnect. That is too much authority for Skeleton stage 1.

Auto-save hooks are designed to fire during assistant sessions and before
compaction. Public docs describe hooks that cause the AI to save key topics,
decisions, quotes, or broad context to the palace; one hook option can auto-run
`mempalace mine` against a configured conversation directory. This conflicts with
Skeleton's current memory boundary, where Runner is the controlled caller and
Codex/OpenHands do not directly own or write Skeleton memory.

Stage 1 must therefore block:

- MemPalace MCP write tools.
- Auto-save hooks.
- Session-start autonomous recall injection.
- Autonomous transcript mining.
- Private corpus ingestion.
- Any background service or hook that changes the palace without a Runner gate.

## Retention, Deletion, Backup, And Namespace Behavior

Retention is primarily local filesystem/database retention, not a policy engine.
MemPalace stores verbatim content, so deletion requirements must cover drawers,
chunked child drawers, source metadata, diaries, graph triples, tunnels/hallways,
backend sidecars, Chroma files, and backups.

The latest visible release includes a backup-retention fix for `migrate` and
`repair max-seq-id`: a new `max_backups` setting defaults to 10, after a report
that stale backups could grow to hundreds of GB. That is useful, but still not a
Skeleton deletion guarantee. A Skeleton pilot must prove that deletion of a
source record removes or invalidates every derived copy and that backup retention
cannot preserve private data beyond policy.

External backends advertise namespace isolation and local marker files to guard
against opening a palace against the wrong server. Namespace isolation helps
avoid accidental tenant mixing, but it is not enough for Skeleton stage 1 because
remote Qdrant/pgvector would send verbatim drawer text and metadata to a service.

## Relationship To Skeleton Memory And Graphify

Skeleton canonical private SQLite remains the source of truth. It stores bounded
operational state, approved records, and opaque private references under Runner
or another controlled server-side caller. GitHub remains the public-safe canon and
handoff surface.

MemPalace overlaps with Skeleton memory where it stores task/session facts,
decision-like text, source references, and temporal entity relationships. That
overlap is acceptable only if MemPalace is derived and rebuildable from approved
Skeleton records, never authoritative.

MemPalace complements Graphify only as a different derived retrieval/index layer:

- Graphify is planned as a private derived graph orientation layer over approved
  local inputs and public repo facts.
- MemPalace is more conversation/transcript and verbatim semantic-retrieval
  oriented, with optional lightweight graph features.
- Neither may outrank human approval, GitHub state, governance contracts, or
  canonical private SQLite.

This review does not alter the active Graphify work plan.

## Recommended Stage 1 Boundary

Required architecture recommendation:

- Keep canonical private SQLite as source of truth.
- Treat MemPalace, if adopted, as derived and rebuildable only.
- Use local-only backend for the first pilot.
- Prefer `sqlite_exact` or a tightly isolated local Chroma palace for synthetic
  evaluation; do not use Qdrant, pgvector, Docker, or network services.
- No hooks, MCP writes, private corpus, external vector services, model
  credentials, or autonomous ingestion in stage 1.
- Expose only a read-only retrieval interface.
- Allow ingestion only through explicit Runner-gated commands over synthetic or
  already-approved public-safe data.
- Preserve source attribution for every returned item and require caller-side
  verification against canonical SQLite or repository facts before use.

## PASS / CAUTION / REJECT Criteria

Privacy:

- `PASS`: synthetic/public-safe data only; no private paths, transcripts, Drive
  IDs, secrets, or customer data; no network egress.
- `CAUTION`: local verbatim data with strict allowlist and redaction proof.
- `REJECT`: private corpus, autonomous transcript mining, remote backend, or
  unclear model/download behavior.

Deletion:

- `PASS`: source deletion test proves drawers, chunks, diaries, graph triples,
  tunnels/hallways, metadata, and backups are removed or invalidated.
- `CAUTION`: primary drawers delete cleanly but secondary structures need manual
  audit.
- `REJECT`: deleted content remains searchable or recoverable from ordinary
  backups without an explicit retention exception.

Backup:

- `PASS`: bounded backup count/size, documented location, synthetic backup
  restore test, and deletion-aware retention policy.
- `CAUTION`: bounded backups exist but require manual cleanup verification.
- `REJECT`: unbounded backups, unknown backup paths, or private content in
  unmanaged snapshots.

Resource use:

- `PASS`: documented disk, RAM, CPU, and model-cache footprint under a synthetic
  fixture; no service startup; no background hooks.
- `CAUTION`: Chroma/embedding stack works locally but has non-trivial maintenance
  or model-cache cost.
- `REJECT`: requires Docker, GPU, network service, remote API, or unstable memory
  use for the target fixture.

Retrieval quality:

- `PASS`: retrieves expected synthetic answers with stable top-k recall, useful
  wing/room filtering, and no unsupported inference treated as fact.
- `CAUTION`: useful recall but weak ranking, duplicate chunks, or noisy metadata.
- `REJECT`: retrieval cannot cite sources, misses required synthetic facts, or
  returns stale/deleted content.

Source attribution:

- `PASS`: every result carries a stable source id that maps back to the approved
  Skeleton record or synthetic fixture.
- `CAUTION`: attribution is present but needs normalization.
- `REJECT`: answers expose verbatim content without source id, provenance, or
  canonical verification path.

## Decision

Do not install or integrate MemPalace now. Keep it in the review queue as a
possible derived local retrieval experiment. A future pilot is justified only for
synthetic data and only after a separate Runner-gated design defines the exact
read-only interface, ingestion allowlist, deletion proof, backup policy, and
resource budget.

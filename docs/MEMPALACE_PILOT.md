# MemPalace Synthetic Pilot

This pilot is local-only, synthetic-data-only, read-only, and routed behind `MemoryGateway`.
Normal `MemoryGateway` construction does not load the synthetic fixture. The bounded route is enabled only when a caller explicitly constructs and injects `MemPalaceAdapter` with `tests/fixtures/mempalace_synthetic/projection.json`.

## Scope

- Namespace: `skeleton`
- Project binding: `mempalace_synthetic`
- Commands:
  - `skeleton.memory.search_semantic`
  - `skeleton.memory.get_index_freshness`
- Projection source: `tests/fixtures/mempalace_synthetic/projection.json`
- Backend: deterministic in-process lexical index

The pilot does not start a service, open a port, call a model/API, use credentials, write canonical memory, write through MCP, install runtime dependencies, or connect to Qdrant, pgvector, or another external vector store.

## Contracts

Projection input uses `schemas/mempalace_projection.schema.json` and is intentionally bounded. Documents allow only `item_id`, `canonical_ref`, `canonical_revision`, `title`, `bounded_text`, `tags`, `source_attribution`, and `deleted`.

Search results use `schemas/mempalace_result.schema.json`. Every result includes:

- `authoritative=false`
- `namespace`
- `result_refs`
- `source_attribution`
- `score`
- `indexed_canonical_revision`
- `current_canonical_revision`
- `source_snapshot_id`
- `indexed_at`
- `stale`

All result text is bounded in `bounded_text` and must have source attribution. The gateway keeps the result non-authoritative even when retrieval quality is high. MemPalace attribution cannot satisfy exact canonical confirmation; proposal confirmation must come from the canonical SQLite exact-authority path.

## Rebuild And Deletion

`MemPalaceAdapter` builds the full derived manifest from the projection source. Deleting an item marks it deleted in a rebuilt projection instance; the deleted item is removed from retrieval and from the rebuilt manifest. `rebuild_manifest()` reproduces the same deterministic manifest from the same projection source.

## Staleness

Freshness compares indexed canonical revisions with the supplied or projected current canonical revision. Stale results remain readable for diagnostics, but they are non-authoritative and fail closed with `STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE` before proposal intake or audit logging.

## Benchmark

Run:

```bash
python3 scripts/mempalace_synthetic_benchmark.py
```

The public benchmark report emits `PASS`, `CAUTION`, or `REJECT` with stable reason codes and aggregate resource metrics only:

- `aggregate_disk_bytes`
- `aggregate_ram_bytes`
- `aggregate_build_ms`

No raw text dump, path, private fixture, credential, service endpoint, or external dependency detail is included in the report.
The script exits zero only for `PASS`; `CAUTION` and `REJECT` are non-zero.

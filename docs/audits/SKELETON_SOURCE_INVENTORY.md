# Skeleton Source Inventory and Freshness Map

Generated: `2026-07-28T00:00:00Z`

Scope: Phase 0/1 public-safe audit for issue `#1752` against current accessible evidence. This artifact contains only public repository references, public GitHub metadata references, and aggregate private-source classes. It does not contain private values, secrets, raw chats, personal records, customer data, financial data, legal data, device topology, document contents, or machine-local paths.

## Inventory Counts

| Class | Count | Source refs | Freshness | Authority |
|---|---:|---|---|---|
| Visible repository files in this checkout | 622 | `git ls-files` equivalent scan of current worktree; `git log` at `0f49c71` | Current checkout at `2026-07-28` | `CURRENT / LIVE` for code state, subject to changed-file review |
| Root control/registry YAML files | 12 | `BOOT_MANIFEST.yaml`, `COMMANDS.yaml`, `MODES.yaml`, `MEMORY_ROUTING.yaml`, `SOURCE_REGISTRY.yaml`, `CAPABILITY_REGISTRY.yaml`, `HELPER_REGISTRY.yaml`, `PROJECT_INDEX.yaml`, `STATUS_CODES.yaml`, `OPERATOR_RULES.yaml`, `PROVIDER_ROUTING.yaml`, policy YAML under `policies/` | Current checkout | `CURRENT / LIVE` when matched by tests; protected files are not edited in this phase |
| Core implementation files | 133 | `core/**` | Current checkout | `CURRENT / LIVE` where tests and merged main agree |
| Schema files | 82 | `schemas/**` | Current checkout | `CURRENT / CONTRACT_ONLY` unless backed by live implementation |
| Public docs and audits | 97 | `docs/**` | Mixed; current files plus historical docs | `PARTIAL`; stale files require explicit ledger classification |
| Project files | 31 | `projects/**` | Mixed; many `STATE.yaml` snapshots are handoff records | `PARTIAL / HISTORICAL` unless verified by current code or manifest |
| Tests | 172 | `tests/**` | Current checkout | `CURRENT / CONTRACT_ONLY` or implementation evidence when passing |
| Scripts | 44 | `scripts/**` | Current checkout | `CURRENT / LIVE` only for tested/installed routes |
| Fixtures | 13 | `fixtures/**` | Current checkout | `CURRENT / CONTRACT_ONLY` synthetic evidence |
| Adapter docs | 8 | `adapters/**` | Current checkout | `CURRENT / CONTRACT_ONLY` |
| Config and ops files | 6 | `config/**`, `ops/**` | Current checkout | `CURRENT / PLANNED` or `LIVE` only where launch receipts prove activation |
| Authenticated Skeleton issue comments inspected | 4 | `https://github.com/alanua/Skeleton/issues/1752` | Connector read on `2026-07-28` | `CURRENT / NEEDS_OPERATOR` for audit instructions and gate state |
| Recent Skeleton PR metadata inspected | 20 | `https://github.com/alanua/Skeleton/pulls` connector query | Connector read on `2026-07-28` | `CURRENT / LIVE` for merged flags and open draft status |
| Recent Skeleton commits inspected | 20 | `https://github.com/alanua/Skeleton/commits/main` connector query | Connector read on `2026-07-28` | `CURRENT / LIVE` for main commit order |
| Required alanua repositories metadata inspected | 6 | `alanua/Skeleton`, `alanua/jeeves`, `alanua/bauclock`, `alanua/DIOS`, `alanua/Lavalamp`, `alanua/LumenFlow` | Connector read on `2026-07-28` | `CURRENT / PARTIAL`; repository metadata only except local project manifests |
| Other accessible alanua repository metadata inspected | 1 connector page | `alanua/*` installation repository list | Connector read on `2026-07-28` | `PARTIAL / NEEDS_OPERATOR`; classify before importing decisions |
| Known private/local source categories | 12 | Aggregate classes in issue `#1752` comments and repo private-memory docs | Current as public-safe categories only | `NEEDS_OPERATOR / BLOCKED`; no values imported in this phase |

## Current Control and Architecture Sources

| Source ref | Role | Status | Freshness | Authority |
|---|---|---|---|---|
| `BOOT_MANIFEST.yaml` | Startup/read-order route | Current protected control source | Current checkout | `CURRENT / LIVE` |
| `SOURCE_REGISTRY.yaml` | Source override chain and trust classes | Current public canon candidate | Current checkout | `CURRENT / LIVE` |
| `MEMORY_ROUTING.yaml` | Memory routing boundary | Current control source | Current checkout | `CURRENT / LIVE` |
| `PROJECT_INDEX.yaml` | Project-to-repository registry | Current public-safe registry | Current checkout | `CURRENT / LIVE` |
| `COMMANDS.yaml` and `MODES.yaml` | Command/mode declarations | Current control source | Current checkout | `CURRENT / LIVE` |
| `OPERATOR_RULES.yaml` | Operator approval and protected rules | Current protected control source | Current checkout | `CURRENT / LIVE` |
| `core/memory_gateway.py` and `core/memory_gateway_policy.py` | MemoryGateway interface and public-safe receipt policy | Implemented code | Current checkout | `CURRENT / LIVE` |
| `core/private_memory_stack.py` and `core/memory_gateway_storage.py` | PrivateMemoryStack storage-backed gateway implementation | Implemented code | Current checkout | `CURRENT / LIVE` |
| `core/mempalace_projection.py`, `core/graphify_adapter.py`, `core/cognee_projection_adapter.py` | Derived index/projection code | Implemented code | Current checkout | `CURRENT / PARTIAL`; derived indexes are non-authoritative |
| `projects/skeleton/PROJECT_MANIFEST.yaml` | Skeleton public project manifest | Current manifest | Current checkout | `CURRENT / LIVE` |
| `projects/skeleton/STATE.yaml` | Skeleton handoff status snapshot | Last verified `2026-06-19` | Stale relative to current main `0f49c71` | `HISTORICAL / PARTIAL` |
| `projects/skeleton/MIGRATION_STATUS.yaml` | Public-safe migration status ledger | Current checkout | Current checkout | `CURRENT / PARTIAL` |
| `docs/SKELETON_ARCHITECTURE_VNEXT.md` and `docs/SKELETON_ARCHITECTURE_VNEXT.yaml` | Historical architecture plan | Mixed; partly overtaken by implementation | Current checkout, content age not authoritative | `PARTIAL / NEEDS_OPERATOR` |
| `docs/SKELETON_DEVELOPMENT_AUDIT_ERRATA_2026-06-30.md` | Retraction of incomplete prior audit conclusions | Dated `2026-06-30` | Historical but still authoritative for warning | `CURRENT / CONTRACT_ONLY` for audit caution |

## Current Project Manifests and State Snapshots

| Project | Repository ref | Manifest source | State source | Classification | Freshness note |
|---|---|---|---|---|---|
| Skeleton | `alanua/Skeleton` | `projects/skeleton/PROJECT_MANIFEST.yaml` | `projects/skeleton/STATE.yaml` | `CURRENT / PARTIAL` | State snapshot says handoff, not canon truth |
| Jeeves | `alanua/jeeves` | `projects/jeeves/PROJECT_MANIFEST.yaml` | `projects/jeeves/STATE.yaml` | `CURRENT / PLANNED` | Separate future runtime with controlled Skeleton inheritance |
| BauClock | `alanua/bauclock` | `projects/bauclock/PROJECT_MANIFEST.yaml` | `projects/bauclock/STATE.yaml` | `CURRENT / PARTIAL` | Product/project relation requires private projection review |
| DIOS | `alanua/DIOS` | `projects/dios/PROJECT_MANIFEST.yaml` | `projects/dios/STATE.yaml` | `CURRENT / PARTIAL` | External repo metadata inspected; local manifest is public-safe bridge |
| Lavalamp | `alanua/Lavalamp` | `projects/lavalamp/PROJECT_MANIFEST.yaml` | `projects/lavalamp/STATE.yaml` | `CURRENT / PARTIAL` | Separate creative/technical product |
| LumenFlow | `alanua/LumenFlow` | `projects/lumenflow/PROJECT_MANIFEST.yaml` | `projects/lumenflow/STATE.yaml` | `CURRENT / PARTIAL` | Registered small repo; priority needs refresh |
| Aufmass | `alanua/Skeleton` | `projects/aufmass/PROJECT_MANIFEST.yaml` | `projects/aufmass/STATE.yaml` | `CURRENT / PARTIAL` | Domain implementation exists with private evidence boundaries |
| Homelab | `alanua/jeeves` | `projects/homelab/PROJECT_MANIFEST.yaml` | `projects/homelab/STATE.yaml` | `CURRENT / PARTIAL` | Private topology stays outside GitHub |
| Gewerbe | `alanua/jeeves` | `projects/gewerbe/PROJECT_MANIFEST.yaml` | `projects/gewerbe/STATE.yaml` | `CURRENT / PARTIAL` | Business/private facts stay outside GitHub |
| Van | `alanua/jeeves` | `projects/van/PROJECT_MANIFEST.yaml` | `projects/van/STATE.yaml` | `CURRENT / PARTIAL` | Owned asset/configuration details private |
| Travel | `alanua/Travel` | `projects/travel/PROJECT_MANIFEST.yaml` | `projects/travel/STATE.yaml` | `CURRENT / PARTIAL` | Public manifest exists; personal travel data remains private |

## GitHub Evidence Snapshot

| Source ref | Observation | Freshness | Authority |
|---|---|---|---|
| `https://github.com/alanua/Skeleton/commit/0f49c719e1106720141d90c2e058c057d54db326` | Current accessible `main`/worktree head is "Document Scheduler launch receipt (#2058)" | `2026-07-28T07:53:42Z` | `CURRENT / LIVE` |
| `https://github.com/alanua/Skeleton/pull/2065` | Open draft Scheduler runtime repair PR exists on current main | `2026-07-28T08:34:06Z` | `CURRENT / PLANNED` |
| `https://github.com/alanua/Skeleton/pull/2063` | Open draft Scheduler user-level runtime PR exists on current main | `2026-07-28T08:18:07Z` | `CURRENT / PLANNED` |
| `https://github.com/alanua/Skeleton/pull/2062` | Open draft Video canonical-root repair PR exists on current main | `2026-07-28T08:17:02Z` | `CURRENT / PLANNED` |
| `https://github.com/alanua/Skeleton/pull/2058` | Scheduler launch receipt doc merged | `2026-07-28T07:53:43Z` | `CURRENT / LIVE` |
| `https://github.com/alanua/Skeleton/pull/2053` | Scheduler protected launch workflow merged | `2026-07-28T07:47:50Z` | `CURRENT / LIVE`; runtime success depends on issue receipt |
| `https://github.com/alanua/Skeleton/pull/2052` | Video Understanding launch route merged | `2026-07-28T07:50:27Z` | `CURRENT / LIVE`; later PR `#2062` indicates activation blocker repair still planned |
| `https://github.com/alanua/Skeleton/pull/2049` | Scheduler Core v1 merged; supersedes `#2044` | `2026-07-28T07:37:07Z` | `CURRENT / LIVE` |
| `https://github.com/alanua/Skeleton/pull/2044` | Scheduler Core earlier candidate closed unmerged | `2026-07-28T07:35:09Z` | `SUPERSEDED / NOT_PLANNED` |
| `https://github.com/alanua/Skeleton/issues/1752#issuecomment-4951285542` | Execution order moved to `#1755`; public canon writes/private imports blocked until later stages | Connector read `2026-07-28` | `CURRENT / NEEDS_OPERATOR` |
| `https://github.com/alanua/Skeleton/issues/1752#issuecomment-5101849703` | Prior runner attempt blocked because no fenced task block was found | Connector read `2026-07-28` | `HISTORICAL / BLOCKED` |

## External Repository Freshness

| Repository | Default branch | Visibility | Size | Current audit role | Freshness | Authority |
|---|---|---:|---:|---|---|---|
| `alanua/Skeleton` | `main` | public | 125989 | Active platform/control source | Metadata and commits read `2026-07-28` | `CURRENT / LIVE` |
| `alanua/jeeves` | `main` | public | 881 | Future Jeeves runtime and historical Skeleton evidence | Metadata read `2026-07-28` | `CURRENT / PARTIAL` |
| `alanua/bauclock` | `main` | public | 563 | BauClock product source | Metadata read `2026-07-28` | `CURRENT / PARTIAL` |
| `alanua/DIOS` | `main` | public | 172 | DIOS domain product source | Latest inspected commit `a00d67c` on `2026-07-06T20:06:48Z` | `CURRENT / PARTIAL` |
| `alanua/Lavalamp` | `main` | public | 82 | Lavalamp product source | Metadata read `2026-07-28` | `CURRENT / PARTIAL` |
| `alanua/LumenFlow` | `main` | public | 15 | LumenFlow product source | Metadata read `2026-07-28` | `CURRENT / PARTIAL` |
| Other accessible `alanua/*` repositories | mixed | public where listed | mixed | Reference or historical evidence only | Connector page read `2026-07-28` | `NEEDS_OPERATOR / PARTIAL` |

## Known Private or Local Source Categories

These categories are inventory classes only. No private values were copied to this artifact.

| Category | Public-safe source ref | Migration status | Notes |
|---|---|---|---|
| Private chat exports and recovered operator decisions | `https://github.com/alanua/Skeleton/issues/1752#issuecomment-4951167981` | `NEEDS_OPERATOR` | Extract only through approved private route |
| Operator preferences and identity bindings | `docs/PRIVATE_MEMORY_MIGRATION_PACK.md` | `IMPORT_PRIVATE` proposal | Requires MemoryGateway write/readback |
| Personal/family facts | issue `#1752` aggregate category | `NEEDS_OPERATOR` | High privacy; not imported in this phase |
| Financial/legal/business records | issue `#1752` aggregate category | `NEEDS_OPERATOR` | High privacy; not imported in this phase |
| Customer/project documents and measurements | `docs/AUFMASS_PRIVATE_WORKSPACE_CONTRACT.md` | `NEEDS_OPERATOR` | Customer evidence stays private |
| Private locations, routes, travel history and bookings | `projects/travel/PROJECT_MANIFEST.yaml` | `NEEDS_OPERATOR` | Public schemas only in GitHub |
| Private device topology, credentials and endpoints | `docs/HOME_EDGE.md`, `docs/PRIVATE_MEMORY_STACK.md` | `NEEDS_OPERATOR` | Secrets remain secret-reference only |
| Private canonical SQLite records | `docs/MEMORY_GATEWAY.md` | `IMPORT_PRIVATE` proposal | No direct SQLite writes |
| MemPalace derived index | `docs/MEMPALACE_PILOT.md` | `KEEP_AS_EVIDENCE` | Non-authoritative projection |
| Graphify derived index | `docs/GRAPHIFY_MEMORY_ADAPTER.md` | `KEEP_AS_EVIDENCE` | Non-authoritative projection |
| Stale public state snapshots | `projects/*/STATE.yaml` | `SUPERSEDE` or `KEEP_AS_EVIDENCE` | Do not execute as current instructions |
| Archive/history recovery material | `SOURCE_REGISTRY.yaml` | `IGNORE_ARCHIVED` or `KEEP_AS_EVIDENCE` | Use only with explicit audit context |

## Stale or Conflicting Public Canon Files

| File | Classification | Evidence | Required handling |
|---|---|---|---|
| `projects/skeleton/STATE.yaml` | `HISTORICAL / PARTIAL` | `state_role: handoff_not_canon_truth`, `last_verified: 2026-06-19`; current main is `0f49c71` on `2026-07-28` | Keep as evidence; rewrite state in a later authorized canon phase |
| `docs/SKELETON_BUILD_PLAN.md` and `docs/SKELETON_BUILD_PLAN.yaml` | `PARTIAL / NEEDS_OPERATOR` | Older build-plan framing predates later MemoryGateway, Video Understanding and Scheduler work | Reconcile before use as current roadmap |
| `docs/SKELETON_ARCHITECTURE_VNEXT.md` and `docs/SKELETON_ARCHITECTURE_VNEXT.yaml` | `PARTIAL / NEEDS_OPERATOR` | VNext planning can be overtaken by current implementation | Retain as plan evidence, not sole current truth |
| `docs/RUNNER_QUEUE_STATUS.md` | `HISTORICAL / PARTIAL` | Queue state changes continuously and issue `#1752` says execution is controlled by `#1755` | Treat as snapshot only |
| `projects/skeleton/REVIEW_QUEUE.yaml` | `HISTORICAL / PARTIAL` | Contains recovered ideas and backlog material from older sources | Classify individual entries before promotion |
| `docs/SKELETON_DEVELOPMENT_AUDIT_ERRATA_2026-06-30.md` | `CURRENT / CONTRACT_ONLY` | Retracts incomplete prior list-view evidence | Preserve as audit caution |

## Phase 1 Publication Boundary

This worktree phase may publish only public-safe audit artifacts and tests. It did not create a PR, post a GitHub comment, import private values, write SQLite, change protected control files, deploy, merge, or push.

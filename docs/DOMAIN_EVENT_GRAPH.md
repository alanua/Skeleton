# Domain Event Graph

The domain event graph is a public-safe reference layer over the existing private MemoryGateway storage. It does not create a second canonical database or a new scheduler/control plane.

Graph events use `skeleton.domain_event_graph.event.v1` and must declare `privacy_boundary: PUBLIC_SAFE_SCHEMA_ONLY`. Nodes are stable typed refs in the form `domain:kind:local_id`; private mail, document, finance, legal, or business values stay in authority stores and are represented only by refs and evidence hashes.

Edges carry:

- `edge_kind`
- `source` and `target` typed refs
- `confidence`
- `inferred`
- `destructive_capable`
- `provenance_refs`
- deterministic `edge_id`

Uncertain inferred links cannot be marked destructive-capable. The gateway rejects those events before writing graph metadata.

MemoryGateway commands:

- `skeleton.domain_graph.apply_event`
- `skeleton.domain_graph.query_edges`
- `skeleton.domain_graph.dependency_state`
- `skeleton.domain_graph.followup_tasks`

These commands use the existing private gateway SQLite sidecar for idempotency and metadata tables. `apply_event` is idempotent by event idempotency key and payload hash. Reusing a key with different graph content fails closed.

Scheduler and Loop integration is intentionally hook-based. Scheduler payloads may include a `graph_dependency` with `source_ref` and `target_ref`; an injected dependency resolver must return verified graph state before dispatch proceeds. Loop creation can use the same resolver to block runs when a dependency remains unverified.

Synthetic bridge coverage currently exercises:

- Mail to Case to Scheduler
- Mail invoice to Finance and Gewerbe
- GitHub CI mail to Recovery
- Documents intake to Case
- Development goal to Runner continuation

Remaining verified gaps can be surfaced as bounded public-safe follow-up tasks through `domain_graph.followup_tasks`.

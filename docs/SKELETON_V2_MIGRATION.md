# Skeleton v2 Migration Status

Status: PUBLIC_SAFE_STATUS_PACK
Scope: Skeleton v2 migration framing, status, and source-of-truth routing

## Framing

Skeleton is a human-controlled, model-neutral control layer for LLM-assisted
work. It records boot routes, project handoff rules, command modes, write
gates, source trust, and public-safe governance material. It is not a product
runtime, a client data store, a secret store, or a replacement for operator
judgment.

Jeeves is a separate future assistant/runtime. Jeeves is not a Skeleton
adapter, and Skeleton v2 migration work must not move Jeeves runtime,
deployment, secrets, or product implementation into this repository.

Chat memory is not canon. Chat memory may provide working context, but serious
claims, instructions, project state, and source routing must be checked against
durable source files before they are treated as active truth.

Private, client, credential, deployment, and secrets material is not
public-repo material. It belongs in private storage, a secret manager, or a
separately approved private route.

## Active Source Of Truth

The active Skeleton v2 source route starts at `BOOT_MANIFEST.yaml`.

Current public-safe source-of-truth files include:

- `BOOT_MANIFEST.yaml` as the startup entrypoint and read-order declaration.
- Project manifests such as `projects/skeleton/PROJECT_MANIFEST.yaml`.
- Project state files such as `projects/skeleton/STATE.yaml`, treated as
  handoff state and not full canon truth.
- Command and mode files: `COMMANDS.yaml` and `MODES.yaml`.
- Gate files and schemas, including `ACTION_GATE.yaml`, `WRITE_GATE.yaml`, and
  PatchPlan validation files.
- Capability and source registries: `CAPABILITY_REGISTRY.yaml`,
  `HELPER_REGISTRY.yaml`, and `SOURCE_REGISTRY.yaml`.

Canon or instruction changes require critique, a PatchPlan, explicit operator
approval, the approved write, and verification. A chat assertion alone cannot
promote material into canon.

## Migrated

The Skeleton v2 boot route has been migrated into `BOOT_MANIFEST.yaml` and
validated by boot manifest tests.

The Skeleton project route has been migrated into
`projects/skeleton/PROJECT_MANIFEST.yaml` and
`projects/skeleton/STATE.yaml`.

Command, mode, memory routing, source registry, capability registry, helper
registry, project index, write gate, action gate, adapter contract, boot loader,
and project loader materials have been migrated as public-safe Skeleton control
layer material.

## Pending Review

Historical Jeeves and ChatGPT Exoskeleton material remains evidence until it is
classified through review. Pending material must be checked against active
source files, privacy boundaries, and current operator intent before migration.

Future Jeeves bridge work is pending review as separate assistant/runtime
planning. It must remain separate from Skeleton adapters and requires explicit
operator approval before any write outside public-safe Skeleton documentation.

Private source packs, client context, deployment routes, and operational
secrets are pending only for private routing decisions. They are not candidates
for direct public-repo import.

## Rejected

Chat memory as canon is rejected. Chat memory is weak working context and must
not override the boot manifest, project manifests, source registry, or explicit
operator instructions.

Treating Jeeves as a Skeleton adapter is rejected. Jeeves is a separate future
assistant/runtime with its own product and implementation boundary.

Direct public migration of private, client, secrets, credential, deployment, or
runtime-only material is rejected.

Runtime behavior changes through this migration pack are rejected. This pack is
documentation and status metadata only.

## Private-Only Materials

Private-only materials include client records, private project context,
credentials, tokens, deployment details, local runner secrets, private memory,
and any material whose publication would expose a person, client, system, or
credential.

Private-only material must stay out of the public repository unless it has been
sanitized, reviewed, granted explicit operator approval, and verified as
public-safe.

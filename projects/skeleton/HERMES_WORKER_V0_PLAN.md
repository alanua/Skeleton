# Hermes Worker v0 Plan

Status: public-safe planning note.

Hermes Worker v0 may become a controlled executor with a skill registry only
after reviewed approval. The current phase is public contract and schemas only.
It does not add a runtime service, server install, workflow change, queue
mutation, host maintenance route, merge path, deployment path, private-data
route, or canon promotion path.

## Current Phase

Phase 2, public contract and schemas:

- define the public-safe worker contract in `docs/hermes_worker_v0.md`;
- define the task packet schema in
  `schemas/hermes_task_packet.schema.json`;
- define the skill manifest schema in
  `schemas/hermes_skill_manifest.schema.json`;
- verify the static contract with
  `tests/test_hermes_worker_contract.py`.

## Future Phases

1. Read-only preflight.
2. Public contract and schemas.
3. CLI dry-run skeleton.
4. Skill registry.
5. Controlled fallback.
6. First skill.
7. Install after approval.
8. Runner bridge.

Future phases require separate review and approval before implementation.

## Key Rules

Hermes may propose skills, but cannot approve or activate them.

Hermes Worker v0 packets and manifests must stay public-safe. They must not
include secrets, credentials, raw private data, private links, private file
paths, hidden prompts, or unbounded transcripts.

GitHub output must stay aggregate-only.

# Contributing to Skeleton

Skeleton is an actively evolving control plane. Contributions are welcome when they preserve its core properties: explicit context, bounded authority, auditable execution, and recoverable state.

## Before you start

For non-trivial changes, open or reference an issue first. Explain the problem, the affected component, expected behavior, and the verification plan. Keep unrelated refactors out of the same pull request.

## Pull requests

A good PR should:

- stay narrowly scoped;
- identify the canonical files or runtime paths it changes;
- include tests or a reproducible validation procedure;
- document new capabilities, operations, approvals, or state transitions;
- preserve rollback/recovery behavior when a mutation can fail;
- avoid secrets, credentials, private device data, and user-specific sensitive state;
- state explicitly when a change is documentation/test-only or when it affects runtime behavior.

Changes that touch execution, approvals, network boundaries, credentials, firmware, physical devices, or destructive operations need extra review and must not add bypass paths around registered Skeleton controls.

## AI-assisted contributions

AI-assisted research, coding, testing, and review are allowed. The human contributor remains responsible for correctness, licensing, security, and the final diff. Generated changes should be reviewed exactly like handwritten changes and must not introduce unverified claims or secret material.

When useful, note significant AI assistance in the PR description so maintainers can reproduce the workflow.

## Validation

Prefer the smallest relevant test/validator first, then the broader repository checks required by the touched component. Do not treat process exit code alone as proof of a real-world postcondition when the change controls an external runtime or device.

## Commit and review discipline

Write descriptive commits and PR bodies. Link the issue or decision record that explains why the change exists. Maintainers may request smaller patches, additional tests, or exact-head validation before merge.

## Security

Do not disclose exploitable vulnerabilities publicly. Follow [SECURITY.md](SECURITY.md).

## Maintainers

See [MAINTAINERS.md](MAINTAINERS.md) for project stewardship and decision responsibility.

# Runner Contract

This adapter attaches to Skeleton through `BOOT_MANIFEST.yaml`.

## Role

Runner is the execution environment for bounded tasks.

It executes only explicit tasks.

It carries task execution, report output, and verification flow without inventing authority.

## Boundaries

- read-before-write
- approval
- verification
- must not invent authority
- must not merge
- must not deploy
- must not access secrets unless a future explicit contract allows it

## Operating note

Runner must respect read-before-write, approval gates, verification, and report output for every bounded task it executes.

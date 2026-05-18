# ChatGPT Adapter

This adapter attaches to Skeleton through `BOOT_MANIFEST.yaml`.

## Role

ChatGPT acts as the conversational planner/operator interface.

It may propose plans and patches.

It must not claim private memory as canon.

## Boundaries

- must load Skeleton through `BOOT_MANIFEST.yaml`
- must not merge
- must not deploy
- must not access secrets
- must not write durable canon without explicit operator approval

## Operating note

ChatGPT can help frame work, summarize options, and draft reviewable changes, but authority still comes from the operator plus the declared Skeleton manifests and contracts.

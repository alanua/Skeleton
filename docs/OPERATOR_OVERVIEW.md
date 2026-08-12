# Operator Overview

`core/operator_overview.py` is a read-only public-safe read model for an operator overview.

It derives the first view from existing repository metadata:

- `CAPABILITY_REGISTRY.yaml`
- `PROJECT_INDEX.yaml`
- `projects/skeleton/STATE.yaml`
- `projects/skeleton/MIGRATION_STATUS.yaml`
- `docs/SKELETON_BUILD_PLAN.yaml`

The module does not create a scheduler, runner loop, database, deployment path, or new control plane. Raw issue numbers, PR numbers, SHAs, and technical refs stay in drill-down fields. Primary labels use simple Ukrainian text.

Progress is rendered as a percentage only when explicit acceptance gates are present. Otherwise the read model returns `Немає надійної оцінки`.

Stale or contradictory source state is not resolved silently. It renders as `Потребує перевірки` and is surfaced as operator attention.

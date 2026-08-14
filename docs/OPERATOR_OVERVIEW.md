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

The Home `СК` destination consumes the derived read-only live-state projection:

- authority: `core.operator_overview.load_operator_overview`
- projection: `core.operator_live_state.load_operator_live_state`
- Home Edge channel: `GET /api/operator/live-state`

The endpoint is protected by the existing Home Edge trusted-device gate and does not mutate Scheduler, Runner, GitHub, device state, or any queue. Android renders `Оновлено щойно` only when this endpoint returns a successful `freshness: current` response. Stale, degraded, unavailable, or failed refreshes remain visible as stale/offline instead of showing replacement rows.

The Android client does not hardcode hostnames, IP addresses, tokens, or private endpoint identifiers. If the production host does not provide the live-state URL, `СК` fails closed with an unavailable state and preserves the non-mutating shell.

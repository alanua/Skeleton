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

## Live Operator Dashboard

`core/operator_live_state.py` is the canonical public-safe live read boundary for the current operator dashboard. It reads a public-safe control-plane Runner queue snapshot as actual queue truth, with existing Scheduler occurrences used only as supplementary per-item context. It does not create a second Runner, Scheduler, queue, database, registry, or state store.

The primary sections are intentionally simple Ukrainian:

- `Працює зараз`
- `Чекає`
- `Потрібна моя увага`
- `Щойно завершено`
- `Далі`

Running, ready, dependency-waiting, blocked, and recently completed items come from the canonical GitHub/control-plane Runner queue labels. Scheduler occurrence state may enrich matching drill-down context but never creates primary queue items on its own. Terminal items do not appear in active sections. Technical issue, occurrence, and schedule IDs are available only when drill-down is explicitly requested; primary labels suppress issue, PR, SHA, Runner, and GitHub-looking references.

`refreshed_at` is stamped only after a successful live read. If the queue snapshot is missing, stale, partial, or malformed, the Home Edge API returns stale/offline state rather than presenting Scheduler-derived or cached content as fresh.

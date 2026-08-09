# Media Translation Monitor Replacement Summary

## Current Main Inventory

- Canonical Scheduler source is `core/scheduler_models.py`, `core/scheduler_store.py`, `core/scheduler_engine.py`, with CLI/runtime entry points in `scripts/scheduler_tick.py` and `scripts/scheduler_runtime.py`.
- Canonical Scheduler dispatch routing is `core/shared_dispatch.py`; unknown routes fail closed through `ROUTE_NOT_ALLOWLISTED`.
- Existing notification delivery is the Telegram sender in `scripts/runner_poll_github_tasks.py::send_telegram_notification`; this replacement calls that sender lazily through the media alert adapter and does not add another Telegram subsystem.
- Existing Home Edge repository source includes executor/static adaptive remote files under `core/home_edge/` and `scripts/home_edge_*`. Current main has no repository-owned `/video` page, Video tab route, or deployed Video control-row source.
- Current main has no TMDB-bound media identity/metadata adapter and no repository-owned media history/watch-list authority. This implementation therefore accepts an existing upstream `work_id` plus TMDB ID instead of inventing a second TMDB matcher.
- Current main has no shared HTTP provider adapter primitive beyond direct stdlib usage in existing scripts, so public metadata adapters are bounded stdlib fetchers that fail closed and can be injected in tests.

## Replacement Scope

- Added `core/media_translation_monitor.py` as a reusable backend monitor that uses existing `SchedulerStore`, `ScheduleSpec`, `SchedulerEngine`, and `SharedDispatcher` route conventions.
- Added `core/media_translation_providers.py` with public-safe provider adapters for Kinobaza-style aggregate pages, official streaming metadata pages, and OpenSubtitles-compatible subtitle observation. These adapters only read public metadata, bound response size, and fail closed.
- Added `SharedDispatcher.for_media_translation_monitor(...)` for executable Scheduler occurrences on route `workflow/media_translation_monitor`.
- Did not add a fake Video tab or new API endpoint because current main does not own the canonical running Video source/deploy contract.
- Did not add a standalone media SQLite database. Monitor state is a bounded JSON ledger for subscriptions, release state, watch-list dedupe, and alert receipts until the canonical Home media authority is repository-owned.

## Follow-Up

Canonicalize the already-running Home media source/deploy contract in this repository: identify the actual Video tab source path, server route/API surface, TMDB identity provider, private watch-history authority, and to-watch authority, then replace the JSON ledger and activate the monitor toggle in the existing Video control row between Play/Reset and Autoplay.

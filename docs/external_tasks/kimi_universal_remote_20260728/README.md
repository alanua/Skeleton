# Kimi Task Packet — Universal Remote / Gamepad

This directory is a self-contained external-analysis packet for the unresolved Skeleton Home Edge universal remote problem.

## User-visible symptom

The phone still shows the old, clumsy remote instead of one coherent universal remote with the expected multimedia controls, touchpad, keyboard, and a separate usable universal gamepad. The repository implementation exists and a deployment issue was closed, but the actual user-visible result is still wrong or incomplete.

Do not treat the closed issue as proof that the live problem is solved.

## Start here

1. [TASK.md](./TASK.md)
2. [CURRENT_STATE.md](./CURRENT_STATE.md)
3. [ACCEPTANCE.md](./ACCEPTANCE.md)
4. [KIMI_PROMPT.md](./KIMI_PROMPT.md)

## Authoritative GitHub context

- Repository: https://github.com/alanua/Skeleton
- Canonical design issue: https://github.com/alanua/Skeleton/issues/1959
- Merged reference implementation: https://github.com/alanua/Skeleton/pull/1960
- Deployment issue that may have been closed prematurely or later regressed: https://github.com/alanua/Skeleton/issues/1966
- Reference contract: https://github.com/alanua/Skeleton/blob/main/core/home_edge/adaptive_remote.py
- Reference UI: https://github.com/alanua/Skeleton/blob/main/core/home_edge/static/adaptive_remote.html
- Contract documentation: https://github.com/alanua/Skeleton/blob/main/docs/HOME_EDGE_ADAPTIVE_REMOTE.md
- Contract tests: https://github.com/alanua/Skeleton/blob/main/tests/test_home_edge_adaptive_remote.py
- Skeleton boot manifest: https://github.com/alanua/Skeleton/blob/main/BOOT_MANIFEST.yaml

## Important boundary

Kimi does not have to access the private Home Edge node. The required output is a precise diagnosis, a repository patch where possible, and a bounded live-probe/deployment plan that Skeleton can execute later through the canonical `Skeleton_Home_Edge.home_edge_exec` route. Never propose direct SSH, a second executor, arbitrary key injection, or bypassing Skeleton approvals and audit.

# CURRENT STATE AND EVIDENCE

## Confirmed repository facts

1. Issue #1959 defined a generic adaptive multimedia remote and one universal gamepad.
2. PR #1960 merged commit `c0e837c60488f9f6efa815e177f4282aae264558` and added:
   - `core/home_edge/adaptive_remote.py`;
   - `core/home_edge/static/adaptive_remote.html`;
   - `docs/HOME_EDGE_ADAPTIVE_REMOTE.md`;
   - `tests/test_home_edge_adaptive_remote.py`.
3. The merged documentation explicitly says the module is an offline reference contract and does not connect to a live TV, Android runtime, browser, player, game, SSH route, or MCP service.
4. Issue #1966 correctly identified the original integration defect: the new `adaptive_remote.html` existed, while the actually served Home web app still used its old `remote.html`.
5. Issue #1966 is closed as completed, but the current user-visible result is still unacceptable: the old/clumsy remote remains visible or the intended universal remote/gamepad is not functioning as one integrated product.

## Key conclusion

The work cannot be considered complete merely because:

- PR #1960 merged;
- offline tests passed;
- issue #1966 was closed;
- a service returned HTTP 200;
- a command was sent or accepted.

The actual `/remote` response and its interaction with the bounded APIs must be verified. Closure of #1966 may represent a one-time runtime replacement, an incomplete closure, or a later regression/runtime drift.

## Evidence links

- #1959: https://github.com/alanua/Skeleton/issues/1959
- #1960: https://github.com/alanua/Skeleton/pull/1960
- #1966: https://github.com/alanua/Skeleton/issues/1966
- UI asset: https://github.com/alanua/Skeleton/blob/main/core/home_edge/static/adaptive_remote.html
- Python contract: https://github.com/alanua/Skeleton/blob/main/core/home_edge/adaptive_remote.py
- Tests: https://github.com/alanua/Skeleton/blob/main/tests/test_home_edge_adaptive_remote.py
- Documentation: https://github.com/alanua/Skeleton/blob/main/docs/HOME_EDGE_ADAPTIVE_REMOTE.md

## Expected product behavior

### Remote

- Designed for a phone in portrait orientation.
- Looks and behaves like a practical physical multimedia remote, not a developer test page.
- Exactly three primary tabs: `Пульт`, `Тачпад`, `Клава`.
- Supports power, mute, refresh, circular D-pad, OK, back, home/menu when supported, transport, and volume.
- Uses the currently active Home mode to select behavior without restarting Android during Android-family app transitions.

### Gamepad

- Separate landscape view.
- One universal controller for all games.
- D-pad, A/B/X/Y, L/R, Start, Select, Library, Android TV, Safe Exit.
- Multi-touch holds work correctly.
- Leaving gamepad does not change TV mode.
- No Arkanoid-specific, title-specific, emulator-specific, or game-specific branch.

## Unknowns Kimi must resolve

- What repository file currently owns `GET /remote`?
- Does current `main` include a durable route integration or only the reference asset?
- What exact HTML file is deployed and served now?
- Is the served page stale because of HTTP cache, application cache, service worker, reverse proxy, or runtime drift?
- Do the UI payloads match the actual backend API schemas?
- Is the universal gamepad reachable from the served UI?
- Was the #1966 mutation durable across service restart/update/deploy?

## Non-negotiable execution architecture

All later physical/runtime changes must execute through `Skeleton_Home_Edge.home_edge_exec` with:

- `node_id=home-edge-01`;
- exact argv or bounded script;
- `run_as`;
- execution lane;
- timeout;
- request ID;
- idempotency key;
- operator approval reference where required;
- independent postcondition verification;
- Skeleton audit receipt.

Kimi's job is to make the diagnosis and patch self-contained. Skeleton will perform any approved live action.

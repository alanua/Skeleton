# TASK — Diagnose and finish the Skeleton universal phone remote

## Mode

External code and architecture investigation. Produce repository-ready artifacts. Do not perform unapproved live actions.

## Repository

`alanua/Skeleton`

Base: current `main`.

## Objective

Determine why the user still receives the old or incomplete Home Edge `/remote` interface even though the adaptive remote reference implementation was merged in PR #1960 and deployment issue #1966 was closed. Prepare the minimal correct repository patch and a bounded runtime verification/deployment procedure.

The final product must be one coherent phone control experience:

- portrait multimedia remote;
- exactly three main tabs: `Пульт`, `Тачпад`, `Клава`;
- a separate universal landscape gamepad;
- no title-specific or game-specific controllers;
- no implicit TV-mode change when leaving the gamepad.

## Problem statement

There are at least three potentially divergent layers:

1. repository reference asset: `core/home_edge/static/adaptive_remote.html`;
2. repository/runtime route that serves HTTP `GET /remote`;
3. the actual deployed Home Edge file/service/cache chain.

PR #1960 proves only that a reference asset and offline contract exist. Issue #1966 describes a live replacement, but its closed state is not sufficient evidence that the live route currently serves the correct file. The user-visible symptom is stronger evidence: the old clumsy remote is still shown or the universal gamepad is not correctly integrated.

## Required investigation

### 1. Trace the repository route

Find every repository location involved in:

- HTTP `GET /remote`;
- `remote.html` and `adaptive_remote.html`;
- static-file resolution;
- service-worker or asset-version caching;
- `/api/remote/status`;
- `/api/remote/control`;
- `/api/remote/pointer`;
- `/api/remote/keyboard`;
- `/api/volume`;
- gamepad button dispatch and allowlists.

Produce an explicit source-to-runtime path graph. Do not stop at the reference asset.

### 2. Compare contracts

Compare the HTML UI button/action payloads with the actual backend request schemas and allowlists. Identify every mismatch in:

- endpoint path;
- JSON field names;
- action/button names;
- `down`, `up`, `tap` phases;
- pointer IDs and multitouch;
- keyboard payloads;
- volume payloads;
- status response fields;
- orientation handling;
- gamepad exit behavior.

### 3. Explain the stale UI

Rank and prove or falsify these candidate root causes:

- adaptive asset exists but `/remote` still points to old `remote.html`;
- runtime file was replaced outside the repository and later overwritten;
- deployment issue closed after a partial or non-persistent mutation;
- cache headers or a service worker continue to serve an obsolete asset;
- the UI is updated but links/buttons still open an old route;
- different services or ports expose different `/remote` implementations;
- gamepad exists only as an unreachable or hidden secondary view;
- current main no longer contains the integration that was deployed.

### 4. Produce a minimal repository patch

Where the repository contains the serving route, make the repository itself authoritative. The patch must:

- make the real `/remote` route serve the adaptive UI;
- avoid copying a second divergent HTML implementation;
- preserve exactly `Пульт`, `Тачпад`, `Клава`;
- expose the universal gamepad through a clear reachable control;
- keep remote and gamepad separate;
- keep the closed 15-button gamepad allowlist;
- release held buttons on `pointercancel`, blur, visibility loss, and an 8-second upper bound;
- preserve current TV mode when exiting the gamepad;
- use only existing bounded backend handlers;
- add targeted cache invalidation/versioning without globally disabling caching;
- include regression tests that fail if `/remote` serves the old asset or if UI/backend action names diverge.

If the serving route is not present in the repository, do not invent it. Instead create a bounded probe that identifies the exact runtime source and returns a machine-readable result for Skeleton.

### 5. Provide a bounded live probe

Add a repository script or documented exact command plan that Skeleton can execute through `Skeleton_Home_Edge.home_edge_exec` on `node_id=home-edge-01`.

The probe must be read-only by default and return redacted JSON containing:

- active service/unit identity;
- route source identity without private host/address disclosure;
- served `/remote` SHA-256;
- repository adaptive asset SHA-256;
- page markers for the three tabs and universal gamepad;
- cache headers and service-worker/version evidence;
- API endpoint availability;
- current TV mode before and after the harmless verification;
- classification: `repo_route_mismatch`, `runtime_drift`, `stale_cache`, `api_contract_mismatch`, `correct`, or `unknown`.

No arbitrary command, path, host, shell fragment, or key parameter may be accepted from callers.

## Validation

At minimum:

- Python compile/static checks;
- JavaScript syntax check;
- existing `tests/test_home_edge_adaptive_remote.py`;
- new route-integration tests;
- exact three-tab assertion;
- exact 15-button gamepad allowlist parity;
- test that gamepad exit does not request a TV-mode change;
- test for release on cancel/blur/visibility/timeout;
- test that stale old-remote markers are absent from the served page;
- test that cache/version behavior changes only for the remote asset or route.

## Forbidden

- direct SSH instructions;
- bypassing `Skeleton_Home_Edge.home_edge_exec`;
- a new executor or control daemon;
- arbitrary keyboard/keycode injection;
- arbitrary shell, host, URL, path, or command parameters;
- package installation, reboot, router/modem/firewall/DNS changes;
- secrets, private addresses, usernames, keys, or raw runtime logs in GitHub;
- game-title-specific UI branches;
- unrelated redesigns;
- declaring success from HTTP 200 alone.

## Required output

Return one of `DONE` or `BLOCKED` and provide:

1. concise root-cause report;
2. source-to-runtime route graph;
3. exact changed files and patch/PR link;
4. tests and results;
5. bounded live-probe command contract;
6. deployment preconditions;
7. independent postconditions for `applied` and `physically_verified`;
8. rollback procedure;
9. remaining unknowns, without guessing.

If Kimi can write to GitHub, open a PR against `alanua/Skeleton:main` and link the new issue created for this task. Otherwise return a unified patch plus the complete contents of every new file.

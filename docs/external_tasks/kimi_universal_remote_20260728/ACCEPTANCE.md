# ACCEPTANCE CONTRACT

The task is accepted only when the diagnosis and patch prove the real serving chain rather than only the offline reference implementation.

## A. Repository diagnosis

- [ ] Every repository occurrence of the old and adaptive remote is listed.
- [ ] The owner of `GET /remote` is identified or conclusively shown to be absent from the repository.
- [ ] Static asset resolution and cache/version behavior are traced.
- [ ] UI-to-API payload compatibility is documented endpoint by endpoint.
- [ ] The reason issue #1966 can be closed while the user still sees the old UI is explained with evidence or reduced to a single bounded live check.

## B. Repository patch

- [ ] The real repository route serves one canonical adaptive asset, or a bounded runtime resolver is added if the route is runtime-only.
- [ ] No duplicate copy of the remote UI is introduced.
- [ ] Primary tabs are exactly `Пульт`, `Тачпад`, `Клава`.
- [ ] The universal gamepad is clearly reachable and remains a separate landscape interface.
- [ ] Gamepad allowlist is exactly 15 buttons and matches backend mapping.
- [ ] No title-specific controller exists.
- [ ] Exiting gamepad cannot select or change TV mode.
- [ ] All button holds are safely released on cancellation, blur, visibility loss, and timeout.
- [ ] Cache invalidation is targeted, versioned, and testable.

## C. Security and architecture

- [ ] No direct SSH or alternative executor is added.
- [ ] No arbitrary keycode, shell, host, URL, path, or command input is accepted.
- [ ] The live plan uses only `Skeleton_Home_Edge.home_edge_exec` on `home-edge-01`.
- [ ] Private addresses, usernames, credentials, secrets, and raw logs are excluded from GitHub.
- [ ] Rollback restores the exact previous asset and service state.

## D. Tests

- [ ] Existing adaptive-remote tests pass.
- [ ] JavaScript syntax passes.
- [ ] New route integration test verifies that `/remote` resolves to the adaptive asset.
- [ ] Regression test rejects old-page markers/hash where deterministic.
- [ ] UI/backend action allowlists are identical.
- [ ] Three-tab assertion passes.
- [ ] 15-button gamepad assertion passes.
- [ ] Safe release tests pass.
- [ ] Gamepad exit mode-preservation test passes.
- [ ] Cache/version test passes.

## E. Live verification contract

The future Skeleton execution must record separate states:

1. `sent` — request dispatched;
2. `accepted` — executor/service accepted it;
3. `applied` — served `/remote` hash and markers changed as intended;
4. `physically_verified` — user-visible phone route shows the expected controls and a harmless bounded input produces the expected application state without changing TV mode.

HTTP 200, service active, successful restart, or command exit code 0 is insufficient by itself.

## F. Kimi result handoff

Kimi must provide:

- [ ] `RESULT: DONE` or `RESULT: BLOCKED`;
- [ ] root cause;
- [ ] patch or PR URL;
- [ ] exact files changed;
- [ ] test commands and outputs;
- [ ] bounded probe specification and sample redacted JSON;
- [ ] deployment/rollback sequence;
- [ ] unresolved facts and next safe action.

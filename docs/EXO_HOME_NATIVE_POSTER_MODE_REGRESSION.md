# Native Home poster mode regression — issue #4913

Base SHA: `a315292ecb276a2454f29139e7cac945abc4e8a3`.
Prior poster-only implementation inspected:
`5f7800b57084be9a3de3e2ec3682f645436e3ba1` (the locally available
#4820/#4824 combined history/poster change). No local ref or commit message
identifies #4827 directly; equivalence to its final reviewed head is unverified.
The working changes are uncommitted for the parent Runner publication contract.

## Behavior and scope

`produceState(initial, key)` restarts its coroutine but retains its state value.
The state is now scoped with Compose `key(full URL + decode dimensions)`.
A → B → A initializes each request from the existing RAM cache synchronously;
a miss or failed load cannot expose another request's bitmap. A disk hit avoids
HTTP; different content URLs, endpoint origins and decode dimensions are isolated.
No TTL or mode-switch invalidation is introduced. Same-URL content replacement
still requires the server to change the URL, as before.

The existing 48 MiB RAM LRU and URL disk directory remain the only caches.
Fixed stripes replace removable locks; RAM is checked and populated under the
loader lock. The disk cache evicts oldest-accessed images above 128 MiB or 256
files, bounds each encoded response to 16 MiB, and replaces corrupt disk images.
Only successfully decoded responses are written. Connections are disconnected
on success and failure. File deletion failures are best effort.

YouTube poster selection checks candidate video IDs against the current response
identity, rather than remembered player metadata from a previous composition.
An unknown or mismatched candidate cannot authorize its poster. The current
video's standard thumbnail is used when no verified candidate exists. A revisit
with a matching candidate retains its original URL and synchronous RAM hit.

Only poster loader helpers and YouTube poster selection change in MainActivity.
History (#4911), portrait Fit/layout (#4912), mode actions, player refresh,
ambient visuals and controls are untouched. Integrate these hunks independently;
do not replace MainActivity wholesale when combining the three issues.

## Regression evidence and blockers

- Focused Python poster source contracts: **4 passed**. They guard request state
  isolation, cache-hit initialization, endpoint/content/size keys, disk hit/miss,
  corrupt images, failure handling, bounds and current-player identity checks.
- Android JUnit tests added for A → B → A request identity, endpoint/content/size
  changes, missing/mismatched poster metadata, and disk byte/count eviction.
  **Not executed**: Java, Gradle and kotlinc are unavailable in this sandbox.
- The approved Skeleton Home Edge executor requires
  `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET`, which is absent. No registered build
  workspace was supplied. Android compilation and unit execution are blocked;
  no alternate build environment was created or used.
- History Python contracts: **2 passed, 2 failed**. Both failures also reproduce
  against the untouched base SHA (history actionability and partial-failure
  loader expectations); these belong to the independent history work.
- `git diff --check`: passed.

Python source contracts do not execute Compose, HTTP or Android decoding. Runner
must run `:app:testDebugUnitTest --tests com.skeleton.home.NativePosterCacheTest`
and `:app:compileDebugKotlin` against this exact edited source through the existing
registered Home Edge environment. Executable Compose coverage for rapid switches,
HTTP failure and disk-only misses remains unverified. No assertion of observed
flicker-free runtime playback is made.

All fixtures are synthetic. No publication, APK, signing, OTA, runtime sync,
playback or device switching occurred. Roll back only the poster hunks and their
three companion test/document files, preserving independent history/Fit changes.

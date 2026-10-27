# Native Home poster mode regression

## Root cause and scope

Compose `produceState(initial, key)` restarts its producer when the key changes,
but remembers the old state value. The ambient poster's A → B → A sequence could
therefore render the previous request before the producer runs, even with a RAM
hit. Scope the state to the full URL and decode-size key. Cache hits now initialize
the state synchronously; misses show an empty state rather than another title.

The existing URL disk cache and 48 MiB decoded RAM LRU remain the only caches.
Recheck RAM under the loader lock and insert successful decodes before releasing
it, avoiding duplicate decoding by simultaneous consumers. Fixed lock stripes
avoid the old remove-while-waiting lock race and bound lock memory. Decode sizes
remain distinct because ambient and card rendering have different requirements.
HTTP failures do not insert null into RAM; an existing disk image avoids HTTP.

YouTube fallback now requires a known matching canonical video ID when the remote
video ID is present. An unknown canonical ID cannot authorize its old poster.
PersistentHomeRemotes and PersistentTabSlot remain intact. Player refresh behavior
is unchanged; this patch does not retain poster fields across incomplete player
responses, whose content identity needs separate verification.

## Evidence and limits

Only the assigned issue workspace was edited; no other issue worktree was used.
The local sources already contain persistent remote panels. No verified 1.4.38
production/golden source was available for comparison, so production equivalence
is not asserted. No network, playback, or device operations were performed.

Local validation:

- `python3 -m pytest -q tests/android/test_home_poster_cache_contract.py`: 5 passed.
- `git diff --check`: passed.
- Kotlin JUnit request identity tests added in NativePosterCacheTest.kt, covering
  mode round trip, endpoint/content change, and decode size isolation.
- Android tests/build could not run: Java, Gradle, and kotlinc are unavailable.
  Python tests inspect source contracts; they do not execute Compose or HTTP.

Runner must run Android unit tests and the Android Home Test Build on the final
reviewed head. Runtime scenarios (cache miss, HTTP failure, and absence of a
cross-title frame) still need executable Android coverage. GitHub Actions could
not be invoked under the no-network/no-gh publication contract. Review overlap
with #4820 before integrating; this workspace does not contain its retained work.

## Changed files and rollback

- android/home/app/src/main/java/com/skeleton/home/MainActivity.kt
- android/home/app/src/test/java/com/skeleton/home/NativePosterCacheTest.kt
- tests/android/test_home_poster_cache_contract.py
- docs/EXO_HOME_NATIVE_POSTER_MODE_REGRESSION.md

Rollback only these patch hunks after integration, preserving any subsequent
#4820 edits. No cache migration, configuration change, deployment, or device
operation is required. This deliverable is source-only and has not been published.

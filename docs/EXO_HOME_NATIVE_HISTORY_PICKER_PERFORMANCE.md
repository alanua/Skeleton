# Native history picker cached opening

A tap computes `NativeHistoryCache.open` synchronously and opens populated cached
items before scheduling refresh. The field stays enabled during refresh. A shared
catalog is held once per server/screen lifetime; only watched rows and freshness
are keyed by endpoint. The 60-second freshness interval applies to explicit user-triggered refreshes; automatic endpoint hydration is once per cache lifetime.

Initial missing history schedules one automatic refresh through an endpoint/server-keyed
Compose effect. Later stale history refreshes only on an explicit tap. Refresh has a 15-second coroutine timeout and at most two catalog
attempts, with the existing 650 ms retry delay. Catalog results become visible
before the optional watched-progress read. Blocking HTTP calls still obey the
transport's socket timeouts; coroutine cancellation prevents their results from
being published after cancellation. Cancellation is rethrown by catalog retries.
There is no perpetual retry loop. Initial errors allow another tap to retry;
a successful empty catalog remains fresh until the normal expiry. An initial tap
remembers the open request and displays populated catalog results immediately when
they arrive, before waiting for watched progress. This request resets on an endpoint
or server switch; hidden-only results do not open a populated picker, including
when the hidden-only catalog is already cached.

Generation tokens reject obsolete completions and prevent duplicate inflight
requests. Endpoint switches cancel the old effect. Catalog failures retain the
last valid membership. Successful reads replace membership, including empty
results; watched rows cannot restore deleted entries or override hidden metadata.
Season, episode, source/job and watch-position fields come from the selected
device's watched rows, never from another endpoint's catalog snapshot. Selected
job loading and playback actions are unchanged by picker refresh.

The dialog retains its LazyColumn and row-local asynchronous, size-limited poster
loading. Opening does not eagerly fetch or decode the catalog's poster images.

## Regression validation

`NativeVideoHistoryTest` covers synchronous hydrated opening, fresh/stale decisions,
single inflight refresh, transient failure retention and retry, initial empty/error
states, cancellation/endpoint switches, shared deletion/hiding, posters, and
separate device season/episode progress. Existing tests cover authoritative
membership and selected-job/source identity.

Validation in the issue worktree:

- `python3 -m pytest -q tests/android/test_home_history_contract.py`: 6 passed.
- `git diff --check`: passed.
- `gradle -p android/home :app:testDebugUnitTest :app:compileDebugKotlin`: could not
  execute: **NOT_RUN_ENV_MISSING** (`gradle: command not found`). No Gradle wrapper is present under
  `android/home`; `java` and `kotlinc` are also absent from PATH. Kotlin compilation
  and unit tests remain unverified and require an Android build environment.

No APK installation, playback, deployment, or production mutation was performed.

## Follow-up: prevent repeated automatic reload on mode revisits

The 60-second TTL remains for *explicit* open/tap refreshes. The screen
claims at most one automatic hydration for each endpoint per `NativeHistoryCache`
lifetime. Switching away and back does not automatically refetch a populated
list after TTL; an error does not create an automatic retry loop. A new user tap
may request a bounded refresh, and the existing list stays interactive.
`HistoryPickerField` receives a spinner flag only when loading **and** the
shared catalog has never been hydrated; background progress refresh cannot mask
previously loaded catalog entries.

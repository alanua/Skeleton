# Provider-independent TV monitor integration

Status: repository adapter and Android capability consumer only. **Not deployed.**
The actual backend is `/home/oleksii/.local/lib/skeleton-cast/media_release_monitor.py`
and is not source-controlled here. Its functions/schema cannot be inspected in this
workspace. The steps below specify the exact integration contract and required
host-side changes, rather than inventing function names or claiming runtime support.
No host files, state, playback, scheduler, network configuration, or secrets were changed.

## Identity and trust boundary

`core/home_edge/provider_release_monitor.py` is a standard-library-only domain
module. Import it in the existing Home Edge process; do not create an executor,
database, polling service, or scheduler. The only current anchor is the exact URL
`https://premier.one/show/reportazh-2035`, with immutable identity
`provider:premier:tv:reportazh-2035`. This is an explicit identity registration,
not a claim about current episodes, availability, or release dates. Production
acceptance must independently review that canonical identity before enabling it.
No title matching, redirects, search hits, similar hostnames, query URLs, URL
credentials, alternative ports, or unregistered slugs qualify. Registry expansion
requires code review and independently verified immutable URLs.

The domain functions accept trusted server verification results. They are not an
HTTP request parser: never map client `identity_verified`, `announcement_verified`,
`verified_source_ids`, state, or `media_uid` directly into them. Resolve a stored
server job's canonical URL through `resolve_series`, and confirm it is TV.
Sources must be full playable episodes already checked by the existing resolver,
bound to **that exact canonical title, season and episode**. A provider catalogue
count, future premiere, trailer, empty source array, or another title's file is not
such evidence. Do not convert an announced episode into a synthetic source ID.
If evidence is unavailable or inconsistent, leave the episode unverified.

`assess_episode` parses an exact ISO calendar date. Identity plus announcement
verification and a date yields `announced`. Only a checked date no later than the
caller's explicitly supplied current date plus nonempty verified source IDs yields
`verified`. All other evidence yields `unverified`; only verified episodes expose
source IDs. Choose the provider's calendar timezone consistently when deriving
`today`; do not turn a date-only announcement into a fabricated timestamp.

## Required host patch (separate reviewed deployment)

1. Back up and inspect the existing host file above in the authorized deployment
   workflow. Locate its GET/PUT `/api/video/monitor` handlers, existing TMDB TV
   eligibility check, monitor persistence, existing polling callback, resolver
   verification boundary, and history identity lookup. No edits to that runtime
   have been performed by this task.
2. Install this module alongside the existing host module as
   `/home/oleksii/.local/lib/skeleton-cast/provider_release_monitor.py`, using the
   host's existing release packaging, then import its domain functions from
   `media_release_monitor.py`. It requires Python 3.11+. There are no dependencies
   to install or background processes to launch.
3. Dispatch using `monitor_domain(media_type, tmdb_id, canonical_url)` on trusted
   job metadata. `tmdb` must invoke the original TMDB branch unchanged: retain its
   identifiers, storage, schedules, release checks, and progress rules. `provider`
   invokes this adapter; `unsupported` must not create a subscription. Never
   fabricate a positive/negative TMDB ID, insert a provider title in a TMDB row,
   or remap an existing TMDB subscription.
4. Extend the existing monitor store with a versioned provider namespace. Its
   subscription unique key is `(media_uid, device_id)`, and its episode progress
   unique key is `(media_uid, device_id, episode_key)`. Episode keys come only from
   `episode_key(series, season, episode)`. Start provider subscriptions disabled;
   do not infer opt-in from autoplay, history, a selected title, or a GET. Existing
   TMDB rows need no migration. Persist updates atomically in the existing store.
5. On GET, authorize `device_id` against the authenticated user's existing device
   registry, resolve `job_id` server-side and return a capability envelope (below).
   Provider enabled state is loaded only for this exact device and media identity.
   On PUT require a JSON boolean `enabled`; enabling is the explicit user opt-in
   and calls `set_enabled(..., explicit_opt_in=True, current=...)`. Disabling calls
   it with `False` and preserves progress. Reject a missing/unauthorized device,
   stale job/identity or nonboolean value without changing any row. Do not accept
   an arbitrary client media UID or provider URL as authority. Existing TMDB
   mutations continue through the original branch.
6. In the existing monitor polling callback only, reverify the canonical identity
   and each candidate's episode binding, announcement date and playable sources.
   Call `assess_episode`, then `pending` with the device subscription. Use the
   existing downstream delivery mechanism; acknowledge via `acknowledge` only
   after success, with a transactional compare/update or the existing idempotent
   outbox. Failed deliveries remain retryable. Serialize concurrent deliveries
   by the full device/episode key; independent devices must not consume each
   other's progress. A poll alone must never opt in or mark playback progress.
7. Use `history_key(series)` as the canonical lookup identity for provider titles.
   Deduplicate repeated discoveries of the same URL against that identity in the
   existing history identity layer; never append one title per job, episode, poll,
   or device. Keep per-device playback/progress in existing separate records.
   The monitor must not add/remove history membership or change player controls.
8. Add host integration tests for the existing handlers/store/resolver with the
   fixtures below. Stage the reviewed patch through normal release procedures
   only after those tests and an Android build pass. This document does not
   authorize deployment, restart, OTA, state migration execution, or playback.

## HTTP contract consumed by native Android

GET `/api/video/monitor?job_id=<encoded>&device_id=<encoded>` is read-only.
PUT `/api/video/monitor` takes
`{"job_id":"synthetic-job","device_id":"synthetic-device","enabled":true}`.
Both success responses must include explicit booleans:

```json
{"supported":true,"enabled":false,"reason":""}
```

A valid legacy TMDB TV item also returns `supported:true` around the existing
TMDB result. An unsupported identity returns
`{"supported":false,"enabled":false,"reason":"unsupported_identity"}` on GET;
PUT must reject it without persistence (409 with an actionable `error` string).
Use 409 for stale/conflicting identity, rather than silently returning disabled.
Other server failures remain non-2xx errors with `{"error":"actual reason"}`.
A timeout is a transport failure and must not be cached as unsupported or disabled.
`failure_kind` supplies a pure classification for host logs/tests; it performs no
HTTP handling and cannot itself guarantee status codes or delivery.

Android requires a valid TV series plus explicit `supported:true`. Missing support
fails closed, including against an older server. GET failures display the actual
exception/server error and keep the toggle disabled until a later successful
lookup. PUT failures restore the previous value and display the error, permitting
retry. Responses for a superseded job, server or endpoint are ignored. Deploy the
backend capability contract before releasing this client to preserve radar access
for existing TMDB users. No runtime TMDB behavior has been altered here.

## Validation and release gates

Repository validation:

```sh
python3 -m pytest -q tests/test_home_provider_release_monitor.py tests/android/test_home_monitor_capability_contract.py
git diff --check
```

The Python suite covers the exact URL, host/userinfo/port/path spoofing, absent
identity, date validation, absent/empty sources, announced versus playable states,
explicit opt-in/off, device separation, canonical history key reuse, dispatch to
the unchanged TMDB branch, and distinct conflict/timeout classification. Android
source contracts cover eligibility, errors, device identity and stale responses.
These are not a test of the unavailable host implementation or a Kotlin compile.

Host acceptance must additionally exercise the real HTTP handlers: GET must not
write state; PUT false must stop delivery; repeated PUT true must preserve progress;
409 must preserve state and show its error; a transport timeout must preserve state
and remain distinguishable from 409; concurrent polls must deliver once per
canonical episode/device; wrong-title/trailer sources must fail verification;
provider polls must leave existing TMDB records and history membership untouched.
Run the original TMDB regression suite before and after the host patch and compare
results. Verify canonical-title deduplication against the actual history store.

Android compile/test: **NOT_RUN_ENV_MISSING** in this task environment (no Java,
Gradle executable or wrapper). Require an independent supported-JDK Android
`:app:assembleDebug` and `:app:testDebugUnitTest` build before merge/release, plus
UI checks for supported non-TMDB TV, supported TMDB TV, unsupported TV/movie,
missing capability, 409, timeout and switching device/title during requests.

Rollback: disable the provider branch/capability in the reviewed backend release;
retain namespaced progress for recovery. Keep legacy TMDB handling unchanged.
Older servers without the capability flag intentionally leave the new client
radar disabled. No destructive data migration is required or supplied.

Draft PR: Refresh canonical Home History after editor return

Root cause and evidence

VideoScreen lives in PersistentTabSlot and keeps endpoint lists in remember state.
Its original hydration effect was keyed only by server and endpoint. The editor
fetched /api/video/history independently; its dismiss callback only closed the
dialog. Returning to the persistent tab therefore did not refresh its snapshot.
Opening the normal picker already performs a read, but the catalog GET had no
explicit HTTP cache policy. Actual HTTP cache hits have not been observed here.
The normal picker correctly excludes hidden catalog items; however, the watch
metadata merge could supply hidden=true when the catalog omitted the flag.

Change

Revalidate only GET /api/video/history using useCaches=false and Cache-Control:
no-cache, no-store. Refresh on active tab entry and editor dismissal via an
explicit revision. One effect invocation performs one load (with the existing
bounded two-attempt catalog retry); history/list/loading state is not an effect
key. Manual picker reads remain available after failures. Preserve the last list
on catalog failure. Endpoint watch rows cannot provide visibility flags.

/api/video/history remains the sole membership authority. /api/media/history
remains endpoint-scoped progress enrichment only. Catalog order and existing
merge rules remain intact; poster memory/disk caching is untouched. No new
catalog, endpoint, background poll, or sorting rule is introduced.

Regression coverage and validation

- NativeVideoHistoryTest adds a synthetic stale-98/fresh-100 fixture: remove two
  old works and add four visible works. Both endpoint modes replace membership
  with the fresh ordered 100, include all four additions, and retain watch
  episode/position fields. A second test covers canonical hidden filtering and
  verifies watch objects are not mutated.
- Python source contracts cover editor-dismiss revision wiring, tab activation,
  bounded refresh lifecycle, catalog-only HTTP cache bypass, hidden authority,
  and the existing loading/error/retry behavior. Updated obsolete History
  assertions to match the existing canonical loader and picker behavior.
- python3 -m pytest -q tests/android/test_home_history_contract.py: 6 passed.
- python3 -m pytest -q tests/test_home_android_media.py -k 'not release_metadata and not video_selection_and_history': 10 passed, 2 deselected.
- The unfiltered combined run initially had 3 failures: one obsolete picker
  assertion now updated, and two existing media contract failures excluded above.
  Those assert version 155/1.4.39 (HEAD is 160/1.4.44) and the obsolete editor/
  endpoint-conditional history loader (already absent at HEAD).
- git diff --check: passed.
- JVM regressions and a real Compose editor-return interaction were not executed:
  this environment has no Java, Kotlin, Gradle executable, or Gradle wrapper.
  Run gradle :app:testDebugUnitTest from android/home in an Android development
  environment before publication. No APK was built. The 98/100 fixture is
  synthetic evidence, not a claim about live database counts.

Manual acceptance before publication

Load normal History, then add four visible works externally in a test catalog
while removing two older works (98 -> 100). Open Edit History, confirm the new
works, close via back/outside/close, and reopen normal History. Confirm all four
appear, hidden works stay absent, catalog ordering/posters remain stable, and
switching endpoints preserves separate progress. Repeat offline: retain prior
items, show the failure, and allow a manual retry without repeated refreshes.

Rollback

Revert the MainActivity.kt changes and associated regression/doc additions.
No schema/data migration or server rollback is needed. No live media database,
service, deployment, restart, APK build, Git index, commit, or PR was changed.
This is draft PR text for the parent Runner; no PR was created.

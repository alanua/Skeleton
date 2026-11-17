# Monitor request isolation / R18

The existing actual1.4.58 candidate had a TMDB-only Android toggle and unguarded asynchronous monitor GET/PUT results. This patch uses server-declared series capability (legacy positive-TMDB fallback only if the capability is absent), current job/server/endpoint identity and request generations. A read started before a write cannot undo that write; cancellation propagates. Unsupported, timeout and errors remain distinct.

Apply patch_monitor.py to a separate actual Android source copy; then copy the new HomeMonitorState.kt and test from this PR. Exact source hashes are embedded. Never replace the current source with old public main.

Independent validation: offline compileDebugKotlin + testDebugUnitTest succeeded in144seconds, one worker,300-second cap.80 JVM tests passed (71 existing+9 monitor), zero failures/errors/skips. Full source digest before/after build: d355dba3858c87a90108db9f8cefdb3fc83239a7a111b9a5683e7e1599611332.19 focused synthetic Python tests also pass independently on DE-PC and Edge. No signing, installer or runtime actions.

R18 remains partial: backend reports PROVIDER_EPISODE_PRODUCER_MISSING for non-TMDB series; no official provider episode producer was supplied and no fabricated snapshot contract was retained. Existing TMDB monitoring remains covered. Real UI/receiver acceptance and the full#4928 ledger remain open. Signed private174 is unchanged and unpublished.
